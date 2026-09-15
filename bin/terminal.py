#!/usr/bin/env python3
"""
ezconf terminal service — standalone WebSocket PTY server.

Run:
  python3 terminal.py --config /run/ezconf/ezconf.toml
  python3 terminal.py --port 9092 --session-key-file /run/ezconf/session.key

Config keys read from TOML: terminal_port, session_key_file, shell, cert, key, webroot,
tmux_session
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import select
import shutil
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

TMUX_SESSION = None  # set by tmux_session in TOML; when set, the shell attaches to a
                      # persistent tmux session instead of being forked fresh per connection,
                      # so a running command survives this service restarting (or a reconnect)
                      # — attach here, --start-session creates it (see ezconf-terminal-session
                      # .service in the NixOS module)
TMUX_BIN     = None   # resolved via shutil.which() at startup; TMUX_SESSION is only honored if
                      # this is found, so a deployment without tmux installed just falls back
                      # to today's non-persistent behavior rather than failing
TMUX_HISTORY_LINES = 2000  # how much tmux scrollback to replay into a (re)connecting client


def _tmux_capture_scrollback():
    """Return this session's tmux scrollback with \\n normalized to \\r\\n for a raw terminal
    stream, or b'' if the session doesn't exist yet (nothing to replay) or tmux errors out.
    Sent to a (re)connecting client before it starts receiving live output, so it sees what it
    missed instead of a blank screen — tmux's own attach only redraws the current on-screen
    contents, not the scrollback leading up to it."""
    try:
        has = subprocess.run([TMUX_BIN, 'has-session', '-t', TMUX_SESSION],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if has.returncode != 0:
            return b''
        cap = subprocess.run(
            [TMUX_BIN, 'capture-pane', '-p', '-e', '-t', TMUX_SESSION,
             '-S', f'-{TMUX_HISTORY_LINES}'],
            capture_output=True, timeout=5,
        )
        return cap.stdout.replace(b'\n', b'\r\n')
    except Exception:
        return b''


def _drain_tmux_attach_burst(master_fd, quiet=0.15, timeout=0.5):
    """Read and discard tmux's own attach-handshake output (its redraw of the current screen,
    which includes an erase-scrollback sequence intended to clear a fresh terminal's native
    scrollback before repainting) for a short window right after a new client connects.

    Without this, that handshake lands on the wire right after our own scrollback replay
    (_tmux_capture_scrollback()) and wipes it out a moment after it's sent -- xterm.js respects
    the same erase-scrollback sequence tmux uses, so the replay would visibly disappear right
    as the live stream starts. Stops once master_fd goes quiet for `quiet` seconds (tmux's
    handshake is near-instant), or after `timeout` regardless, so a stuck attach can't block a
    connection indefinitely."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([master_fd], [], [], quiet)
        if not r:
            return
        try:
            os.read(master_fd, 4096)
        except OSError:
            return


def _terminal_ws(handler):
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

    replay = _tmux_capture_scrollback() if TMUX_SESSION and TMUX_BIN else b''

    state = {'proc': None, 'master_fd': None, 'rows': 24, 'cols': 80, 'done': False}

    def set_winsize(fd, rows, cols):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        except Exception:
            pass

    def launch_shell():
        old_fd = state['master_fd']
        if old_fd is not None:
            try: os.close(old_fd)
            except OSError: pass
        try:
            master_fd, slave_fd = pty.openpty()
        except Exception as e:
            print(f'[terminal] pty.openpty() failed: {e}', file=sys.stderr)
            return False
        set_winsize(slave_fd, state['rows'], state['cols'])
        env = {'TERM': 'xterm-256color'}

        def _init_child():
            os.setsid()
            try:
                fcntl.ioctl(0, getattr(termios, 'TIOCSCTTY', 0x540E), 0)
            except Exception:
                pass

        # attach-session: attach to the persistent session created by ezconf-terminal-session
        # .service (see --start-session below) instead of forking a fresh shell — a running
        # command survives this connection (or this whole process) ending, since the shell isn't
        # a child of ours anymore. tmux resizes the session to the attaching client automatically,
        # and redraws its current screen on attach — _tmux_capture_scrollback() above covers the
        # scrollback leading up to that, which attach alone doesn't replay.
        cmd = [TMUX_BIN, 'attach-session', '-t', TMUX_SESSION] if TMUX_SESSION and TMUX_BIN \
            else [SHELL, '-l']

        try:
            proc = subprocess.Popen(
                cmd,
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
            return False
        os.close(slave_fd)
        state['proc'] = proc
        state['master_fd'] = master_fd
        return True

    if not launch_shell():
        return

    if TMUX_SESSION and TMUX_BIN:
        _drain_tmux_attach_burst(state['master_fd'])

    if replay:
        try:
            _ws_send(wfile, replay, opcode=0x02)
        except Exception:
            pass

    def pty_to_ws():
        try:
            while not state['done']:
                proc      = state['proc']
                master_fd = state['master_fd']
                if proc.poll() is not None:
                    break
                r, _, _ = select.select([master_fd], [], [], 0.5)
                if r:
                    try:
                        data = os.read(master_fd, 4096)
                        _ws_send(wfile, data, opcode=0x02)
                    except OSError:
                        pass
        except Exception:
            pass
        finally:
            try:
                _ws_send(wfile, b'', 0x08)
            except Exception:
                pass

    threading.Thread(target=pty_to_ws, daemon=True).start()

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
                            state['rows'] = int(msg['rows'])
                            state['cols'] = int(msg['cols'])
                            set_winsize(state['master_fd'], state['rows'], state['cols'])
                            continue
                    except Exception:
                        pass
                try:
                    os.write(state['master_fd'], payload)
                except OSError:
                    pass
    except Exception as e:
        print(f'[terminal] ws loop error: {e}', file=sys.stderr)
    finally:
        state['done'] = True
        try:
            proc = state['proc']
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
            os.close(state['master_fd'])
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
    ap.add_argument('--start-session', action='store_true',
                    help='create (or configure) the persistent tmux session at tmux_session '
                         'instead of starting the WebSocket server; this is '
                         'ezconf-terminal-session.service, not something to run by hand')
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

    TMUX_SESSION = cfg.get('tmux_session') or None
    TMUX_BIN = shutil.which('tmux')

    if args.start_session:
        if not TMUX_SESSION:
            print('error: tmux_session not set in config', file=sys.stderr)
            sys.exit(1)
        if not TMUX_BIN:
            print('error: tmux not found on PATH', file=sys.stderr)
            sys.exit(1)
        os.environ['TERM'] = 'xterm-256color'
        # -d: create/leave it detached, no client attached from here — this process's job is
        # just to bring the session into existence (or no-op if it's already there, e.g. a
        # redundant unit start) and configure it, then exit; the actual attaching happens per
        # connection in launch_shell() above. Not check=True: a pre-existing session makes
        # new-session fail, which is fine, we only care that one exists by the time we're done.
        subprocess.run([TMUX_BIN, 'new-session', '-d', '-s', TMUX_SESSION, SHELL, '-l'])
        # status off: no tmux chrome in a panel that's meant to look like a plain shell.
        # history-limit: matched to TMUX_HISTORY_LINES above, so a scrollback replay can
        # actually reach back that far.
        subprocess.run([TMUX_BIN, 'set-option', '-t', TMUX_SESSION, 'status', 'off'])
        subprocess.run([TMUX_BIN, 'set-option', '-t', TMUX_SESSION, 'history-limit',
                         str(TMUX_HISTORY_LINES)])
        sys.exit(0)

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
