#!/usr/bin/env python3
"""
ezconf server — single listener bound to 127.0.0.1:
  http(s)://localhost:9090  static files + API
    POST /api/v1/save-config   writes the config file specified by --file

Run:
  python3 server.py --file /path/to/configuration.json
  python3 server.py --webroot /path/to/webroot --file /path/to/configuration.json
  python3 server.py --terminal-port 9092 --file ...  show terminal panel (run terminal.py separately)
  python3 server.py --auth custom --file ...          custom username/password from ezconf.toml
  python3 server.py --auth pam --file ...             PAM auth (requires python-pam)
  python3 server.py --generate-cert [DIR]             generate cert only (DIR defaults to .)

Terminal:
  Run terminal.py separately. Pass --terminal-port (or set terminal_port in TOML) to enable
  the terminal panel and point the frontend at the right port.

Auth:
  --auth auto     (default) pam if available, else custom
  --auth custom   username/password from ezconf.toml (requires "username" and "password")
  --auth pam      system username + password via PAM (requires python-pam)

Config file (ezconf.toml):
  file, webroot, auth, terminal_port, session_key_file, cert, key, username, password,
  allowed_users, mkoptions, nixos_target, ports.web
"""
import argparse
import datetime
import http.server
import ipaddress
import json
import os
import secrets
import ssl
import subprocess
import sys
import threading
from urllib.parse import urlparse, parse_qs

try:
    import pam as _pam
    _PAM = True
except ImportError:
    _PAM = None

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    _CRYPTO = True
except ImportError:
    _CRYPTO = False


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

def _resolve(cli, toml, env, default):
    """Return the first non-None value: CLI arg > TOML value > env var > default."""
    for v in (cli, toml, env):
        if v is not None:
            return v
    return default

WEB_PORT      = 9090
CERT_FILE     = 'localhost.pem'
KEY_FILE      = 'localhost-key.pem'
ALLOWED_USERS = set()

WEBROOT          = os.path.join(os.getcwd(), 'webroot')  # serves all static files; set by --webroot
AUTOCOMPLETE_DIR = None          # override for /autocomplete/ requests; set by --autocomplete-dir
CONFIG_FILE      = None          # path to the JSON config file to edit; set by --file
AUTH_MODE        = 'none'        # set by --auth: 'none', 'custom', 'pam'
TERMINAL_ENABLED = False         # True when terminal_port is set
TERMINAL_PORT    = None          # port the terminal WebSocket service is running on
THEME            = 'nixos'       # ui theme: nixos, dark, light
LOGIN_USER       = ''            # custom auth username
LOGIN_PASS       = ''            # custom auth password
MKOPTIONS_CMD    = None          # path to ezconf-mkoptions binary; enables /api/v1/update-autocomplete
NIXOS_TARGET     = '/etc/nixos'  # flake path passed as TARGET to mkoptions
TRUSTED_HOSTS    = set()         # extra hostnames allowed by _valid_host; set by trusted_hosts in TOML
BIND_ADDR        = '127.0.0.1'   # IP address to listen on; set by listen in TOML

_SESSION_KEY = secrets.token_hex(32)


def make_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    return ctx


def generate_local_ca(out_dir):
    """Generate a local CA and a server cert signed by it — trusted by Chrome via NSS."""
    if not _CRYPTO:
        sys.exit('error: --generate-ca requires the cryptography package (pip install cryptography)')
    os.makedirs(out_dir, exist_ok=True)
    ca_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ezconf Local CA')])
    ca_cert = (x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            key_cert_sign=True, crl_sign=True, digital_signature=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            content_commitment=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    srv_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_cert = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
        ]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    for path, data in [
        (os.path.join(out_dir, 'ca-key.pem'),        ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())),
        (os.path.join(out_dir, 'ca.pem'),             ca_cert.public_bytes(serialization.Encoding.PEM)),
        (os.path.join(out_dir, 'localhost-key.pem'),  srv_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())),
        (os.path.join(out_dir, 'localhost.pem'),       srv_cert.public_bytes(serialization.Encoding.PEM)),
    ]:
        with open(path, 'wb') as f:
            f.write(data)


def generate_self_signed_cert(cert_path, key_path):
    if not _CRYPTO:
        sys.exit('error: --generate-cert requires the cryptography package (pip install cryptography)')
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    cert = (x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def check_pam(username, password):
    if _PAM is None:
        return False
    try:
        return _pam.pam().authenticate(username, password)
    except Exception:
        return False


def user_allowed(username):
    return not ALLOWED_USERS or username in ALLOWED_USERS


def validate_credentials(username, password):
    """Return True if username/password are valid."""
    if AUTH_MODE == 'custom':
        return username == LOGIN_USER and password == LOGIN_PASS
    if AUTH_MODE == 'pam':
        if not user_allowed(username):
            return False
        return check_pam(username, password)
    return False

def _session_from_cookie(headers):
    for part in headers.get('Cookie', '').split(';'):
        k, _, v = part.strip().partition('=')
        if k.strip() == 'ezconf_session':
            return v.strip()
    return ''

def check_auth(headers):
    return _session_from_cookie(headers) == _SESSION_KEY


def _read_login_page(error=''):
    if ALLOWED_USERS:
        options = ''.join(f'<option value="{u}">{u}</option>' for u in sorted(ALLOWED_USERS))
        username_field = f'<select id="u" name="username" class="enum-select">{options}</select>'
    else:
        username_field = '<input id="u" name="username" type="text" autocomplete="username" autofocus>'
    path = os.path.join(WEBROOT, 'login.html')
    try:
        return (open(path).read()
                .replace('%%EZCONF_ERROR%%', error)
                .replace('%%EZCONF_THEME%%', THEME)
                .replace('%%EZCONF_USERNAME_FIELD%%', username_field))
    except FileNotFoundError:
        return f'<html><body><form method="post" action="/login"><input name="username"><input name="password" type="password"><button>Sign in</button></form><p>{error}</p></body></html>'


class StaticHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        self.directory = WEBROOT
        return super().translate_path(path)

    def _deny(self, error=''):
        accept = self.headers.get('Accept', '')
        if 'text/html' in accept:
            page = _read_login_page(error).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        else:
            self.send_response(401)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Unauthorized\n')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/login':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8', errors='replace')
            params = {k: v[0] for k, v in parse_qs(body).items()}
            username = params.get('username', '')
            password = params.get('password', '')
            if validate_credentials(username, password):
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie', f'ezconf_session={_SESSION_KEY}; HttpOnly; SameSite=Strict; Path=/')
                self.end_headers()
            else:
                self._deny('Invalid username or password.')
            return
        if not _valid_host(self.headers):
            self.send_error(403); return
        if not check_auth(self.headers):
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"Unauthorized"}')
            return
        if parsed.path == '/api/v1/save-config':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(body, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/update-autocomplete':
            if not MKOPTIONS_CMD:
                resp = json.dumps({'error': 'mkoptions not configured'}).encode()
                self.send_response(501)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return
            out_dir = AUTOCOMPLETE_DIR or os.path.join(WEBROOT, 'autocomplete')
            env = {**os.environ, 'TARGET': NIXOS_TARGET}
            try:
                result = subprocess.run(
                    [MKOPTIONS_CMD, '-o', out_dir],
                    env=env, capture_output=True, text=True, timeout=600
                )
                if result.returncode == 0:
                    resp = b'{"ok":true}'
                    self.send_response(200)
                else:
                    msg = (result.stderr or result.stdout or 'unknown error').strip()
                    resp = json.dumps({'error': msg}).encode()
                    self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except subprocess.TimeoutExpired:
                resp = b'{"error":"timed out after 600s"}'
                self.send_response(504)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    _PUBLIC_PATHS = {'/style.css', '/login.html'}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/logout':
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Set-Cookie', 'ezconf_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            self.end_headers()
            return
        if parsed.path in self._PUBLIC_PATHS or (
                parsed.path.startswith('/theme-') and parsed.path.endswith('.css')):
            super().do_GET()
            return
        if not check_auth(self.headers):
            self._deny(); return
        if parsed.path.rstrip('/') in ('', '/index.html'):
            self._serve_index(); return
        # configuration.json and custom-options.json live next to CONFIG_FILE, not in WEBROOT
        if parsed.path == '/configuration.json':
            self._serve_raw(CONFIG_FILE); return
        if parsed.path == '/custom-options.json':
            self._serve_raw(os.path.join(os.path.dirname(CONFIG_FILE), 'custom-options.json')); return
        # autocomplete files served from AUTOCOMPLETE_DIR when set
        if AUTOCOMPLETE_DIR and parsed.path.startswith('/autocomplete/'):
            rel = os.path.normpath(parsed.path[len('/autocomplete/'):]).lstrip('/')
            self._serve_raw(os.path.join(AUTOCOMPLETE_DIR, rel)); return
        super().do_GET()

    def do_HEAD(self):
        if not check_auth(self.headers):
            self._deny(); return
        super().do_HEAD()

    def _serve_raw(self, path):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_index(self):
        try:
            terminal_scripts = (
                '<link rel="stylesheet" href="addons/xterm.css">\n'
                '<script src="addons/xterm.js"></script>\n'
                '<script src="addons/xterm-addon-fit.js"></script>\n'
                '<script src="addons/xterm-addon-webgl.js"></script>'
            ) if TERMINAL_PORT else ''
            content = (open(os.path.join(WEBROOT, 'index.html')).read()
                .replace('%%EZCONF_TERMINAL_SCRIPTS%%', terminal_scripts)
                .replace('%%EZCONF_TERMINAL%%', 'true' if TERMINAL_PORT else 'false')
                .replace('%%EZCONF_TERMINAL_PORT%%', str(TERMINAL_PORT or WEB_PORT))
                .replace('%%EZCONF_THEME%%', THEME)
                .replace('%%EZCONF_MKOPTIONS%%', 'true' if MKOPTIONS_CMD else 'false')
            )
            data = content.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, fmt, *args):
        print(f'[web]  {self.address_string()} - {fmt % args}')


def _valid_host(headers):
    host = headers.get('Host', '').split(':')[0].lower()
    return host in {'localhost', '127.0.0.1', ''} | TRUSTED_HOSTS


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='ezconf web server')
    ap.add_argument('--config', metavar='FILE', default=None,
                    help='TOML config file (default: ezconf.toml in current directory)')
    ap.add_argument('--webroot', metavar='DIR', default=None,
                    help='directory to serve all static files from')
    ap.add_argument('--autocomplete-dir', metavar='DIR', default=None,
                    help='directory to serve /autocomplete/ from (overrides WEBROOT/autocomplete/)')
    ap.add_argument('--mkoptions', metavar='CMD', default=None,
                    help='path to ezconf-mkoptions binary; enables the Update Autocomplete button')
    ap.add_argument('--nixos-target', metavar='PATH', default=None,
                    help='flake path passed as TARGET to mkoptions (default: /etc/nixos)')
    ap.add_argument('--file', metavar='FILE', default=None,
                    help='configuration JSON file to edit')
    ap.add_argument('--auth', choices=['auto', 'custom', 'pam'], default=None,
                    help='authentication mode: auto, custom, or pam')
    ap.add_argument('--theme', choices=['nixos', 'dark', 'light'], default=None,
                    help='UI theme (default: nixos)')
    ap.add_argument('--terminal-port', type=int, default=None,
                    help='port the terminal.py WebSocket service is running on (enables terminal panel)')
    ap.add_argument('--session-key-file', metavar='FILE', default=None,
                    help='file to persist session key across service restarts')
    ap.add_argument('--cert', metavar='FILE', default=None, help='TLS certificate file (PEM)')
    ap.add_argument('--key',  metavar='FILE', default=None, help='TLS private key file (PEM)')
    ap.add_argument('--generate-cert', metavar='DIR', nargs='?', const='.',
                    help='generate a self-signed cert in DIR (default: current directory)')
    ap.add_argument('--generate-ca', metavar='DIR', nargs='?', const='.',
                    help='generate a local CA + server cert in DIR; CA can be installed in browser trust store')
    args = ap.parse_args()

    cfg = load_toml(args.config or 'ezconf.toml')

    _wr = _resolve(args.webroot, cfg.get('webroot'), None, None)
    if _wr:
        WEBROOT = os.path.abspath(_wr)

    _ac = _resolve(args.autocomplete_dir, cfg.get('autocomplete_dir'), None, None)
    if _ac:
        AUTOCOMPLETE_DIR = os.path.abspath(_ac)

    _mk = _resolve(args.mkoptions, cfg.get('mkoptions'), None, None)
    if _mk:
        MKOPTIONS_CMD = os.path.abspath(_mk)
    NIXOS_TARGET = _resolve(args.nixos_target, cfg.get('nixos_target'), None, '/etc/nixos')

    CERT_FILE = _resolve(args.cert, cfg.get('cert'), None, 'localhost.pem')
    KEY_FILE  = _resolve(args.key,  cfg.get('key'),  None, 'localhost-key.pem')
    AUTH_MODE = _resolve(args.auth, cfg.get('auth'), None, 'auto')
    THEME     = _resolve(args.theme, cfg.get('theme'), None, 'nixos')
    _term_port = args.terminal_port or cfg.get('terminal_port')
    if _term_port:
        TERMINAL_PORT    = int(_term_port)
        TERMINAL_ENABLED = True

    _key_file = args.session_key_file or cfg.get('session_key_file')
    if _key_file:
        _key_file = os.path.abspath(_key_file)
        if os.path.exists(_key_file):
            _SESSION_KEY = open(_key_file).read().strip()
        else:
            _SESSION_KEY = secrets.token_hex(32)
            os.makedirs(os.path.dirname(_key_file), exist_ok=True)
            with open(_key_file, 'w') as f:
                f.write(_SESSION_KEY)
            os.chmod(_key_file, 0o600)

    BIND_ADDR = cfg.get('listen') or '127.0.0.1'

    _trusted = list(cfg.get('trusted_hosts') or [])
    TRUSTED_HOSTS = {h.lower().strip() for h in _trusted if h.strip()}

    LOGIN_USER = cfg.get('username') or ''
    LOGIN_PASS = cfg.get('password') or ''

    _toml_users = cfg.get('allowed_users')
    if _toml_users:
        ALLOWED_USERS = {u.strip() for u in _toml_users if u.strip()}

    _ports = cfg.get('ports', {})
    WEB_PORT = int(_ports.get('web', WEB_PORT))

    if AUTH_MODE == 'auto':
        AUTH_MODE = 'pam' if _PAM is not None else 'custom'

    if AUTH_MODE == 'pam' and not ALLOWED_USERS:
        _current_user = os.environ.get('USER') or os.environ.get('LOGNAME') or ''
        if _current_user:
            ALLOWED_USERS = {_current_user}

    if AUTH_MODE == 'custom' and not (LOGIN_USER and LOGIN_PASS):
        ap.error('auth = "custom" requires "username" and "password" set in ezconf.toml')
    elif AUTH_MODE == 'pam' and _PAM is None:
        ap.error('--auth pam requires python-pam (pip install python-pam)')


    if args.generate_cert is not None:
        cert_dir = os.path.abspath(args.generate_cert)
        cert_path = os.path.join(cert_dir, 'localhost.pem')
        key_path  = os.path.join(cert_dir, 'localhost-key.pem')
        if os.path.exists(cert_path) and os.path.exists(key_path):
            print(f'cert → {cert_path} (already exists, skipping)')
        else:
            generate_self_signed_cert(cert_path, key_path)
            print(f'cert → {cert_path}')
            print(f'key  → {key_path}')
        if not args.cert:
            CERT_FILE = cert_path
        if not args.key:
            KEY_FILE = key_path
        if not args.file and not cfg.get('file'):
            sys.exit(0)  # cert-only mode

    if args.generate_ca is not None:
        ca_dir    = os.path.abspath(args.generate_ca)
        ca_path   = os.path.join(ca_dir, 'ca.pem')
        cert_path = os.path.join(ca_dir, 'localhost.pem')
        key_path  = os.path.join(ca_dir, 'localhost-key.pem')
        if os.path.exists(cert_path) and os.path.exists(key_path):
            print(f'cert → {cert_path} (already exists, skipping)')
        else:
            generate_local_ca(ca_dir)
            print(f'ca   → {ca_path}')
            print(f'cert → {cert_path}')
            print(f'key  → {key_path}')
        if not args.cert:
            CERT_FILE = cert_path
        if not args.key:
            KEY_FILE = key_path
        if not args.file and not cfg.get('file'):
            sys.exit(0)  # cert-only mode

    _file = _resolve(args.file, cfg.get('file'), None, None)
    if not _file:
        ap.error('--file is required (or set "file" in ezconf.toml)')
    CONFIG_FILE = os.path.abspath(_file)

    use_tls = os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)
    scheme = 'https' if use_tls else 'http'

    if use_tls:
        ctx = make_ssl_context()
    else:
        ctx = None
        print('No certificates found — running plain HTTP.')
        print('For HTTPS: python3 server.py --generate-cert [DIR]')

    web_srv = http.server.ThreadingHTTPServer((BIND_ADDR, WEB_PORT), StaticHandler)

    if ctx:
        web_srv.socket = ctx.wrap_socket(web_srv.socket, server_side=True)

    print(f'web  → {scheme}://localhost:{WEB_PORT}')
    print(f'dir  → {WEBROOT}')
    print(f'file → {CONFIG_FILE}')
    if AUTH_MODE == 'custom':
        print(f'auth → custom   (username: {LOGIN_USER})')
    elif AUTH_MODE == 'pam':
        print(f'auth → PAM      (system username + password)')
    if ALLOWED_USERS:
        print(f'users → {", ".join(sorted(ALLOWED_USERS))}')
    if TERMINAL_PORT:
        print(f'term  → {scheme}://localhost:{TERMINAL_PORT}')
    else:
        print(f'term  → disabled (run terminal.py and set --terminal-port)')
    web_srv.serve_forever()
