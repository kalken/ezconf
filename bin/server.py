#!/usr/bin/env python3
"""
ezconf server — single listener bound to 127.0.0.1:
  http(s)://localhost:9090  static files + API
    GET  /api/v1/files            lists the config files (tabs) and folders in CONFIG_DIR
    POST /api/v1/save-config      writes a config file (backs up first); creates it if new
    GET  /api/v1/backups          lists backups for a config file
    POST /api/v1/create-backup    backs up a config file's current on-disk contents on demand
    POST /api/v1/restore-backup   restores a backup over a config file
    POST /api/v1/delete-backup    deletes a backup file
    POST /api/v1/delete-file      deletes a whole config file (refuses to delete the last one)
    POST /api/v1/rename-file      renames/moves a config file (same op — moving between
                                   subfolders is just a path change)
    POST /api/v1/create-folder    creates an (initially empty) subfolder under CONFIG_DIR
    POST /api/v1/delete-folder    deletes a subfolder and everything in it (refuses if that
                                   would leave zero config files anywhere)

Every *.json file in CONFIG_DIR (except custom-options.json) is a
separately editable/saveable "tab" in the UI, merged together only at
Nix-eval time via lib.mkMerge. --file (or "file" in ezconf.toml) can name
CONFIG_DIR directly, or a specific *.json file inside it (kept for
compatibility — its directory becomes CONFIG_DIR and it's used as the
initially selected tab).

Run:
  python3 server.py --file /path/to/config-dir/
  python3 server.py --webroot /path/to/webroot --file /path/to/config-dir/
  python3 server.py --file /path/to/config-dir/configuration.json
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
  file, default_file, webroot, auth, terminal_port, session_key_file, cert, key, username,
  password, allowed_users, mkoptions, nixos_target, ports.web, backup_dir, backup_count
"""
import argparse
import datetime
import http.server
import ipaddress
import json
import os
import secrets
import shutil
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
CONFIG_DIR       = None          # directory of JSON config files (tabs); set by --file
DEFAULT_FILE     = None          # basename to prefer as the initial tab; set when --file names a specific file
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
CA_FILE          = None          # path to CA cert served at /download-ca; set by --generate-ca or ca_file in TOML
BACKUP_DIR       = None          # directory for configuration.json backups; set by --backup-dir or backup_dir in TOML (default: <config dir>/.ezconf-backups)
BACKUP_COUNT     = 5             # number of backups to keep; 0 disables backups; set by --backup-count or backup_count in TOML

_SESSION_KEY = secrets.token_hex(32)


def make_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    return ctx


def _build_sans(extra_sans=None):
    sans = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.IPv4Address('127.0.0.1'))]
    seen = {'localhost', '127.0.0.1'}
    for san in (extra_sans or []):
        san = san.strip()
        if not san or san in seen or san in ('0.0.0.0', '::'):
            continue
        seen.add(san)
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            sans.append(x509.DNSName(san))
    return sans


def _cert_san_strings(cert_path):
    with open(cert_path, 'rb') as f:
        cert = x509.load_pem_x509_certificate(f.read())
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        result = set()
        for name in ext.value:
            if isinstance(name, x509.DNSName):
                result.add(name.value)
            elif isinstance(name, x509.IPAddress):
                result.add(str(name.value))
        return result
    except x509.ExtensionNotFound:
        return set()


def _wanted_san_strings(extra_sans=None):
    result = set()
    for san in _build_sans(extra_sans):
        if isinstance(san, x509.DNSName):
            result.add(san.value)
        elif isinstance(san, x509.IPAddress):
            result.add(str(san.value))
    return result


def _generate_server_cert(out_dir, ca_key, ca_cert, extra_sans=None):
    srv_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_cert = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
        .issuer_name(ca_cert.subject)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc))
        .add_extension(x509.SubjectAlternativeName(_build_sans(extra_sans)), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    for path, data in [
        (os.path.join(out_dir, 'localhost-key.pem'), srv_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())),
        (os.path.join(out_dir, 'localhost.pem'),      srv_cert.public_bytes(serialization.Encoding.PEM)),
    ]:
        with open(path, 'wb') as f:
            f.write(data)


def generate_local_ca(out_dir, extra_sans=None):
    """Ensure a local CA and server cert exist with the correct SANs.

    The CA is only generated once. The server cert is regenerated whenever
    the required SANs don't match the existing cert.
    Returns (ca_generated, srv_generated).
    """
    if not _CRYPTO:
        sys.exit('error: --generate-ca requires the cryptography package (pip install cryptography)')
    os.makedirs(out_dir, exist_ok=True)

    ca_key_path  = os.path.join(out_dir, 'ca-key.pem')
    ca_cert_path = os.path.join(out_dir, 'ca.pem')
    cert_path    = os.path.join(out_dir, 'localhost.pem')

    ca_generated = False
    if not os.path.exists(ca_key_path) or not os.path.exists(ca_cert_path):
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
        for path, data in [
            (ca_key_path,  ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())),
            (ca_cert_path, ca_cert.public_bytes(serialization.Encoding.PEM)),
        ]:
            with open(path, 'wb') as f:
                f.write(data)
        ca_generated = True
    else:
        with open(ca_key_path, 'rb') as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(ca_cert_path, 'rb') as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())

    wanted = _wanted_san_strings(extra_sans)
    srv_generated = False
    if not os.path.exists(cert_path) or _cert_san_strings(cert_path) != wanted:
        _generate_server_cert(out_dir, ca_key, ca_cert, extra_sans)
        srv_generated = True

    return ca_generated, srv_generated


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


def _flatten_stem(rel):
    """Turn a CONFIG_DIR-relative path like 'services/nginx.json' into a flat, collision-safe
    backup stem ('services--nginx') so BACKUP_DIR itself never needs subdirectories."""
    return os.path.splitext(rel)[0].replace(os.sep, '--').replace('/', '--')


def backup_config(path):
    """Copy path into BACKUP_DIR, pruning to BACKUP_COUNT newest backups sharing its stem."""
    if BACKUP_COUNT <= 0 or not os.path.exists(path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    rel = os.path.relpath(path, os.path.realpath(CONFIG_DIR))
    stem = _flatten_stem(rel)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'{stem}-{ts}.json')
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(BACKUP_DIR, f'{stem}-{ts}-{i}.json')
        i += 1
    shutil.copy2(path, dest)
    prefix = f'{stem}-'
    backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix) and f.endswith('.json')]
    backups.sort(key=lambda f: os.path.getmtime(os.path.join(BACKUP_DIR, f)), reverse=True)
    for old in backups[BACKUP_COUNT:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def list_backups(stem):
    items = []
    prefix = f'{stem}-'
    if os.path.isdir(BACKUP_DIR):
        for name in os.listdir(BACKUP_DIR):
            if not (name.startswith(prefix) and name.endswith('.json')):
                continue
            st = os.stat(os.path.join(BACKUP_DIR, name))
            items.append({'name': name, 'mtime': st.st_mtime, 'size': st.st_size})
    items.sort(key=lambda x: x['mtime'], reverse=True)
    return items


def resolve_backup_path(name):
    """Return the absolute path for a backup file name, or None if invalid/outside BACKUP_DIR."""
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        return None
    base = os.path.realpath(BACKUP_DIR)
    full = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(full) != base or not os.path.isfile(full):
        return None
    return full


def resolve_config_path(name):
    """Return the absolute path for a config file name inside CONFIG_DIR, or None if invalid.

    Falls back to DEFAULT_FILE when name is empty, so a caller that hasn't learned the file
    list yet still resolves to a sensible file. Does not require the file to already exist,
    since save-config uses this to create new tabs. Subpaths (e.g. "services/nginx.json") are
    allowed for organizing tabs into folders; this only keeps writes inside CONFIG_DIR by
    construction (an authenticated user here already has full terminal access to the machine,
    so this is a correctness guard against typos, not a security boundary).
    """
    name = name or DEFAULT_FILE
    if not name or not name.endswith('.json') or os.path.basename(name) == 'custom-options.json':
        return None
    parts = name.replace('\\', '/').split('/')
    if os.path.isabs(name) or any(p in ('', '.', '..') for p in parts):
        return None
    base = os.path.realpath(CONFIG_DIR)
    full = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, full]) != base:
        return None
    return full


def list_config_files():
    """Recursively list the *.json tabs under CONFIG_DIR as relative POSIX paths.

    Skips dotdirs (in particular BACKUP_DIR's default name, .ezconf-backups, when it lives
    inside CONFIG_DIR) so backup files never show up as tabs.
    """
    base = os.path.realpath(CONFIG_DIR)
    names = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fn in filenames:
            if not fn.endswith('.json') or fn == 'custom-options.json':
                continue
            rel = os.path.relpath(os.path.join(root, fn), base).replace(os.sep, '/')
            names.append(rel)
    names.sort()
    return names


def list_config_folders():
    """Recursively list every subdirectory under CONFIG_DIR as a relative POSIX path.

    Unlike the folders implied by list_config_files(), this also reports directories that
    don't (yet) contain any *.json file, so a folder created via /api/v1/create-folder still
    shows up as an (empty) tab group after a reload.
    """
    base = os.path.realpath(CONFIG_DIR)
    names = []
    for root, dirs, _filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for d in dirs:
            rel = os.path.relpath(os.path.join(root, d), base).replace(os.sep, '/')
            names.append(rel)
    names.sort()
    return names


def resolve_folder_path(name):
    """Like resolve_config_path, but for a directory rather than a *.json file — no extension
    requirement, and the directory need not already exist."""
    if not name:
        return None
    parts = name.replace('\\', '/').split('/')
    if os.path.isabs(name) or any(p in ('', '.', '..') for p in parts):
        return None
    base = os.path.realpath(CONFIG_DIR)
    full = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, full]) != base:
        return None
    return full


def _config_stem(name):
    """Resolve name to a config path and return its flattened backup stem, or None if invalid."""
    path = resolve_config_path(name)
    if path is None:
        return None
    rel = os.path.relpath(path, os.path.realpath(CONFIG_DIR))
    return _flatten_stem(rel)


def _read_login_page(error=''):
    if ALLOWED_USERS:
        options = ''.join(f'<option value="{u}">{u}</option>' for u in sorted(ALLOWED_USERS))
        username_field = f'<select id="u" name="username" class="enum-select">{options}</select>'
    else:
        username_field = '<input id="u" name="username" type="text" autocomplete="username" autofocus>'
    ca_link = ''
    if CA_FILE and os.path.exists(CA_FILE):
        ca_link = '<div class="login-ca-link"><a href="/download-ca">Download CA certificate</a></div>'
    path = os.path.join(WEBROOT, 'login.html')
    try:
        return (open(path).read()
                .replace('%%EZCONF_ERROR%%', error)
                .replace('%%EZCONF_THEME%%', THEME)
                .replace('%%EZCONF_USERNAME_FIELD%%', username_field)
                .replace('%%EZCONF_CA_LINK%%', ca_link))
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
                qs = parse_qs(parsed.query)
                target = resolve_config_path(qs.get('file', [None])[0])
                if not target:
                    resp = b'{"error":"invalid file name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                backup_config(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, 'w') as f:
                    json.dump(body, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/restore-backup':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_config_path(body.get('file'))
                if not target:
                    resp = b'{"error":"invalid file name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                src = resolve_backup_path(body.get('name', ''))
                if not src:
                    resp = b'{"error":"invalid backup name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                shutil.copy2(src, target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/create-backup':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_config_path(body.get('file'))
                if not target or not os.path.isfile(target):
                    resp = b'{"error":"invalid file name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                if BACKUP_COUNT <= 0:
                    resp = b'{"error":"backups are disabled (backup_count is 0)"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                backup_config(target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/delete-file':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_config_path(body.get('file', ''))
                if not target or not os.path.isfile(target):
                    resp = b'{"error":"invalid file name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                if len(list_config_files()) <= 1:
                    resp = b'{"error":"cannot delete the last remaining file"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.remove(target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/rename-file':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                src = resolve_config_path(body.get('from', ''))
                dst = resolve_config_path(body.get('to', ''))
                if not src or not os.path.isfile(src) or not dst:
                    resp = b'{"error":"invalid file name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                if os.path.exists(dst):
                    resp = b'{"error":"a file already exists at the destination"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/create-folder':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_folder_path(body.get('folder', ''))
                if not target:
                    resp = b'{"error":"invalid folder name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                if os.path.isfile(target):
                    resp = b'{"error":"a file already exists there"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.makedirs(target, exist_ok=True)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/delete-folder':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                folder = body.get('folder', '')
                target = resolve_folder_path(folder)
                if not target or not os.path.isdir(target):
                    resp = b'{"error":"invalid folder name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                prefix = folder.rstrip('/') + '/'
                remaining = [f for f in list_config_files() if not f.startswith(prefix)]
                if not remaining:
                    resp = b'{"error":"cannot delete the only files that exist"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                shutil.rmtree(target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/delete-backup':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_backup_path(body.get('name', ''))
                if not target:
                    resp = b'{"error":"invalid backup name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.remove(target)
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
        if parsed.path == '/download-ca':
            if CA_FILE and os.path.exists(CA_FILE):
                with open(CA_FILE, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-pem-file')
                self.send_header('Content-Disposition', 'attachment; filename="ezconf-ca.pem"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
            return
        if parsed.path in self._PUBLIC_PATHS or (
                parsed.path.startswith('/theme-') and parsed.path.endswith('.css')):
            super().do_GET()
            return
        if not check_auth(self.headers):
            self._deny(); return
        if parsed.path.rstrip('/') in ('', '/index.html'):
            self._serve_index(); return
        if parsed.path == '/api/v1/files':
            try:
                data = json.dumps({
                    'files': list_config_files(),
                    'folders': list_config_folders(),
                    'default': DEFAULT_FILE,
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
            return
        if parsed.path == '/api/v1/backups':
            try:
                qs = parse_qs(parsed.query)
                stem = _config_stem(qs.get('file', [None])[0])
                if stem is None:
                    self.send_error(400); return
                data = json.dumps({'backups': list_backups(stem)}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
            return
        # configuration.json and custom-options.json live in CONFIG_DIR, not in WEBROOT
        if parsed.path == '/configuration.json':
            qs = parse_qs(parsed.query)
            target = resolve_config_path(qs.get('file', [None])[0])
            if not target:
                self.send_error(400); return
            self._serve_raw(target); return
        if parsed.path == '/custom-options.json':
            self._serve_raw(os.path.join(CONFIG_DIR, 'custom-options.json')); return
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
                .replace('%%EZCONF_BACKUP%%', 'true' if BACKUP_COUNT > 0 else 'false')
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
                    help='directory of JSON config files (tabs) to edit, or a specific *.json file inside one')
    ap.add_argument('--default-file', metavar='NAME', default=None,
                    help='file (relative to --file, when --file is a directory) to prefer as the initially-selected tab')
    ap.add_argument('--backup-dir', metavar='DIR', default=None,
                    help='directory to store configuration.json backups (default: <config dir>/.ezconf-backups)')
    ap.add_argument('--backup-count', metavar='N', type=int, default=None,
                    help='number of backups to keep on each save; 0 disables backups (default: 5)')
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
    ap.add_argument('--san', metavar='NAME', action='append',
                    help='extra IP or hostname to include in generated cert SANs (repeat for multiple)')
    ap.add_argument('--ca-file', metavar='FILE', default=None,
                    help='path to CA cert to serve at /download-ca (set automatically by --generate-ca)')
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

    _ca = _resolve(args.ca_file, cfg.get('ca_file'), None, None)
    if _ca:
        CA_FILE = os.path.abspath(_ca)

    _trusted = list(cfg.get('trusted_hosts') or [])
    TRUSTED_HOSTS = {h.lower().strip() for h in _trusted if h.strip()}
    if BIND_ADDR not in ('0.0.0.0', '::'):
        TRUSTED_HOSTS.add(BIND_ADDR.lower())
    for _san in (args.san or []):
        _san = _san.strip().lower()
        if _san and _san not in ('0.0.0.0', '::'):
            TRUSTED_HOSTS.add(_san)

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
        extra_sans = list(args.san or [])
        if BIND_ADDR not in ('0.0.0.0', '::'):
            extra_sans.append(BIND_ADDR)
        ca_new, srv_new = generate_local_ca(ca_dir, extra_sans=extra_sans)
        if ca_new:
            print(f'ca   → {ca_path} (new)')
        if srv_new:
            print(f'cert → {cert_path} ({"new" if ca_new else "regenerated — SANs changed"})')
            print(f'key  → {key_path}')
        if not ca_new and not srv_new:
            print(f'cert → {cert_path} (SANs unchanged, skipping)')
        if not args.cert:
            CERT_FILE = cert_path
        if not args.key:
            KEY_FILE = key_path
        if not CA_FILE:
            CA_FILE = ca_path
        if not args.file and not cfg.get('file'):
            sys.exit(0)  # cert-only mode

    _file = _resolve(args.file, cfg.get('file'), None, None)
    if not _file:
        ap.error('--file is required (or set "file" in ezconf.toml)')
    _file = os.path.abspath(_file)
    if os.path.isdir(_file):
        CONFIG_DIR = _file
        DEFAULT_FILE = _resolve(args.default_file, cfg.get('default_file'), None, None)
    else:
        CONFIG_DIR = os.path.dirname(_file)
        DEFAULT_FILE = os.path.basename(_file)
    os.makedirs(CONFIG_DIR, exist_ok=True)

    _bd = _resolve(args.backup_dir, cfg.get('backup_dir'), None, None)
    BACKUP_DIR = os.path.abspath(_bd) if _bd else os.path.join(CONFIG_DIR, '.ezconf-backups')
    BACKUP_COUNT = args.backup_count if args.backup_count is not None else int(cfg.get('backup_count', 5))

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
    _n = len(list_config_files())
    print(f'files → {CONFIG_DIR} ({_n} file{"s" if _n != 1 else ""})')
    if BACKUP_COUNT > 0:
        print(f'backup → {BACKUP_DIR} (keeping {BACKUP_COUNT})')
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
