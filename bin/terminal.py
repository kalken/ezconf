#!/usr/bin/env python3
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
see _SESSION's own comment for why. Reconnecting reattaches to the same still-running shell
(replaying its recent output) instead of forking a fresh one; the shell itself only ever dies when
it exits on its own, or when this whole process does (a reboot, a manual restart of
ezconf-terminal.service) -- nothing here can make it survive that. With terminal_persist left at
its default, a client disconnecting kills the shell immediately (see
TERM_PERSIST/_terminal_ws()'s own finally below) -- exactly the original, pre-persistence
behavior, except still shared across simultaneous connections rather than one shell each.
"""
import argparse
import base64
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
# 'lock' guarding that session's mutable state (buffer/writers/rows/cols) -- two separate locks so
# the session's I/O never blocks a connect/disconnect deciding whether to (re)create it.
_SESSION = None
_SESSION_LOCK = threading.Lock()

# Bound on how much recent output is kept for replay to a client that (re)connects. Not a
# scrollback restore -- xterm.js's own in-browser scrollback already covers a tab that's stayed
# open continuously -- just enough recent context that a *newly* connecting client (a fresh tab,
# or the first reconnect after a drop) isn't looking at a blank screen for a shell that's actually
# been running and producing output all along.
TERM_BUFFER_MAX = 200_000


def _set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    except Exception:
        pass


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
        'buffer': bytearray(),
        'writers': set(),
        'lock': threading.Lock(),
        'done': False,
    }
    threading.Thread(target=_session_reader, args=(session,), daemon=True).start()
    return session


def _session_reader(session):
    """The one thread that ever reads this session's PTY, for its entire lifetime -- started once
    by _create_session() and never restarted. Keeps draining the PTY (into a bounded buffer, and
    out live to whichever clients are currently attached) regardless of whether anyone's attached
    at all, so output isn't lost between a client detaching and a later reconnect, and the shell
    is never left blocked writing into a full pipe with nothing draining it. Only exits -- and
    only then tears the shell down -- once the shell process itself actually exits or the PTY read
    fails outright; a client disconnecting never reaches this at all (see _terminal_ws()'s own
    finally below, which only ever removes that one client from 'writers')."""
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
                buf = session['buffer']
                buf.extend(data)
                if len(buf) > TERM_BUFFER_MAX:
                    del buf[:len(buf) - TERM_BUFFER_MAX]
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
        replay = bytes(session['buffer'])

    if replay:
        try:
            _ws_send(wfile, replay, opcode=0x02)
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
        _ws_send(wfile, json.dumps({'type': 'ready'}).encode(), opcode=0x01)
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
                            with session['lock']:
                                session['rows'] = int(msg['rows'])
                                session['cols'] = int(msg['cols'])
                                _set_winsize(session['master_fd'], session['rows'], session['cols'])
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
