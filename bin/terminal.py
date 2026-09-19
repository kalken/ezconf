#!/usr/bin/env python3
# Bumped to verify the terminal-restart notification picks up a real content change.
"""
ezconf terminal service — standalone WebSocket PTY server.

Run:
  python3 terminal.py --config /run/ezconf/ezconf.toml
  python3 terminal.py --port 9092 --session-key-file /run/ezconf/session.key

Config keys read from TOML: terminal_port, session_key_file, shell, cert, key, webroot,
terminal_persist

terminal_persist = true (default false) makes the shell session survive a dropped/closed
WebSocket connection -- see _SESSION, _create_session(), _session_reader() below. There is only
ever one session, shared by every connection regardless of who or what browser it comes from --
see _SESSION's own comment for why. Reconnecting reattaches to the same still-running shell,
repainting a snapshot of its current screen content (see _VirtualScreen below) rather than
replaying its raw output history, instead of forking a fresh one; the shell itself only ever dies when
it exits on its own, or when this whole process does (a reboot, a manual restart of
ezconf-terminal.service) -- nothing here can make it survive that. With terminal_persist left at
its default, a client disconnecting kills the shell immediately (see
TERM_PERSIST/_terminal_ws()'s own finally below) -- exactly the original, pre-persistence
behavior, except still shared across simultaneous connections rather than one shell each.
"""
import argparse
import base64
import codecs
import hashlib
import http.server
import json
import os
import secrets
import select
import ssl
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

try:
    import pty
    import termios
    import fcntl
    _PTY = True
except ImportError:
    _PTY = False

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

def _ws_accept_key(client_key):
    digest = hashlib.sha1((client_key + _WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()

def _ws_recv(rfile):
    header = rfile.read(2)
    if len(header) < 2:
        raise ConnectionError('connection closed')
    b0, b1 = header[0], header[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack('>H', rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack('>Q', rfile.read(8))[0]
    mask = rfile.read(4) if masked else b''
    payload = bytearray(rfile.read(length))
    if masked:
        for i in range(len(payload)):
            payload[i] ^= mask[i % 4]
    return opcode, bytes(payload)

def _ws_send(wfile, data, opcode=0x02):
    length = len(data)
    if length < 126:
        header = bytes([0x80 | opcode, length])
    elif length < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack('>H', length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack('>Q', length)
    wfile.write(header + (data if isinstance(data, (bytes, bytearray)) else data.encode()))
    wfile.flush()


def load_toml(path):
    if tomllib is None:
        return {}
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f'warning: could not read {path}: {e}', file=sys.stderr)
        return {}


SHELL        = '/bin/sh'
SESSION_KEY  = ''
PORT         = 9091
WEBROOT      = '.'
BIND_ADDR    = '127.0.0.1'

READY_DELAY  = 0.3  # see the 'ready' comment in _terminal_ws() below

# A content hash of this running process's own source file, computed once at import time --
# included in every 'ready' message so the frontend can tell whether the ezconf-terminal.service
# it's actually connected to is stale relative to what's currently on disk (server.py computes the
# same hash fresh at its own startup, since ezconf.service restarts on every rebuild and
# ezconf-terminal.service deliberately doesn't -- see restartIfChanged in the NixOS module). Same
# truncated-sha256 convention as WEBROOT_HASH in server.py.
try:
    with open(__file__, 'rb') as _f:
        SELF_HASH = hashlib.sha256(_f.read()).hexdigest()[:16]
except OSError:
    SELF_HASH = ''

# Off by default -- set by terminal_persist in TOML. When False, a session behaves exactly as
# before persistence existed: _terminal_ws()'s own finally (a client disconnecting) nudges the
# shell to exit immediately rather than leaving it running unattached, so nothing outlives the one
# connection that created it. See that finally block for how this reuses _session_reader()'s
# normal "the shell exited" teardown path rather than needing a separate one.
TERM_PERSIST = False

# _SESSION holds the one shell that's still running, or None -- independent of any particular
# WebSocket connection, which is the whole point: it outlives a client detaching (browser closed,
# network drop, logout) and is only ever cleared by _session_reader() once the shell itself
# actually exits. There's only ever this one session, shared by every connection regardless of who
# or what browser it comes from -- there's no per-browser identity anywhere else in ezconf either
# (one shared login for everyone with access, see check_auth() in server.py), so a per-browser
# terminal would be the only thing in the whole app pretending otherwise. It's also simpler, and
# has no failure mode a per-browser id (tried first, then reverted) did: clearing cookies/site
# data (which usually wipes localStorage too, where a per-browser id would live) would silently
# orphan that browser's still-running shell forever, unreachable by anything, with no cleanup
# mechanism -- there's no id here to lose in the first place. The trade-off is explicit: anyone
# who can log in shares this exact one terminal, always, not just multiple tabs of one browser.
# _SESSION_LOCK guards setting/clearing/checking _SESSION itself; the session also has its own
# 'lock' guarding that session's mutable state (vscreen/writers/rows/cols) -- two separate locks so
# the session's I/O never blocks a connect/disconnect deciding whether to (re)create it.
#
# One thing this file can't prevent at the source: a shell prompt framework that queries the
# terminal at startup (background colour via OSC 11, device attributes via DA1, DECRQM mode
# queries, ...) can end up with xterm.js's automatic answer landing as literal typed input sitting
# unsubmitted in the shell's own line editor, if the shell wasn't still synchronously listening for
# it by the time the answer got back over the WebSocket round-trip. That's real, live state in the
# shell's own input buffer, not anything this file holds -- _VirtualScreen's snapshot reproduces it
# faithfully (correctly, since it's genuinely what's on screen), but the *live* shell can still
# redraw it a second time on top of a correct snapshot if something forces a SIGWINCH afterward --
# see the resize handling's own comment for why that's avoided for a plain shell prompt.
_SESSION = None
_SESSION_LOCK = threading.Lock()

def _set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    except Exception:
        pass


# _VirtualScreen -- a small, dependency-free terminal emulator that tracks just enough screen
# state (a grid of cells, each with its own colour/attributes, plus cursor position) to paint an
# exact, safe snapshot for a reattaching client, in place of replaying the session's raw output
# history. Earlier versions tried exactly that (a bounded ring of recent bytes, replayed
# outright), and every attempt to make raw replay safe kept running into the same root problem: a
# byte stream that was only ever meant to be interpreted once, live, isn't safe to interpret a
# second time out of context -- it corrupts a full-screen curses app's cursor/screen tracking
# (replaying a differential-update stream with no full paint underneath), and separately, any
# terminal-capability query in it (OSC 10/11 colour, DA1, DECRQM, ...) gets answered a second time
# by a fresh xterm.js instance that can't tell replay from live traffic, landing back in the shell
# as visible garbage. Feeding that same stream through this class instead and sending only the
# *result* -- the resolved screen content, not the escape sequences that produced it -- sidesteps
# both: a full repaint of the current grid needs no prior state to make sense of, and a query never
# reaches the reattaching client's xterm.js at all, since it's parsed and discarded here rather
# than reproduced.
#
# Deliberately not a complete VT100/xterm implementation -- just the escape sequences real shells
# and full-screen apps actually rely on for screen *content* (cursor movement, erase, scrolling,
# SGR colour/attributes, the alternate screen buffer). Anything else (title changes, charset
# switching, ...) is safely ignored rather than mis-rendered, and an unrecognized sequence is
# skipped rather than risking getting the parser permanently out of sync with the stream. Query/
# status sequences (DA1/DA2 `c`, DSR `n`, DECRQM `$p`) are recognized and dropped with no effect --
# correct, since there's no live client attached to answer them at parse time anyway, and it would
# be wrong to answer them even if there were.
_SGR_FIELDS = ('bold', 'dim', 'italic', 'underline', 'blink', 'reverse', 'strike', 'fg', 'bg')


def _default_sgr():
    return {'bold': False, 'dim': False, 'italic': False, 'underline': False,
            'blink': False, 'reverse': False, 'strike': False, 'fg': None, 'bg': None}


def _sgr_tuple(sgr):
    return tuple(sgr[k] for k in _SGR_FIELDS)


_BLANK_SGR = _sgr_tuple(_default_sgr())


def _sgr_params_for(sgr_tuple):
    bold, dim, italic, underline, blink, reverse, strike, fg, bg = sgr_tuple
    parts = []
    if bold: parts.append('1')
    if dim: parts.append('2')
    if italic: parts.append('3')
    if underline: parts.append('4')
    if blink: parts.append('5')
    if reverse: parts.append('7')
    if strike: parts.append('9')
    if fg: parts.append(fg)
    if bg: parts.append(bg)
    return ';'.join(parts) if parts else '0'


class _VirtualScreen:
    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.cur_row = 0
        self.cur_col = 0
        self.sgr = _default_sgr()
        self.top_margin = 0
        self.bottom_margin = rows - 1
        self._saved = None
        # Incremental UTF-8 decoding (rather than decoding each feed() chunk on its own) is what
        # makes a multi-byte character split exactly across two PTY reads come out correct instead
        # of a replacement char followed by garbage -- the same reason _pending exists below, one
        # layer up, for an escape sequence split the same way.
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._pending = ''
        self._grid = self._blank_grid()

    def _blank_row(self):
        return [(' ', _BLANK_SGR) for _ in range(self.cols)]

    def _blank_grid(self):
        return [self._blank_row() for _ in range(self.rows)]

    def reset(self):
        self._grid = self._blank_grid()
        self.cur_row = 0
        self.cur_col = 0
        self.sgr = _default_sgr()
        self.top_margin = 0
        self.bottom_margin = self.rows - 1

    def resize(self, rows, cols):
        new_grid = [[(' ', _BLANK_SGR) for _ in range(cols)] for _ in range(rows)]
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                new_grid[r][c] = self._grid[r][c]
        self._grid = new_grid
        self.rows, self.cols = rows, cols
        self.cur_row = min(self.cur_row, rows - 1)
        self.cur_col = min(self.cur_col, cols - 1)
        self.top_margin, self.bottom_margin = 0, rows - 1

    def _scroll_up(self, n=1):
        top, bot = self.top_margin, self.bottom_margin
        for _ in range(n):
            del self._grid[top]
            self._grid.insert(bot, self._blank_row())

    def _scroll_down(self, n=1):
        top, bot = self.top_margin, self.bottom_margin
        for _ in range(n):
            del self._grid[bot]
            self._grid.insert(top, self._blank_row())

    def _index(self):
        if self.cur_row == self.bottom_margin:
            self._scroll_up()
        elif self.cur_row < self.rows - 1:
            self.cur_row += 1

    def _reverse_index(self):
        if self.cur_row == self.top_margin:
            self._scroll_down()
        elif self.cur_row > 0:
            self.cur_row -= 1

    def _putc(self, ch):
        if self.cur_col >= self.cols:
            self.cur_col = 0
            self._index()
        self._grid[self.cur_row][self.cur_col] = (ch, _sgr_tuple(self.sgr))
        self.cur_col += 1

    def _erase_cell(self, row, col):
        self._grid[row][col] = (' ', _sgr_tuple(self.sgr))

    def _erase_in_line(self, mode):
        if mode == 0:
            cols = range(self.cur_col, self.cols)
        elif mode == 1:
            cols = range(0, self.cur_col + 1)
        else:
            cols = range(0, self.cols)
        for c in cols:
            self._erase_cell(self.cur_row, c)

    def _erase_in_display(self, mode):
        if mode == 0:
            self._erase_in_line(0)
            rows = range(self.cur_row + 1, self.rows)
        elif mode == 1:
            self._erase_in_line(1)
            rows = range(0, self.cur_row)
        else:
            rows = range(0, self.rows)
        for r in rows:
            for c in range(self.cols):
                self._erase_cell(r, c)

    def _apply_sgr(self, params):
        params = params or [0]
        i, n = 0, len(params)
        while i < n:
            p = params[i]
            if p == 0: self.sgr = _default_sgr()
            elif p == 1: self.sgr['bold'] = True
            elif p == 2: self.sgr['dim'] = True
            elif p == 3: self.sgr['italic'] = True
            elif p == 4: self.sgr['underline'] = True
            elif p in (5, 6): self.sgr['blink'] = True
            elif p == 7: self.sgr['reverse'] = True
            elif p == 9: self.sgr['strike'] = True
            elif p == 22: self.sgr['bold'] = False; self.sgr['dim'] = False
            elif p == 23: self.sgr['italic'] = False
            elif p == 24: self.sgr['underline'] = False
            elif p == 25: self.sgr['blink'] = False
            elif p == 27: self.sgr['reverse'] = False
            elif p == 29: self.sgr['strike'] = False
            elif 30 <= p <= 37: self.sgr['fg'] = str(p)
            elif p == 38:
                if i + 2 < n and params[i + 1] == 5:
                    self.sgr['fg'] = f'38;5;{params[i + 2]}'; i += 2
                elif i + 4 < n and params[i + 1] == 2:
                    self.sgr['fg'] = f'38;2;{params[i+2]};{params[i+3]};{params[i+4]}'; i += 4
            elif p == 39: self.sgr['fg'] = None
            elif 40 <= p <= 47: self.sgr['bg'] = str(p)
            elif p == 48:
                if i + 2 < n and params[i + 1] == 5:
                    self.sgr['bg'] = f'48;5;{params[i + 2]}'; i += 2
                elif i + 4 < n and params[i + 1] == 2:
                    self.sgr['bg'] = f'48;2;{params[i+2]};{params[i+3]};{params[i+4]}'; i += 4
            elif p == 49: self.sgr['bg'] = None
            elif 90 <= p <= 97: self.sgr['fg'] = str(p)
            elif 100 <= p <= 107: self.sgr['bg'] = str(p)
            i += 1

    def _csi(self, params_str, final):
        private = params_str.startswith('?')
        body = params_str[1:] if private else params_str
        if body.endswith('$'):
            return  # DECRQM/DECRPM ($p/$y) -- swallow, see the class docstring above
        try:
            params = [int(p) if p else 0 for p in body.split(';')] if body else []
        except ValueError:
            return
        if private:
            if final in ('h', 'l') and any(p in (47, 1047, 1049) for p in params):
                # Entering/leaving the alternate screen buffer. A real terminal starts that buffer
                # blank, and switching back to the primary one should never show alt-screen
                # leftovers bleeding through cells the app never touched -- resetting on both
                # transitions is simpler than keeping two separate grids and is correct as long as
                # snapshots are only ever taken of "the screen as it looks right now", never a
                # diff against what used to be there.
                self.reset()
            return
        if final == 'm':
            self._apply_sgr(params)
        elif final in ('H', 'f'):
            row = (params[0] if params else 1) or 1
            col = (params[1] if len(params) > 1 else 1) or 1
            self.cur_row = min(max(row - 1, 0), self.rows - 1)
            self.cur_col = min(max(col - 1, 0), self.cols - 1)
        elif final == 'A':
            self.cur_row = max(self.cur_row - ((params[0] or 1) if params else 1), self.top_margin)
        elif final == 'B':
            self.cur_row = min(self.cur_row + ((params[0] or 1) if params else 1), self.bottom_margin)
        elif final == 'C':
            self.cur_col = min(self.cur_col + ((params[0] or 1) if params else 1), self.cols - 1)
        elif final == 'D':
            self.cur_col = max(self.cur_col - ((params[0] or 1) if params else 1), 0)
        elif final == 'G':
            self.cur_col = min(max((params[0] if params else 1) - 1, 0), self.cols - 1)
        elif final == 'd':
            self.cur_row = min(max((params[0] if params else 1) - 1, 0), self.rows - 1)
        elif final == 'E':
            self.cur_row = min(self.cur_row + ((params[0] or 1) if params else 1), self.rows - 1)
            self.cur_col = 0
        elif final == 'F':
            self.cur_row = max(self.cur_row - ((params[0] or 1) if params else 1), 0)
            self.cur_col = 0
        elif final == 'J':
            self._erase_in_display(params[0] if params else 0)
        elif final == 'K':
            self._erase_in_line(params[0] if params else 0)
        elif final == 'L':
            n = (params[0] or 1) if params else 1
            for _ in range(n):
                del self._grid[self.bottom_margin]
                self._grid.insert(self.cur_row, self._blank_row())
        elif final == 'M':
            n = (params[0] or 1) if params else 1
            for _ in range(n):
                del self._grid[self.cur_row]
                self._grid.insert(self.bottom_margin, self._blank_row())
        elif final == 'P':
            n = (params[0] or 1) if params else 1
            row = self._grid[self.cur_row]
            del row[self.cur_col:self.cur_col + n]
            row.extend([(' ', _sgr_tuple(self.sgr))] * n)
        elif final == '@':
            n = (params[0] or 1) if params else 1
            row = self._grid[self.cur_row]
            row[self.cur_col:self.cur_col] = [(' ', _sgr_tuple(self.sgr))] * n
            del row[self.cols:]
        elif final == 'X':
            n = (params[0] or 1) if params else 1
            for c in range(self.cur_col, min(self.cur_col + n, self.cols)):
                self._erase_cell(self.cur_row, c)
        elif final == 'S':
            self._scroll_up((params[0] or 1) if params else 1)
        elif final == 'T':
            self._scroll_down((params[0] or 1) if params else 1)
        elif final == 'r':
            if params and len(params) >= 2:
                self.top_margin = min(max(params[0] - 1, 0), self.rows - 1)
                self.bottom_margin = min(max(params[1] - 1, 0), self.rows - 1)
            else:
                self.top_margin, self.bottom_margin = 0, self.rows - 1
            self.cur_row, self.cur_col = 0, 0
        # 'c' (DA1/DA2) and 'n' (DSR): swallowed, see the class docstring above.

    def feed(self, data: bytes):
        text = self._pending + self._decoder.decode(data)
        self._pending = ''
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == '\x1b':
                rest = text[i + 1:]
                if rest == '':
                    self._pending = ch
                    break
                nxt = rest[0]
                if nxt == '[':
                    end = self._find_csi_end(rest[1:])
                    if end is None:
                        self._pending = text[i:]
                        break
                    self._csi(rest[1:1 + end], rest[1 + end])
                    i += 2 + end + 1
                    continue
                if nxt == ']':
                    end = self._find_osc_end(rest[1:])
                    if end is None:
                        self._pending = text[i:]
                        break
                    i += 2 + end
                    continue
                if nxt in ('(', ')', '*', '+'):
                    if len(rest) < 2:
                        self._pending = text[i:]
                        break
                    i += 3  # charset designator -- swallowed, see the class docstring above
                    continue
                if nxt == '7':
                    self._saved = (self.cur_row, self.cur_col, dict(self.sgr))
                    i += 2
                    continue
                if nxt == '8':
                    if self._saved:
                        self.cur_row, self.cur_col, self.sgr = (
                            self._saved[0], self._saved[1], dict(self._saved[2]))
                    i += 2
                    continue
                if nxt == 'c':
                    self.reset()
                    i += 2
                    continue
                if nxt == 'D':
                    self._index()
                    i += 2
                    continue
                if nxt == 'M':
                    self._reverse_index()
                    i += 2
                    continue
                # Any other single-byte ESC sequence -- consume just the ESC + one byte rather
                # than risk getting stuck or printing either literally.
                i += 2
                continue
            if ch == '\r':
                self.cur_col = 0
            elif ch == '\n':
                self._index()
            elif ch == '\b':
                self.cur_col = max(self.cur_col - 1, 0)
            elif ch == '\t':
                self.cur_col = min(((self.cur_col // 8) + 1) * 8, self.cols - 1)
            elif ch not in ('\x07', '\x00'):
                self._putc(ch)
            i += 1

    def _find_csi_end(self, s):
        for idx, c in enumerate(s):
            if '\x40' <= c <= '\x7e':
                return idx
        return None

    def _find_osc_end(self, s):
        bel = s.find('\x07')
        st = s.find('\x1b\\')
        if bel == -1 and st == -1:
            return None
        if bel == -1:
            return st + 2
        if st == -1:
            return bel + 1
        return min(bel + 1, st + 2)

    def snapshot(self):
        """Render the current grid + cursor position as a self-contained ANSI byte string safe to
        send to a fresh xterm.js instance as a full repaint -- plain text, SGR and cursor
        positioning only, never a query/response sequence of any kind (see the class docstring).
        Rows are joined with a real \\r\\n rather than addressed one by one with an absolute cursor
        move -- deliberately, even though every row's column position is already known: a
        reattaching client's own terminal can still be sized however it was last left (or mid a
        CSS-transition-driven resize that hasn't settled yet -- see connectTerminalWs()'s own
        comment on a similar timing race, index.html), possibly much shorter than this snapshot's
        row count. A real newline lets a too-short terminal do what a real terminal always does
        when it runs out of room -- scroll, keeping every row recoverable from its own scrollback --
        where an absolute move to a row beyond its current height would instead clamp, silently
        overwriting whatever an earlier row had already written to that same clamped position."""
        last_row = self.cur_row
        for r, row in enumerate(self._grid):
            if any(ch != ' ' for ch, _ in row):
                last_row = max(last_row, r)
        out = []
        prev_sgr = _BLANK_SGR
        for r, row in enumerate(self._grid[:last_row + 1]):
            if r > 0:
                out.append('\r\n')
            line = []
            for ch, sgr in row:
                if sgr != prev_sgr:
                    line.append('\x1b[0m' if sgr == _BLANK_SGR else f'\x1b[0;{_sgr_params_for(sgr)}m')
                    prev_sgr = sgr
                line.append(ch)
            out.append(''.join(line).rstrip())
        out.append(f'\x1b[0m\x1b[{self.cur_row + 1};{self.cur_col + 1}H')
        return ''.join(out).encode('utf-8')


# Substrings that mean "a full-screen program just switched into/out of the alternate screen
# buffer" -- smcup/rmcup in terminfo terms, what ncurses (htop, vim, less, top, ...) wraps its
# whole screen in. Checked as a plain substring rather than parsed properly: a false negative here
# (the sequence split exactly across two PTY reads, or a program not using the alt screen at all)
# just means the resize handling below falls back to treating it as a plain shell -- the same as
# today, not a regression -- so a cheap check is an acceptable trade rather than a real parser.
# Deliberately NOT termios ECHO/ICANON state, which was tried first and doesn't work: an
# interactive readline-based shell (bash, zsh -- most real shells) *also* turns ICANON/ECHO off to
# do its own line editing, indistinguishable that way from a genuine full-screen app.
_ALT_SCREEN_ENTER = (b'\x1b[?1049h', b'\x1b[?47h', b'\x1b[?1047h')
_ALT_SCREEN_EXIT = (b'\x1b[?1049l', b'\x1b[?47l', b'\x1b[?1047l')


def _update_alt_screen_state(session, data):
    for seq in _ALT_SCREEN_ENTER:
        if seq in data:
            session['alt_screen'] = True
    for seq in _ALT_SCREEN_EXIT:
        if seq in data:
            session['alt_screen'] = False


def _create_session(rows, cols):
    """Fork a fresh shell and start its dedicated reader thread, returning the new session dict
    (or None if the PTY/shell itself couldn't be created). From here on, _session_reader() --
    not any particular WebSocket connection -- owns this shell's entire lifecycle: it keeps
    running for as long as the shell does, regardless of whether a client is currently attached."""
    try:
        master_fd, slave_fd = pty.openpty()
    except Exception as e:
        print(f'[terminal] pty.openpty() failed: {e}', file=sys.stderr)
        return None
    _set_winsize(slave_fd, rows, cols)
    env = {'TERM': 'xterm-256color'}

    def _init_child():
        os.setsid()
        try:
            fcntl.ioctl(0, getattr(termios, 'TIOCSCTTY', 0x540E), 0)
        except Exception:
            pass

    try:
        proc = subprocess.Popen(
            [SHELL, '-l'],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True,
            preexec_fn=_init_child,
            cwd=os.path.expanduser('~'),
            env=env,
        )
    except Exception as e:
        print(f'[terminal] shell launch failed: {e}', file=sys.stderr)
        os.close(slave_fd)
        os.close(master_fd)
        return None
    os.close(slave_fd)

    session = {
        'proc': proc,
        'master_fd': master_fd,
        'rows': rows,
        'cols': cols,
        'vscreen': _VirtualScreen(rows, cols),
        'writers': set(),
        'lock': threading.Lock(),
        'done': False,
        'alt_screen': False,
    }
    threading.Thread(target=_session_reader, args=(session,), daemon=True).start()
    return session


def _session_reader(session):
    """The one thread that ever reads this session's PTY, for its entire lifetime -- started once
    by _create_session() and never restarted. Keeps draining the PTY out live to whichever clients
    are currently attached, regardless of whether anyone's attached at all, so the shell is never
    left blocked writing into a full pipe with nothing draining it. Only exits -- and only then
    tears the shell down -- once the shell process itself actually exits or the PTY read fails
    outright; a client disconnecting never reaches this at all (see _terminal_ws()'s own finally
    below, which only ever removes that one client from 'writers')."""
    global _SESSION
    master_fd = session['master_fd']
    proc = session['proc']
    try:
        while True:
            if proc.poll() is not None:
                break
            try:
                r, _, _ = select.select([master_fd], [], [], 0.5)
            except OSError:
                break
            if not r:
                continue
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            with session['lock']:
                # alt_screen still tracked independently of _VirtualScreen's own (separate) alt-
                # screen handling -- this copy only ever feeds the resize-wiggle decision below,
                # not snapshot correctness, which _VirtualScreen owns entirely on its own.
                _update_alt_screen_state(session, data)
                session['vscreen'].feed(data)
                writers = list(session['writers'])
            for wfile in writers:
                try:
                    _ws_send(wfile, data, opcode=0x02)
                except Exception:
                    pass  # that client's own connection loop will notice and clean itself up
    finally:
        # The shell is genuinely gone (or as good as) -- tell every currently attached client
        # explicitly, via a control message, rather than leaving them to infer it from the
        # connection merely closing (which also happens on an ordinary drop, where the shell is
        # still very much alive). See connectTerminalWs()'s _termExited in index.html, which is
        # the only thing that reacts to this.
        with session['lock']:
            session['done'] = True
            writers = list(session['writers'])
            session['writers'].clear()
        for wfile in writers:
            try:
                _ws_send(wfile, json.dumps({'type': 'exited'}).encode(), opcode=0x01)
                _ws_send(wfile, b'', 0x08)
            except Exception:
                pass
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except Exception:
            pass
        with _SESSION_LOCK:
            if _SESSION is session:
                _SESSION = None


def _terminal_ws(handler):
    global _SESSION
    if not _PTY:
        handler.send_error(501, 'PTY not available on this platform')
        return

    client_key = handler.headers.get('Sec-WebSocket-Key', '').strip()
    accept = _ws_accept_key(client_key)

    sock = handler.connection
    sock.sendall((
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    ).encode('latin-1'))

    handler.close_connection = True
    rfile = handler.rfile
    wfile = sock.makefile('wb', buffering=0)

    with _SESSION_LOCK:
        session = _SESSION
        is_new = session is None or session.get('done')
        if is_new:
            session = _create_session(rows=24, cols=80)
            if session is None:
                handler.send_error(500)
                return
            _SESSION = session

    with session['lock']:
        session['writers'].add(wfile)

    if not is_new:
        # Reattaching to a shell that's been running unattended. Clear this client's screen, then
        # paint _VirtualScreen's current snapshot onto it -- a full repaint of the screen exactly
        # as it looks right now, safe regardless of whether a plain shell or a full-screen app
        # produced it (see the class's own docstring for why this replaced raw-history replay).
        # The alt_screen-gated resize wiggle below still runs on top of this for a full-screen app,
        # as an extra correctness net -- this snapshot is a best-effort emulation, not a substitute
        # for the app redrawing itself with its own, authoritative knowledge of its state.
        with session['lock']:
            snapshot = session['vscreen'].snapshot()
        try:
            _ws_send(wfile, b'\x1b[2J\x1b[H', opcode=0x02)
            _ws_send(wfile, snapshot, opcode=0x02)
        except Exception:
            pass

    # A text frame is always a control message from here on (real terminal output is always sent
    # binary, see _session_reader() above) -- 'ready' tells the client it's now safe to send
    # input, rather than the client guessing readiness from the WebSocket's own open state or the
    # first byte of output, either of which can race ahead of the shell actually being ready to
    # receive it. Delayed rather than sent the instant a *brand new* shell is forked: a fresh PTY
    # starts in canonical/echo mode by default, so input sent before the shell has actually
    # finished its own startup (sourcing profile/rc files) and taken over the terminal gets echoed
    # back raw by the kernel immediately, then redrawn a second time once the shell's own line
    # editor takes over and finds it already queued -- visible as a duplicated line (seen in
    # practice on the first button press right after a reboot, when cold disk/page caches make
    # profile scripts slow enough to actually hit this). This narrows the window rather than
    # closing it -- a shell still mid-startup after READY_DELAY would still hit it -- deliberately
    # not guessed from output patterns instead (unreliable: a shell that's silently slow, e.g.
    # profile scripts with nothing to print while cold, looks identical to one that's already idle
    # and settled). _session_reader() is already running above so any real startup output the
    # shell does produce during this wait still streams live instead of arriving all at once
    # afterward. A *reused* session is long past its own startup by now, so there's nothing to
    # wait out -- sending 'ready' immediately is both correct and lets a reconnect feel instant.
    if is_new:
        time.sleep(READY_DELAY)
    try:
        _ws_send(wfile, json.dumps({'type': 'ready', 'hash': SELF_HASH}).encode(), opcode=0x01)
    except Exception:
        pass

    try:
        while True:
            opcode, payload = _ws_recv(rfile)
            if opcode == 0x08:
                break
            if opcode in (0x01, 0x02):
                if opcode == 0x01 and payload.startswith(b'{'):
                    try:
                        msg = json.loads(payload)
                        if msg.get('type') == 'resize':
                            rows, cols = int(msg['rows']), int(msg['cols'])
                            with session['lock']:
                                same = (session['rows'] == rows and session['cols'] == cols)
                                session['rows'], session['cols'] = rows, cols
                                session['vscreen'].resize(rows, cols)
                                master_fd = session['master_fd']
                                alt_screen = session['alt_screen']
                            if same and alt_screen:
                                # A same-value TIOCSWINSZ is a no-op at the kernel level -- no
                                # SIGWINCH at all -- which is routinely what a reattach sends (the
                                # browser resizing to whatever size it already was, e.g. right after
                                # a plain page reload). A full-screen app (currently in the alternate
                                # screen buffer -- see _update_alt_screen_state() above) needs a
                                # genuine SIGWINCH to repaint fully on reattach (its own last paint is
                                # stale/incomplete), so wiggle through an off-by-one size with a real
                                # pause before landing back on the real one -- confirmed (manually
                                # dragging the panel) that a real, sustained size change is what
                                # actually makes this work, and that the pause matters too: an app
                                # whose signal handler doesn't run until after both changes have
                                # already landed can observe only the final, unchanged size and
                                # correctly (from its own perspective) decide there's nothing to
                                # redraw. Deliberately NOT done outside the alternate screen (a plain
                                # shell prompt): forcing a SIGWINCH there has a real cost with no
                                # benefit -- an interactive shell's own line editor (bash/zsh
                                # readline, or the kernel's own canonical-mode echo for one that
                                # doesn't use it) redraws the current input line on SIGWINCH, which
                                # can contain leftover bytes from an earlier terminal-capability
                                # query/response the shell never got to consume (see _SESSION's own
                                # comment on why that's not something this file can safely prevent at
                                # the source) -- so forcing it twice made that visible, twice, on
                                # every single reattach.
                                _set_winsize(master_fd, rows - 1 if rows > 1 else rows + 1, cols)
                                time.sleep(0.15)
                            _set_winsize(master_fd, rows, cols)
                            continue
                    except Exception:
                        pass
                try:
                    os.write(session['master_fd'], payload)
                except OSError:
                    pass
    except Exception as e:
        print(f'[terminal] ws loop error: {e}', file=sys.stderr)
    finally:
        # Only ever detaches this one client -- the session (and the shell inside it) keeps
        # running regardless, which is the entire point of this design. _session_reader() is the
        # only thing that ever actually tears a session down, and only once the shell itself has
        # genuinely exited.
        with session['lock']:
            session['writers'].discard(wfile)
            remaining = len(session['writers'])
        if not TERM_PERSIST and remaining == 0:
            # Non-persistent mode: nothing should outlive the one connection that created it.
            # Clear _SESSION immediately, synchronously, right here -- not left for
            # _session_reader() to notice and do asynchronously on its own next select() cycle --
            # so a connection arriving a moment later (e.g. a fast page reload) can never win a
            # race against the old shell's own teardown and get attached to a session that's
            # mid-death. That thread still does the actual OS-level work (terminate/kill if
            # needed, close the fd) once it notices proc.poll() is no longer None; its own "clear
            # _SESSION" is a no-op by then (already gone), same as any other double-clear guard.
            with _SESSION_LOCK:
                if _SESSION is session:
                    _SESSION = None
            try:
                proc = session['proc']
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass


def _session_from_cookie(headers):
    for part in headers.get('Cookie', '').split(';'):
        k, _, v = part.strip().partition('=')
        if k.strip() == 'ezconf_session':
            return v.strip()
    return ''


class TerminalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if (parsed.path == '/terminal' and
                self.headers.get('Upgrade', '').lower() == 'websocket'):
            if _session_from_cookie(self.headers) == SESSION_KEY:
                _terminal_ws(self)
            else:
                self.send_response(401)
                self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print(f'[terminal] {self.address_string()} - {fmt % args}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='ezconf terminal WebSocket service')
    ap.add_argument('--config', metavar='FILE', default=None,
                    help='TOML config file (default: ezconf.toml)')
    ap.add_argument('--port', type=int, default=None,
                    help='port to listen on (default: terminal_port from TOML, or 9092)')
    ap.add_argument('--session-key-file', metavar='FILE', default=None,
                    help='file to load/store the session key (must match server.py)')
    ap.add_argument('--cert', metavar='FILE', default=None, help='TLS certificate (PEM)')
    ap.add_argument('--key',  metavar='FILE', default=None, help='TLS private key (PEM)')
    args = ap.parse_args()

    cfg = load_toml(args.config or 'ezconf.toml')

    PORT = args.port or cfg.get('terminal_port') or PORT

    _passwd_shell = ''
    try:
        import pwd as _pwd
        _passwd_shell = _pwd.getpwuid(os.getuid()).pw_shell or ''
    except Exception:
        pass
    SHELL = cfg.get('shell') or _passwd_shell or os.environ.get('SHELL') or '/bin/sh'

    TERM_PERSIST = bool(cfg.get('terminal_persist', False))

    WEBROOT   = cfg.get('webroot') or WEBROOT
    BIND_ADDR = cfg.get('listen') or BIND_ADDR

    _key_file = args.session_key_file or cfg.get('session_key_file')
    if _key_file:
        _key_file = os.path.abspath(_key_file)
        if os.path.exists(_key_file):
            SESSION_KEY = open(_key_file).read().strip()
        else:
            SESSION_KEY = secrets.token_hex(32)
            os.makedirs(os.path.dirname(_key_file), exist_ok=True)
            with open(_key_file, 'w') as f:
                f.write(SESSION_KEY)
            os.chmod(_key_file, 0o600)
    else:
        SESSION_KEY = secrets.token_hex(32)
        print('warning: no session_key_file — key not shared with server.py', file=sys.stderr)

    CERT_FILE = args.cert or cfg.get('cert') or 'localhost.pem'
    KEY_FILE  = args.key  or cfg.get('key')  or 'localhost-key.pem'

    use_tls = os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)
    scheme  = 'https' if use_tls else 'http'

    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    else:
        ctx = None

    srv = http.server.ThreadingHTTPServer((BIND_ADDR, PORT), TerminalHandler)
    if ctx:
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    print(f'terminal → {scheme}://localhost:{PORT}')
    srv.serve_forever()
