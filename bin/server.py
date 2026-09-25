#!/usr/bin/env python3
"""
ezconf server — single listener bound to 127.0.0.1:
  http(s)://localhost:9090  static files + API
    GET  /api/v1/ping             {"boot_id", "webroot_hash", "theme", "terminal_enabled",
                                   "mkoptions_enabled", "backup_enabled", "system_backup_enabled",
                                   "nixos_target", "buttons", "terminal_current_hash",
                                   "terminal_running_hash", "terminal_config_hash",
                                   "terminal_running_config_hash"} — polled periodically by the
                                   frontend's restart/upgrade detection (initRestartWatcher() in
                                   index.html); webroot_hash covers an ezconf package upgrade
                                   itself (new HTML/CSS/JS), not just a settings change. The
                                   terminal_* fields are two independent code/config drift checks
                                   for ezconf-terminal.service, which doesn't restart alongside
                                   this process — see "Terminal-restart notification" in
                                   CLAUDE.md. Was
                                   previously pushed over a held-open /api/v1/ping-stream SSE
                                   connection instead of polled, for near-instant detection instead
                                   of up-to-one-poll-interval latency — reverted after confirming
                                   (repeatedly, with EventSource explicitly stubbed out as a
                                   control) that an EventSource reconnecting after this process
                                   restarts reliably wipes the browser's entire cookie jar for this
                                   origin as a side effect, in both headless and real windowed
                                   Chrome — a genuine browser behavior, not something in this code,
                                   but one only a persistent/reconnecting connection type
                                   triggers; plain one-shot fetch() polling never does. A dead
                                   session (this process's boot_id changed as part of an actual
                                   restart, but auth now fails — a fresh session key with no
                                   session_key_file to persist it) shows up as this endpoint itself
                                   returning 401, not a separate signal, since there's no
                                   connection to "error out" under polling
    GET  /api/v1/markdown-files          {"files": [...]} — every *.md file under NIXOS_TARGET,
                                          recursively, as relative paths (e.g.
                                          "services/nginx/README.md"), sorted
    GET  /api/v1/markdown-files/content  {"content"} for one of those files, by ?name=<relative path>
    GET  /api/v1/files            lists the config files (tabs) and folders in CONFIG_DIR
    GET  /api/v1/file             serves a resolved config file's raw JSON content
    POST /api/v1/file/save        writes a config file (backs up first); creates it if new
    GET  /api/v1/backups          lists backups for a config file
    GET  /api/v1/backup/content   serves a backup file's raw JSON content
    POST /api/v1/file/delete      deletes a whole config file (zero files afterward is fine)
    POST /api/v1/file/rename      renames/moves a config file (same op — moving between
                                   subfolders is just a path change; also how a file is
                                   disabled/re-enabled, by renaming to/from NAME.json.disabled —
                                   there's no dedicated file/disable|enable endpoint)
    POST /api/v1/folder/create    creates an (initially empty) subfolder under CONFIG_DIR
    POST /api/v1/folder/delete    deletes a subfolder and everything in it
    POST /api/v1/folder/disable   disables a whole subfolder (renamed to .NAME.disabled, so
                                   json2nix.nix's walk skips it, same as dotdirs)
    POST /api/v1/folder/enable    re-enables a subfolder disabled via folder/disable
    GET  /api/v1/system-export    zips up the whole NIXOS_TARGET tree (not just CONFIG_DIR) —
                                   flake.nix, flake.lock, etc. — skipping symlinks always, plus
                                   dotfiles/dotdirs by default (configurable via
                                   system_export_exclude_dotfiles/system_export_exclude in TOML;
                                   see _iter_system_export_files())
    POST /api/v1/system-import    writes a zip's files (body) into NIXOS_TARGET, overwriting any
                                   existing file of the same name, and deletes any in-scope file
                                   (see _iter_system_export_files()) the zip doesn't mention, so
                                   the tree ends up matching the zip's state rather than just
                                   merging into it; skips dotfile/dotdir entries and rejects path
                                   traversal (see _is_disallowed_import_entry()). Backs up the
                                   current tree first via backup_system() (a no-op when system
                                   backups are disabled) — every import is a destructive
                                   overwrite, so this is exactly the moment a "before" snapshot
                                   is worth having
    GET  /api/v1/system-backups   lists existing system backups (see backup_system()), newest
                                   first — no endpoint creates one directly; every backup is made
                                   automatically, by system-import/system-backup/restore, right
                                   before either overwrites something
    POST /api/v1/system-backup/restore  applies a backup zip already in SYSTEM_BACKUP_DIR straight
                                   to NIXOS_TARGET, by ?name=<filename> — identical write/delete
                                   logic and auto-backup-first safety net as system-import (see
                                   _restore_system_zip()), differing only in where the zip bytes
                                   come from (a file already on disk, not a fresh upload)
    POST /api/v1/terminal/restart  runs `systemctl restart ezconf-terminal.service` directly from
                                   this process — the single restart mechanism #term-restart-btn
                                   (index.html) uses, independent of the terminal's own shell, so
                                   it works even if the shell is wedged or the panel's never been
                                   opened. Linux/systemd only (501 elsewhere)

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
  Run terminal.py separately. Pass --terminal-port (or set terminal_port in TOML) -- matching
  what terminal.py itself is configured with -- to enable the panel and proxy /terminal through
  to it (see StaticHandler._proxy_terminal()). terminal.py always binds 127.0.0.1 regardless of
  --listen; the browser only ever talks to this process's own port.

Auth:
  --auth auto     (default) pam if available, else custom
  --auth custom   username/password from ezconf.toml (requires "username" and "password")
  --auth pam      system username + password via PAM (requires python-pam)

Config file (ezconf.toml):
  file, default_file, webroot, auth, terminal_port, session_key_file, cert, key, username,
  password, allowed_users, mkoptions, nixos_target, ports.web, backup_dir, backup_count,
  system_backup_dir, system_backup_count,
  buttons (list of [[buttons]] tables: label, command, save_first, clear_first, static —
  shown in the terminal panel alongside any services.ezconf.buttons defined in a config file)
"""
import argparse
import datetime
import email.utils
import gzip
import hashlib
import http.client
import http.server
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import zipfile
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
TERMINAL_SCRIPT  = None          # path to the terminal.py currently on disk; set by terminal_script in TOML
TERMINAL_CURRENT_HASH = ''       # hash of TERMINAL_SCRIPT, computed once at startup — see _ping_payload()
TERMINAL_CONFIG_HASH = ''        # hash of the config values terminal.py itself reads, computed once at
                                  # startup from this process's own cfg — see _ping_payload() and
                                  # terminal.py's own CONFIG_HASH (must use the identical key list/formula)
THEME            = 'nixos'       # ui theme: nixos, dark, light
EZCONF_MODE      = None          # None or 'install'; baked into index.html on load — shows
                                  # install-mode buttons in their own row and greys out ordinary
                                  # ones; set by mode in TOML (deploy-time, not user-toggleable)
LOGIN_USER       = ''            # custom auth username
LOGIN_PASS       = ''            # custom auth password
MKOPTIONS_CMD    = None          # path to ezconf-mkoptions binary; enables /api/v1/autocomplete/update
NIXOS_TARGET     = '/etc/nixos'  # flake path passed as TARGET to mkoptions
TRUSTED_HOSTS    = set()         # extra hostnames allowed by _valid_host; set by trusted_hosts in TOML
BIND_ADDR        = '127.0.0.1'   # IP address to listen on; set by listen in TOML
CA_FILE          = None          # path to CA cert served at /download-ca; set by --generate-ca or ca_file in TOML
BACKUP_DIR       = None          # directory for configuration.json backups; set by --backup-dir or backup_dir in TOML (default: <config dir>/.ezconf-backups)
BACKUP_COUNT     = 5             # number of backups to keep; 0 disables backups; set by --backup-count or backup_count in TOML
SYSTEM_BACKUP_DIR   = None       # directory for whole-NIXOS_TARGET zip backups; set by --system-backup-dir or
                                  # system_backup_dir in TOML (default: <config dir>/.ezconf-system-backups)
SYSTEM_BACKUP_COUNT = 5          # number of system backups to keep; 0 disables the feature; set by
                                  # --system-backup-count or system_backup_count in TOML
STATIC_BUTTONS   = []            # terminal panel buttons from [[buttons]] in TOML (deploy-time,
                                  # not tied to any config file/tab); see services.ezconf.buttons
                                  # in modules/ezconf.nix, which is what generates this TOML
SYSTEM_EXPORT_EXCLUDE_DOTFILES = True                      # blanket dotfile/dotdir skip in
                                                            # _iter_system_export_files(); set by
                                                            # system_export_exclude_dotfiles in TOML
SYSTEM_EXPORT_EXCLUDE = set()                              # extra basenames skipped by
                                                            # _iter_system_export_files(); set by
                                                            # system_export_exclude in TOML

# Short hostname (domain stripped, same convention as generate-nixos-data.py's own
# system_hostname), used as the default basename for exported zips (exportAll()/exportSystem())
# so a download is identifiable by which machine it came from rather than a generic "ezconf"/
# "nixos" label. Doesn't depend on any config/args, so unlike WEBROOT_HASH etc. it's computed
# immediately rather than deferred to __main__.
HOSTNAME = socket.gethostname().split('.')[0]

_SESSION_KEY = secrets.token_hex(32)
# Login brute-force throttling: per-source-IP failed-attempt tracking, checked before touching
# validate_credentials() at all (so a locked-out IP doesn't even trigger a PAM call). Not
# persisted across a restart -- a restart is already a real barrier of its own, and this only
# needs to slow down sustained guessing within one process's uptime, not survive a reboot.
# _LOGIN_LOCK guards the dict since ThreadingHTTPServer runs every request on its own thread.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300       # failures older than this no longer count against the limit
_LOGIN_FAILURES = {}             # ip -> [failure timestamps within the current window]
_LOGIN_LOCK = threading.Lock()
# Fresh every process start, unlike _SESSION_KEY (which can persist across restarts via
# --session-key-file so logins survive a service restart) — this is deliberately *not*
# persisted, since its only job is letting the frontend's periodic /api/v1/ping poll notice the
# process restarted (e.g. nixos-rebuild switch restarting ezconf.service) so it can offer to
# reload and pick up fresh server-injected state (STATIC_BUTTONS, THEME, etc.).
BOOT_ID = secrets.token_hex(8)
# Content hash of the static frontend files (see _compute_webroot_hash(), computed once WEBROOT
# is finalized, near the bottom of __main__) — unlike BOOT_ID, this is *not* different on every
# restart, only when the actual HTML/CSS genuinely changed (e.g. an ezconf package upgrade).
# initRestartWatcher() treats a mismatch here as needing a real reload, same as a settings change
# — a restart alone doesn't necessarily mean anything the frontend serves actually changed, but a
# real upgrade always does, and unlike a settings change there's no way to apply it live at all.
WEBROOT_HASH = ''
# Lazily-populated cache of (raw_bytes, gzip_bytes) for static frontend files served via
# _serve_static_gzip()/_serve_index() — keyed by absolute path. Safe to compute once and reuse
# for the life of the process: these files don't change while it's running (a real change only
# ever arrives via a restart, same assumption WEBROOT_HASH above already relies on).
_STATIC_GZIP_CACHE = {}


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
    """Return True if username/password are valid. secrets.compare_digest (not ==) for both,
    evaluated unconditionally rather than short-circuited with `and` -- a plain == returns as
    soon as it hits the first differing byte, and `and` skips the password check entirely on a
    wrong username, both of which leak timing information an attacker could use to narrow down
    a guess. Neither is a practical attack over a real network, but there's no reason to accept
    the risk when the constant-time version costs nothing."""
    if AUTH_MODE == 'custom':
        user_ok = secrets.compare_digest(username, LOGIN_USER)
        pass_ok = secrets.compare_digest(password, LOGIN_PASS)
        return user_ok and pass_ok
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
    return secrets.compare_digest(_session_from_cookie(headers), _SESSION_KEY)


def _login_retry_after(ip):
    """Seconds until `ip` may attempt to log in again, or 0 if it isn't currently rate-limited.
    Also prunes failures older than LOGIN_WINDOW_SECONDS so _LOGIN_FAILURES doesn't grow
    unbounded over a long-running process."""
    now = time.time()
    with _LOGIN_LOCK:
        attempts = [t for t in _LOGIN_FAILURES.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        if attempts:
            _LOGIN_FAILURES[ip] = attempts
        else:
            _LOGIN_FAILURES.pop(ip, None)
        if len(attempts) < LOGIN_MAX_ATTEMPTS:
            return 0
        return max(1, int(LOGIN_WINDOW_SECONDS - (now - attempts[0])))


def _record_login_failure(ip):
    """Returns the attempt count within the current window after recording this one, for the
    [auth] log line in do_POST -- avoids a second lock-protected read just to report it."""
    with _LOGIN_LOCK:
        attempts = _LOGIN_FAILURES.setdefault(ip, [])
        attempts.append(time.time())
        return len(attempts)


def _clear_login_failures(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(ip, None)


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _strip_ansi(s):
    """generate-nixos-data.py colors its stderr output for terminal use; the autocomplete/update
    response is displayed in a plain HTML <pre>, which would otherwise show the raw escape codes."""
    return _ANSI_RE.sub('', s)


def _is_empty_json_array(path):
    """A missing file counts as fine here (e.g. running just `ezconf-mkoptions options` legitimately
    leaves packages.json/kernels.json untouched) — only an existing-but-empty file is suspicious."""
    try:
        with open(path) as f:
            return json.load(f) == []
    except (OSError, json.JSONDecodeError):
        return False


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


_SYSTEM_BACKUP_PREFIX = 'system-'


def backup_system():
    """Zip the whole NIXOS_TARGET tree (same file selection as /api/v1/system-export, see
    _iter_system_export_files()) into SYSTEM_BACKUP_DIR, pruning to SYSTEM_BACKUP_COUNT newest.
    Unlike backup_config(), there's no per-stem grouping to prune within — every system backup
    covers the same one tree, so pruning is just "keep the N newest files in this directory."
    Returns the new backup's filename, or None if disabled (SYSTEM_BACKUP_COUNT <= 0)."""
    if SYSTEM_BACKUP_COUNT <= 0:
        return None
    os.makedirs(SYSTEM_BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(SYSTEM_BACKUP_DIR, f'{_SYSTEM_BACKUP_PREFIX}{ts}.zip')
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(SYSTEM_BACKUP_DIR, f'{_SYSTEM_BACKUP_PREFIX}{ts}-{i}.zip')
        i += 1
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zf:
        for full, arcname in _iter_system_export_files(NIXOS_TARGET):
            zf.write(full, arcname)
    backups = [f for f in os.listdir(SYSTEM_BACKUP_DIR)
               if f.startswith(_SYSTEM_BACKUP_PREFIX) and f.endswith('.zip')]
    backups.sort(key=lambda f: os.path.getmtime(os.path.join(SYSTEM_BACKUP_DIR, f)), reverse=True)
    for old in backups[SYSTEM_BACKUP_COUNT:]:
        try:
            os.remove(os.path.join(SYSTEM_BACKUP_DIR, old))
        except OSError:
            pass
    return os.path.basename(dest)


def list_system_backups():
    items = []
    if os.path.isdir(SYSTEM_BACKUP_DIR):
        for name in os.listdir(SYSTEM_BACKUP_DIR):
            if not (name.startswith(_SYSTEM_BACKUP_PREFIX) and name.endswith('.zip')):
                continue
            st = os.stat(os.path.join(SYSTEM_BACKUP_DIR, name))
            items.append({'name': name, 'mtime': st.st_mtime, 'size': st.st_size})
    items.sort(key=lambda x: x['mtime'], reverse=True)
    return items


def resolve_system_backup_path(name):
    """Return the absolute path for a system backup zip name, or None if invalid/outside
    SYSTEM_BACKUP_DIR. Same shape as resolve_backup_path()."""
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        return None
    base = os.path.realpath(SYSTEM_BACKUP_DIR)
    full = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(full) != base or not os.path.isfile(full):
        return None
    return full


def list_markdown_files():
    """Recursively list every *.md file under NIXOS_TARGET as a relative POSIX path (e.g.
    "services/nginx/README.md"), sorted. Same dotfile/dotdir/symlink exclusions as
    _iter_system_export_files() — a dot-prefixed directory is pruned before os.walk descends
    into it (so it's never even visited, not just filtered out after), and a symlinked
    directory or file is skipped too, so this can't follow a `result`/`result-*` symlink into
    /nix/store or loop on a cycle."""
    base = os.path.realpath(NIXOS_TARGET)
    names = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not os.path.islink(os.path.join(root, d))]
        for fn in filenames:
            if fn.startswith('.') or not fn.endswith('.md'):
                continue
            full = os.path.join(root, fn)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, base).replace(os.sep, '/')
            names.append(rel)
    names.sort()
    return names


def resolve_markdown_path(name):
    """Return the absolute path for a *.md file's relative path under NIXOS_TARGET, or None if
    invalid/outside/missing. Same shape as resolve_config_path() (subpaths allowed, .. and
    absolute paths rejected)."""
    if not name or not name.endswith('.md'):
        return None
    parts = name.replace('\\', '/').split('/')
    if os.path.isabs(name) or any(p in ('', '.', '..') for p in parts):
        return None
    base = os.path.realpath(NIXOS_TARGET)
    full = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        return None
    return full


def _is_disabled_folder_name(name):
    """True for a single path segment marking a disabled folder, e.g. '.services.disabled'.

    The leading dot piggybacks on the dotdir skip that already excludes .ezconf-backups from
    both list_config_folders() and json2nix.nix's walk() — no change to json2nix.nix needed for
    a disabled folder (and everything nested inside it) to drop out of the Nix merge."""
    return name.startswith('.') and name.endswith('.disabled') and len(name) > len('..disabled')


def resolve_config_path(name):
    """Return the absolute path for a config file name inside CONFIG_DIR, or None if invalid.

    Falls back to DEFAULT_FILE when name is empty, so a caller that hasn't learned the file
    list yet still resolves to a sensible file. Does not require the file to already exist,
    since file/save uses this to create new tabs. Subpaths (e.g. "services/nginx.json") are
    allowed for organizing tabs into folders; this only keeps writes inside CONFIG_DIR by
    construction (an authenticated user here already has full terminal access to the machine,
    so this is a correctness guard against typos, not a security boundary).

    A name ending in ".json.disabled" (a file disabled by renaming it via /api/v1/file/rename —
    see list_config_files()) resolves just like its ".json" counterpart, since disabling only
    renames the file; its content is still read/saved the same way.
    """
    name = name or DEFAULT_FILE
    if not name or os.path.basename(name) == 'custom-options.json':
        return None
    if not (name.endswith('.json') or name.endswith('.json.disabled')):
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
    inside CONFIG_DIR) so backup files never show up as tabs — except a disabled folder
    (_is_disabled_folder_name()), which is still walked so its files keep showing up (as
    disabled tabs) in the UI even though json2nix.nix skips it at eval time. Also includes
    *.json.disabled files (individually disabled tabs, see /api/v1/file/rename) alongside
    their *.json siblings.
    """
    base = os.path.realpath(CONFIG_DIR)
    names = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.') or _is_disabled_folder_name(d)]
        for fn in filenames:
            if fn == 'custom-options.json':
                continue
            if not (fn.endswith('.json') or fn.endswith('.json.disabled')):
                continue
            rel = os.path.relpath(os.path.join(root, fn), base).replace(os.sep, '/')
            names.append(rel)
    names.sort()
    return names


def list_config_folders():
    """Recursively list every subdirectory under CONFIG_DIR as a relative POSIX path.

    Unlike the folders implied by list_config_files(), this also reports directories that
    don't (yet) contain any *.json file, so a folder created via /api/v1/folder/create still
    shows up as an (empty) tab group after a reload. A disabled folder (dot-prefixed, see
    _is_disabled_folder_name()) is listed too — and still walked into, so any subfolders
    nested inside it are listed as well.
    """
    base = os.path.realpath(CONFIG_DIR)
    names = []
    for root, dirs, _filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.') or _is_disabled_folder_name(d)]
        for d in dirs:
            rel = os.path.relpath(os.path.join(root, d), base).replace(os.sep, '/')
            names.append(rel)
    names.sort()
    return names


def _iter_system_export_files(root):
    """Yield (absolute_path, arcname) for every regular file under root, for the whole-tree
    /api/v1/system-export zip — deliberately unrelated to CONFIG_DIR/list_config_files(), since
    this walks the *entire* NIXOS_TARGET tree (flake.nix, flake.lock, hardware-configuration.nix,
    etc.), not just ezconf's own *.json tabs.

    Skips every dotfile/dotdir unconditionally by default (.git, .direnv, age/sops keys under
    ~/.config-style dirs, etc. are conventionally dot-prefixed — this is a deliberate, blunt
    exclusion so secrets aren't swept into a downloadable zip by default; SYSTEM_EXPORT_EXCLUDE_
    DOTFILES, set by system_export_exclude_dotfiles in TOML, lets an operator turn it off) and
    skips symlinks entirely and unconditionally, which both avoids `nix build`'s "result"/
    "result-*" symlinks (pointing into /nix/store — not config, and potentially huge or a broken
    link after a gc) and keeps this a plain tree walk with no cycle risk.

    Also skips any basename in SYSTEM_EXPORT_EXCLUDE (default empty, set by system_export_exclude
    in TOML), anywhere in the tree.
    """
    root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                        if (not SYSTEM_EXPORT_EXCLUDE_DOTFILES or not d.startswith('.'))
                        and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            if (SYSTEM_EXPORT_EXCLUDE_DOTFILES and fn.startswith('.')) or fn in SYSTEM_EXPORT_EXCLUDE:
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            arcname = os.path.relpath(full, root).replace(os.sep, '/')
            yield full, arcname


def _is_disallowed_import_entry(parts):
    """True if any path segment of a /api/v1/system-import zip entry is empty, '.', '..', or
    dot-prefixed. Two purposes at once: the dotfile/dotdir exclusion _iter_system_export_files()
    applies on export, applied symmetrically here so an imported zip can't write into .git/.ssh/
    etc. even if it happens to contain such entries (e.g. one built by another tool, not ezconf
    itself) — and, since '..' and a leading '/' (which splits to a leading '') are also caught
    here, this is what actually stops a zip-slip path-traversal entry in practice, before
    resolve_system_import_path() (a defense-in-depth backstop for anything that somehow slips
    past this — e.g. an OS-specific path quirk this doesn't anticipate — is ever reached). Either
    way the entry is skipped and reported, not treated as a harder error; a plain dotfile and a
    traversal attempt end up indistinguishable in the response, which is fine — both mean "this
    entry wasn't written," and that's the only thing that actually matters here."""
    return any(p in ('', '.', '..') or p.startswith('.') for p in parts)


def resolve_system_import_path(rel):
    """Resolve a /api/v1/system-import zip entry's relative path to an absolute path inside
    NIXOS_TARGET, or None if it would somehow still escape. In practice _is_disallowed_import_entry()
    already rejects every realistic traversal attempt (any '..' segment, or a leading '/') before
    this is ever called, so this exists purely as a defense-in-depth backstop — a correctness
    guard, not a security boundary beyond what already exists, same reasoning as
    resolve_config_path()'s equivalent check."""
    base = os.path.realpath(NIXOS_TARGET)
    full = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, full]) != base:
        return None
    return full


class _InvalidImportEntry(Exception):
    """Raised by _apply_system_zip() for an entry resolve_system_import_path() rejects -- see its
    docstring; not expected to actually happen given _is_disallowed_import_entry() already ran."""


def _apply_system_zip(zf):
    """Write every real (non-dotfile, non-traversal) entry of an open zipfile.ZipFile into
    NIXOS_TARGET, overwriting same-named files, deleting nothing on its own -- the plain-write half
    of _restore_system_zip(), which both /api/v1/system-import and /api/v1/system-backup/restore
    use (the latter via the former) to actually apply a zip's contents, differing only in where the
    zip bytes come from and whether anything gets deleted on top of this. Returns (written,
    skipped) relative-path lists."""
    plan = []
    skipped = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        rel = info.filename.replace('\\', '/')
        if _is_disallowed_import_entry(rel.split('/')):
            skipped.append(info.filename)
            continue
        target = resolve_system_import_path(rel)
        if not target:
            # _is_disallowed_import_entry() already rejects every realistic traversal attempt
            # above, so reaching this is not expected -- abort the whole import before writing
            # anything, rather than silently skip something this unanticipated.
            raise _InvalidImportEntry(info.filename)
        plan.append((target, info))
    written = []
    base = os.path.realpath(NIXOS_TARGET)
    for target, info in plan:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        written.append(os.path.relpath(target, base).replace(os.sep, '/'))
    return written, skipped


def _restore_system_zip(zf):
    """Like _apply_system_zip(), but also deletes every in-scope file (per
    _iter_system_export_files()'s own selection — symlinks, dotfiles/dotdirs, and
    system_export_exclude basenames are never in scope to begin with, so this can't touch those
    either way) that the zip doesn't mention, so the tree actually ends up matching the zip's
    state instead of just merging into it. Used by both /api/v1/system-backup/restore (a zip
    already sitting in SYSTEM_BACKUP_DIR) and /api/v1/system-import (a freshly uploaded one) --
    import used to stay a non-destructive merge (_apply_system_zip() alone) on the reasoning that
    an arbitrary uploaded zip isn't necessarily built with a full export's own file selection, e.g.
    someone uploading just one file to patch a single thing in. Reverted: the surprising case
    turned out to be the opposite one -- importing your own System Export zip (a full snapshot,
    same file selection a backup has) and having it *not* replace the tree the way restoring the
    exact same kind of zip does. Both auto-backup the current tree first (see backup_system()) for
    the same reason either way: this is a genuinely destructive overwrite. Returns (written,
    skipped, removed)."""
    written, skipped = _apply_system_zip(zf)
    written_set = set(written)
    removed = []
    for full, arcname in _iter_system_export_files(NIXOS_TARGET):
        if arcname in written_set:
            continue
        try:
            os.remove(full)
            removed.append(arcname)
        except OSError:
            pass
    return written, skipped, removed


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


def _compute_webroot_hash():
    """WEBROOT_HASH — the static files that actually make up the served frontend (skipping the
    xterm.js addons and autocomplete data, which don't affect ezconf's own behavior). Called once,
    after WEBROOT is finalized, in __main__ — not on every request, since these files don't
    change while this process is running (a real change only ever arrives via a restart)."""
    h = hashlib.sha256()
    for name in ('index.html', 'style.css', 'theme-nixos.css', 'theme-dark.css', 'theme-light.css'):
        try:
            with open(os.path.join(WEBROOT, name), 'rb') as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()[:16]


def _compute_file_hash(path):
    """Same truncated-sha256 convention as _compute_webroot_hash(), for a single file — used for
    TERMINAL_CURRENT_HASH (see there). Returns '' if path is unset or unreadable, same as an
    ordinary "nothing to compare against" case rather than an error."""
    if not path:
        return ''
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ''


def _terminal_running_status():
    """Best-effort fetch of the *actually running* terminal.py's own SELF_HASH/CONFIG_HASH, via
    its /terminal/hash status endpoint (loopback only, see terminal.py) -- not the WS 'ready'
    message, which only ever arrives once a client has the terminal panel open. Included in
    _ping_payload() so initRestartWatcher() can keep the restart notification accurate even
    when the panel is closed (previously: reloading the GUI reset the frontend's in-memory
    _terminalRunningHash to null, and nothing repopulated it unless the panel happened to be
    open, silently hiding a notification that was still genuinely true). Returns {} on any
    failure -- terminal.py not up yet, a slow response, TERMINAL_PORT unset -- so a transient
    miss just leaves this one ping tick's fields absent rather than reporting a wrong hash."""
    if not TERMINAL_PORT:
        return {}
    try:
        conn = http.client.HTTPConnection('127.0.0.1', TERMINAL_PORT, timeout=2)
        try:
            conn.request('GET', '/terminal/hash', headers={'Cookie': f'ezconf_session={_SESSION_KEY}'})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                return {}
            return json.loads(data)
        finally:
            conn.close()
    except Exception:
        return {}


def _ping_payload():
    """GET /api/v1/ping's whole response — the fields initRestartWatcher() (index.html) polls and
    compares against what index.html was actually templated with at load time, to tell a real
    restart (something here differs) apart from a process restart that changed nothing the
    frontend cares about."""
    _running = _terminal_running_status()
    return {
        'boot_id': BOOT_ID,
        'webroot_hash': WEBROOT_HASH,
        'theme': THEME,
        'terminal_enabled': bool(TERMINAL_PORT),
        'mkoptions_enabled': bool(MKOPTIONS_CMD),
        'backup_enabled': BACKUP_COUNT > 0,
        'system_backup_enabled': SYSTEM_BACKUP_COUNT > 0,
        'nixos_target': NIXOS_TARGET,
        'buttons': STATIC_BUTTONS,
        'terminal_current_hash': TERMINAL_CURRENT_HASH,
        'terminal_running_hash': _running.get('hash'),
        'terminal_config_hash': TERMINAL_CONFIG_HASH,
        'terminal_running_config_hash': _running.get('config_hash'),
    }


def _read_login_page(error=''):
    if ALLOWED_USERS:
        # A <select> here (tried first) can only ever submit one of these exact values, but
        # browsers' saved-password heuristics look for an <input> paired with the password field --
        # a <select> isn't recognized as a username field at all, so Brave/Chrome saved the password
        # with no username attached. A plain <input> with a <list>-linked <datalist> (tried second)
        # fixed that but broke autofill in a different way: Chromium suppresses its own saved-
        # password suggestion dropdown on any input that has a `list` attribute at all, since the
        # datalist popup and the browser's native autofill popup would otherwise compete for the
        # same space -- confirmed this reproduces on any <input list=...> paired with a password
        # field, on any site, not something specific to this app. A hand-rolled dropdown wired to
        # the input's own focus/typing (tried third) hit the exact same problem one level up:
        # screenshotted on a real login, our dropdown and the browser's own suggestion (both opening
        # on focus) stacked on top of each other. There's no documented way to make a suggestion UI
        # and the browser's native autofill popup coexist at that same moment -- checked Chromium's
        # own password-forms guidance and web.dev's sign-in-form best practices, neither covers it --
        # so this version doesn't try: the field is an entirely ordinary <input> that autofill never
        # has any reason to react to, and the picker is a separate, explicit action (a small arrow
        # button) rather than anything tied to focusing or typing in the field at all. None of this
        # is a security boundary either way: user_allowed() re-checks the submitted username against
        # ALLOWED_USERS server-side regardless of how it arrived, since anyone can POST /login with
        # an arbitrary username directly, with or without a picker in front of it.
        users_json = json.dumps(sorted(ALLOWED_USERS))
        username_field = f'''<div class="login-suggest-wrap">
      <input id="u" name="username" type="text" autocomplete="username" autofocus>
      <button type="button" class="login-suggest-arrow" id="u-arrow" tabindex="-1" aria-label="Choose a username">&#9662;</button>
      <div class="login-suggest hidden" id="u-suggest"></div>
    </div>
    <script>
    (function() {{
      var users = {users_json};
      var inp = document.getElementById('u');
      var arrow = document.getElementById('u-arrow');
      var list = document.getElementById('u-suggest');
      function render() {{
        list.innerHTML = '';
        users.forEach(function(u) {{
          var item = document.createElement('div');
          item.className = 'login-suggest-item';
          item.textContent = u;
          item.addEventListener('mousedown', function(e) {{
            e.preventDefault();
            inp.value = u;
            list.classList.add('hidden');
          }});
          list.appendChild(item);
        }});
      }}
      render();
      // The only way this ever opens -- never on the input's own focus or typing, both of which
      // the browser's native autofill also reacts to on this same field (see the comment above).
      // An explicit click is a separate, deliberate action that can't collide with that.
      arrow.addEventListener('mousedown', function(e) {{
        e.preventDefault();
        list.classList.toggle('hidden');
      }});
      document.addEventListener('mousedown', function(e) {{
        if (e.target !== arrow && !list.contains(e.target)) list.classList.add('hidden');
      }});
    }})();
    </script>'''
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


def _recv_until_double_crlf(sock, chunk=4096):
    """Read from a raw socket until the HTTP header terminator; returns (header_bytes including
    the terminator, any already-read bytes past it) -- used by _proxy_terminal() to forward
    terminal.py's handshake response without needing http.client on either leg."""
    buf = b''
    while b'\r\n\r\n' not in buf:
        data = sock.recv(chunk)
        if not data:
            return b'', b''
        buf += data
    idx = buf.find(b'\r\n\r\n') + 4
    return buf[:idx], buf[idx:]


def _pipe(src, dst):
    """One direction of _proxy_terminal()'s byte relay -- runs until src is closed or errors,
    then shuts dst down too. Without this, whichever side dies first (e.g. terminal.py itself,
    restarted via "Restart Terminal") left the *other* direction's blocking recv() with no way to
    ever learn its peer is gone -- confirmed as a real, reproduced hang, since a browser sitting
    on an idle WS connection has no reason to send anything that would otherwise surface the dead
    backend. Deliberately shutdown(SHUT_RDWR), not just close(): closing a socket from a thread
    other than the one blocked in recv() on it is not reliably enough to unblock that call --
    confirmed empirically (a plain dst.close() here left the peer thread's recv() hanging
    indefinitely on macOS, even though the fd was genuinely closed and the TCP connection had
    already gone to CLOSE_WAIT). shutdown() is the actual POSIX-documented way to force a
    concurrent blocking call on the same socket to return; close() still runs after to release
    the fd once nothing is blocked on it anymore."""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            dst.close()
        except OSError:
            pass


class StaticHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        self.directory = WEBROOT
        return super().translate_path(path)

    def end_headers(self):
        # Files served from WEBROOT often come from the Nix store, where mtimes are normalized
        # to a fixed value for build reproducibility — Last-Modified-based conditional caching
        # would then treat genuinely new content as unchanged. Disable caching outright instead.
        # /autocomplete/* opts out of this (see _serve_autocomplete()): those files live in a
        # real writable directory and get a genuinely fresh mtime every time ezconf-mkoptions
        # regenerates them, so Last-Modified-based caching is trustworthy there.
        if not getattr(self, '_no_default_cache_control', False):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

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
            ip = self.client_address[0]
            retry_after = _login_retry_after(ip)
            if retry_after:
                print(f'[auth] {ip} rate-limited, retry in {retry_after}s')
                self._deny(f'Too many failed attempts. Try again in {retry_after}s.')
                return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8', errors='replace')
            params = {k: v[0] for k, v in parse_qs(body).items()}
            username = params.get('username', '')
            password = params.get('password', '')
            if validate_credentials(username, password):
                _clear_login_failures(ip)
                print(f'[auth] login succeeded for {username!r} from {ip}')
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie', f'ezconf_session={_SESSION_KEY}; HttpOnly; SameSite=Strict; Path=/')
                self.end_headers()
            else:
                count = _record_login_failure(ip)
                print(f'[auth] login failed for {username!r} from {ip} ({count}/{LOGIN_MAX_ATTEMPTS} attempts)')
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
        if parsed.path == '/api/v1/file/save':
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
        elif parsed.path == '/api/v1/file/delete':
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
                os.remove(target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/file/rename':
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
        elif parsed.path == '/api/v1/folder/create':
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
        elif parsed.path == '/api/v1/folder/delete':
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
                shutil.rmtree(target)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/folder/disable':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                target = resolve_folder_path(body.get('folder', ''))
                if not target or not os.path.isdir(target) or _is_disabled_folder_name(os.path.basename(target)):
                    resp = b'{"error":"invalid folder name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                dst = os.path.join(os.path.dirname(target), '.' + os.path.basename(target) + '.disabled')
                if os.path.exists(dst):
                    resp = b'{"error":"a disabled version already exists"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.rename(target, dst)
                rel = os.path.relpath(dst, os.path.realpath(CONFIG_DIR)).replace(os.sep, '/')
                resp = json.dumps({'ok': True, 'folder': rel}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/folder/enable':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                folder = body.get('folder', '')
                target = resolve_folder_path(folder)
                base_name = os.path.basename(target) if target else ''
                if not target or not os.path.isdir(target) or not _is_disabled_folder_name(base_name):
                    resp = b'{"error":"invalid folder name"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                inner = base_name[1:-len('.disabled')]
                dst = os.path.join(os.path.dirname(target), inner)
                if os.path.exists(dst):
                    resp = b'{"error":"a folder already exists at the destination"}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                os.rename(target, dst)
                rel = os.path.relpath(dst, os.path.realpath(CONFIG_DIR)).replace(os.sep, '/')
                resp = json.dumps({'ok': True, 'folder': rel}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/autocomplete/update':
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
                    [MKOPTIONS_CMD, '-v', '-o', out_dir],
                    env=env, capture_output=True, text=True, timeout=600
                )
                output = _strip_ansi((result.stdout + result.stderr).strip())
                if result.returncode == 0:
                    # exit 0 doesn't mean the data is actually usable — a total eval failure for
                    # one file (e.g. a host's configuration.nix importing a missing
                    # hardware-configuration.nix) still exits 0 with that file written as [].
                    # Checking the files directly is simpler and more reliable than trying to
                    # parse generate-nixos-data.py's progress output for signs of trouble.
                    empty = [
                        f for f in ('options.json', 'packages.json', 'kernels.json')
                        if _is_empty_json_array(os.path.join(out_dir, f))
                    ]
                    if empty:
                        msg = f"{', '.join(empty)} came back empty — see output below"
                        resp = json.dumps({'error': f"{msg}\n\n{output}" if output else msg}).encode()
                        self.send_response(500)
                    else:
                        resp = b'{"ok":true}'
                        self.send_response(200)
                else:
                    resp = json.dumps({'error': output or 'unknown error'}).encode()
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
        elif parsed.path == '/api/v1/terminal/restart':
            # Runs systemctl directly from server.py's own process, independent of the terminal's
            # own shell -- so this still works even if the running shell is wedged, or the panel's
            # never been opened. systemctl only exists on Linux, so this is a no-op elsewhere.
            if not TERMINAL_PORT:
                resp = b'{"error":"terminal not enabled"}'
                self.send_response(501)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return
            if not sys.platform.startswith('linux'):
                resp = b'{"error":"systemctl restart is only available on Linux"}'
                self.send_response(501)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return
            try:
                result = subprocess.run(
                    ['systemctl', 'restart', 'ezconf-terminal.service'],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    resp = b'{"ok":true}'
                    self.send_response(200)
                else:
                    output = (result.stdout + result.stderr).strip() or 'unknown error'
                    resp = json.dumps({'error': output}).encode()
                    self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except subprocess.TimeoutExpired:
                resp = b'{"error":"timed out after 30s"}'
                self.send_response(504)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/system-import':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                # Auto-backup the current tree before overwriting anything with it -- an import is
                # exactly the moment something is about to be destructively replaced, so this is
                # the one place a "before" snapshot is actually useful. No-ops (returns None) when
                # system backups are disabled (SYSTEM_BACKUP_COUNT = 0), same as backup_config()
                # silently no-ops when BACKUP_COUNT = 0.
                backup_system()
                # Uses _restore_system_zip(), same as system-backup/restore below -- an import
                # deletes any in-scope file the zip doesn't mention too, so the tree actually
                # matches what was imported rather than just merging into whatever was already
                # there. See _restore_system_zip()'s own docstring for why this used to be the
                # non-destructive _apply_system_zip() instead, and why that was reverted.
                with zipfile.ZipFile(io.BytesIO(body)) as zf:
                    written, skipped, removed = _restore_system_zip(zf)
                resp = json.dumps({'ok': True, 'written': written, 'skipped': skipped, 'removed': removed}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except zipfile.BadZipFile:
                resp = b'{"error":"not a valid zip file"}'
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except _InvalidImportEntry as e:
                resp = json.dumps({'error': 'invalid entry path: ' + str(e)}).encode()
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        elif parsed.path == '/api/v1/system-backup/restore':
            try:
                qs = parse_qs(parsed.query)
                target = resolve_system_backup_path(qs.get('name', [''])[0])
                if not target:
                    self.send_error(400); return
                # Same auto-backup-before-overwrite as system-import above -- restoring a backup
                # is itself an import (of a zip that happens to already be sitting on disk rather
                # than freshly uploaded), so it gets the same "snapshot what's about to be
                # replaced" treatment. Uses _restore_system_zip() rather than _apply_system_zip(),
                # though -- unlike a plain import, a restore also deletes anything not in the
                # backup, so the tree actually matches the backup afterward instead of just having
                # the backup's files merged into whatever was already there.
                backup_system()
                with zipfile.ZipFile(target) as zf:
                    written, skipped, removed = _restore_system_zip(zf)
                resp = json.dumps({'ok': True, 'written': written, 'skipped': skipped, 'removed': removed}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except zipfile.BadZipFile:
                resp = b'{"error":"backup is not a valid zip file"}'
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except _InvalidImportEntry as e:
                resp = json.dumps({'error': 'invalid entry path: ' + str(e)}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    _PUBLIC_PATHS = {'/login.html'}

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
        if parsed.path == '/style.css' or (
                parsed.path.startswith('/theme-') and parsed.path.endswith('.css')):
            # Public (pre-auth, for the login page) and worth gzipping: style.css alone runs
            # ~46KB, served fresh on every page load since WEBROOT files are no-store.
            self._serve_static_gzip(parsed.path.lstrip('/')); return
        if parsed.path in self._PUBLIC_PATHS:
            super().do_GET()
            return
        if not check_auth(self.headers):
            self._deny(); return
        if (parsed.path == '/terminal' and TERMINAL_PORT and
                self.headers.get('Upgrade', '').lower() == 'websocket'):
            self._proxy_terminal(); return
        if parsed.path.rstrip('/') in ('', '/index.html'):
            self._serve_index(); return
        if parsed.path == '/api/v1/ping':
            # Polled periodically by initRestartWatcher() (index.html) — see this file's own
            # module docstring above for why this is polling rather than a held-open SSE
            # connection (an earlier /api/v1/ping-stream, removed). A dead session (cookie no
            # longer valid) never reaches this handler at all — check_auth()/_deny() above
            # already reject it with 401 before this branch runs, same as any other endpoint.
            data = json.dumps(_ping_payload()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == '/api/v1/markdown-files':
            try:
                data = json.dumps({'files': list_markdown_files()}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
            return
        if parsed.path == '/api/v1/markdown-files/content':
            qs = parse_qs(parsed.query)
            target = resolve_markdown_path(qs.get('name', [''])[0])
            if not target:
                self.send_error(400); return
            try:
                with open(target, 'r', encoding='utf-8') as f:
                    payload = {'content': f.read()}
            except Exception as e:
                self.send_error(500, str(e)); return
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
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
        if parsed.path == '/api/v1/system-export':
            try:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for full, arcname in _iter_system_export_files(NIXOS_TARGET):
                        zf.write(full, arcname)
                data = buf.getvalue()
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', f'attachment; filename="{HOSTNAME}-system.zip"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
            return
        if parsed.path == '/api/v1/system-backups':
            try:
                data = json.dumps({'backups': list_system_backups()}).encode()
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
        if parsed.path == '/api/v1/backup/content':
            qs = parse_qs(parsed.query)
            target = resolve_backup_path(qs.get('name', [''])[0])
            if not target:
                self.send_error(400); return
            self._serve_raw(target); return
        # A resolved config file's content and custom-options.json live in CONFIG_DIR, not WEBROOT
        if parsed.path == '/api/v1/file':
            qs = parse_qs(parsed.query)
            target = resolve_config_path(qs.get('file', [None])[0])
            if not target:
                self.send_error(400); return
            self._serve_raw(target); return
        if parsed.path == '/custom-options.json':
            self._serve_raw(os.path.join(CONFIG_DIR, 'custom-options.json')); return
        # autocomplete files: AUTOCOMPLETE_DIR when set, else WEBROOT/autocomplete/ (same default
        # as MKOPTIONS_CMD's own out_dir) — served via _serve_autocomplete() for conditional GET
        # + gzip, since options.json/packages.json can run into several MB on a large flake
        if parsed.path.startswith('/addons/'):
            # xterm.js + addons: only ever requested once logged in (terminal panel), but
            # xterm.js/xterm-addon-webgl.js alone run ~735KB combined — worth gzipping.
            self._serve_static_gzip(parsed.path.lstrip('/')); return
        if parsed.path.startswith('/autocomplete/'):
            rel = os.path.normpath(parsed.path[len('/autocomplete/'):]).lstrip('/')
            base = AUTOCOMPLETE_DIR or os.path.join(WEBROOT, 'autocomplete')
            self._serve_autocomplete(os.path.join(base, rel)); return
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

    def _serve_autocomplete(self, path):
        """Conditional-GET + gzip serving for /autocomplete/*.json. Unlike _serve_raw() (always
        no-store, see end_headers()), these files get real caching: options.json alone can be
        several MB on a large flake, re-downloaded on every page load/reload otherwise, and their
        mtime is genuinely meaningful (they're rewritten by ezconf-mkoptions, not Nix-store
        artifacts with a frozen mtime)."""
        try:
            mtime = int(os.stat(path).st_mtime)
        except OSError:
            self.send_error(404); return
        last_modified = self.date_time_string(mtime)
        ims = self.headers.get('If-Modified-Since')
        if ims:
            try:
                ims_dt = email.utils.parsedate_to_datetime(ims)
                if ims_dt.tzinfo is None:
                    ims_dt = ims_dt.replace(tzinfo=datetime.timezone.utc)
                if int(ims_dt.timestamp()) >= mtime:
                    self._no_default_cache_control = True
                    self.send_response(304)
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Last-Modified', last_modified)
                    self.end_headers()
                    return
            except (TypeError, ValueError, OverflowError):
                pass
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(404); return
        gzipped = 'gzip' in self.headers.get('Accept-Encoding', '')
        if gzipped:
            data = gzip.compress(data)
        self._no_default_cache_control = True
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Last-Modified', last_modified)
        if gzipped:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static_gzip(self, rel_path):
        """Gzip-capable serving for a handful of sizeable static WEBROOT files (style.css,
        theme-*.css, addons/*) that don't go through SimpleHTTPRequestHandler's own static
        serving, which has no compression at all. Cache-Control stays no-store (see
        end_headers()) — these files really can change across a restart, unlike /autocomplete/*'s
        genuinely-fresh mtimes, so no attempt at conditional-GET caching here, just compression.
        _STATIC_GZIP_CACHE holds both encodings per path, built on first request."""
        path = os.path.join(WEBROOT, rel_path)
        cached = _STATIC_GZIP_CACHE.get(path)
        if cached is None:
            try:
                with open(path, 'rb') as f:
                    raw = f.read()
            except OSError:
                self.send_error(404); return
            cached = (raw, gzip.compress(raw))
            _STATIC_GZIP_CACHE[path] = cached
        raw, gzipped_data = cached
        use_gzip = 'gzip' in self.headers.get('Accept-Encoding', '')
        data = gzipped_data if use_gzip else raw
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(path))
        if use_gzip:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self):
        try:
            # Every %%EZCONF_...%% substitution below comes from a global finalized once at
            # startup (config parsing, or a once-computed hash/id), so the rendered output is
            # constant for the life of this process — same "only a restart changes it" assumption
            # as _STATIC_GZIP_CACHE's other entries. '__index__' is a synthetic key (not a real
            # filesystem path) since this content is templated, not read verbatim from disk.
            cached = _STATIC_GZIP_CACHE.get('__index__')
            if cached is None:
                terminal_scripts = (
                    '<link rel="stylesheet" href="addons/xterm.css">\n'
                    '<script src="addons/xterm.js"></script>\n'
                    '<script src="addons/xterm-addon-fit.js"></script>\n'
                    '<script src="addons/xterm-addon-webgl.js"></script>'
                ) if TERMINAL_PORT else ''
                content = (open(os.path.join(WEBROOT, 'index.html')).read()
                    .replace('%%EZCONF_TERMINAL_SCRIPTS%%', terminal_scripts)
                    .replace('%%EZCONF_TERMINAL%%', 'true' if TERMINAL_PORT else 'false')
                    .replace('%%EZCONF_THEME%%', THEME)
                    .replace('%%EZCONF_MKOPTIONS%%', 'true' if MKOPTIONS_CMD else 'false')
                    .replace('%%EZCONF_BACKUP%%', 'true' if BACKUP_COUNT > 0 else 'false')
                    .replace('%%EZCONF_SYSTEM_BACKUP%%', 'true' if SYSTEM_BACKUP_COUNT > 0 else 'false')
                    .replace('%%EZCONF_MODE%%', json.dumps(EZCONF_MODE))
                    .replace('%%EZCONF_NIXOS_TARGET%%', NIXOS_TARGET.replace('\\', '\\\\').replace("'", "\\'"))
                    .replace('%%EZCONF_HOSTNAME%%', HOSTNAME.replace('\\', '\\\\').replace("'", "\\'"))
                    .replace('%%EZCONF_BOOT_ID%%', BOOT_ID)
                    .replace('%%EZCONF_WEBROOT_HASH%%', WEBROOT_HASH)
                    .replace('%%EZCONF_TERMINAL_CURRENT_HASH%%', TERMINAL_CURRENT_HASH)
                    .replace('%%EZCONF_TERMINAL_CONFIG_HASH%%', TERMINAL_CONFIG_HASH)
                    # Escape "</" so a command/label containing "</script>" can't prematurely close
                    # the <script> block this gets embedded into as a JS array literal.
                    .replace('%%EZCONF_BUTTONS%%', json.dumps(STATIC_BUTTONS).replace('</', '<\\/'))
                )
                raw = content.encode('utf-8')
                cached = (raw, gzip.compress(raw))
                _STATIC_GZIP_CACHE['__index__'] = cached
            raw, gzipped_data = cached
            use_gzip = 'gzip' in self.headers.get('Accept-Encoding', '')
            data = gzipped_data if use_gzip else raw
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            if use_gzip:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))

    def _proxy_terminal(self):
        """Relay a /terminal WebSocket upgrade straight through to terminal.py's own listener on
        127.0.0.1:TERMINAL_PORT (which only ever binds to loopback -- see terminal.py's own
        BIND_ADDR), so the browser only ever needs to trust *this* process's certificate. A
        separate wss://host:TERMINAL_PORT connection straight to terminal.py would be a different
        origin (port included), needing its own certificate-trust decision the browser has no way
        to prompt for over a raw WebSocket handshake -- see the comment on TERMINAL_PORT in
        index.html. Both legs of this relay stay plain HTTP: browser<->server.py is whatever
        scheme this process itself is running (TLS-wrapped already if HTTPS is on, by the time
        request handling reaches here), and server.py<->terminal.py never leaves the loopback
        interface, so there's nothing to encrypt on that leg regardless.

        This never parses WebSocket frames -- it's a pure byte relay, so PTY resize/reattach/
        persistence/etc. in terminal.py are completely unaffected by going through it."""
        self.close_connection = True
        try:
            backend = socket.create_connection(('127.0.0.1', TERMINAL_PORT), timeout=10)
        except OSError as e:
            self.send_error(502, f'terminal backend unreachable: {e}')
            return
        try:
            headers = dict(self.headers.items())
            headers['Host'] = f'127.0.0.1:{TERMINAL_PORT}'
            lines = [f'{self.command} {self.path} {self.request_version}']
            lines += [f'{k}: {v}' for k, v in headers.items()]
            lines += ['', '']
            backend.sendall('\r\n'.join(lines).encode('iso-8859-1'))
            # Deliberately not checking self.rfile for leftover buffered bytes past the request
            # headers here: a real WS client sends nothing more until it gets the 101 response, so
            # self.rfile's buffer is genuinely empty at this point -- and io.BufferedReader.peek()
            # performs a real (blocking) read on the raw stream when its buffer is empty, so
            # calling it here would stall the whole proxy waiting for client bytes that were never
            # coming (confirmed: this was tried first and hung every connection).
            header_bytes, leftover_from_backend = _recv_until_double_crlf(backend)
            if not header_bytes:
                self.send_error(502, 'terminal backend closed the connection')
                return
            backend.settimeout(None)
            self.connection.sendall(header_bytes)
            if leftover_from_backend:
                self.connection.sendall(leftover_from_backend)
            t = threading.Thread(target=_pipe, args=(backend, self.connection), daemon=True)
            t.start()
            _pipe(self.connection, backend)
            t.join(timeout=2)
        finally:
            try:
                backend.close()
            except OSError:
                pass

    def log_message(self, fmt, *args):
        print(f'[web]  {self.address_string()} - {fmt % args}')


def _valid_host(headers):
    # '*' in trusted_hosts disables this check entirely — accepts any Host header. Meant for
    # cases where the reachable address genuinely can't be known ahead of time (e.g. a NixOS
    # installer ISO getting a DHCP lease), where listing exact hosts isn't possible.
    if '*' in TRUSTED_HOSTS:
        return True
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
    ap.add_argument('--system-backup-dir', metavar='DIR', default=None,
                    help='directory to store whole-NIXOS_TARGET zip backups (default: <config dir>/.ezconf-system-backups)')
    ap.add_argument('--system-backup-count', metavar='N', type=int, default=None,
                    help='number of system backups to keep; 0 disables the feature (default: 5)')
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
    if 'system_export_exclude_dotfiles' in cfg:
        SYSTEM_EXPORT_EXCLUDE_DOTFILES = bool(cfg['system_export_exclude_dotfiles'])
    if 'system_export_exclude' in cfg:
        SYSTEM_EXPORT_EXCLUDE = {str(n).strip() for n in (cfg.get('system_export_exclude') or []) if str(n).strip()}

    CERT_FILE = _resolve(args.cert, cfg.get('cert'), None, 'localhost.pem')
    KEY_FILE  = _resolve(args.key,  cfg.get('key'),  None, 'localhost-key.pem')
    AUTH_MODE = _resolve(args.auth, cfg.get('auth'), None, 'auto')
    THEME     = _resolve(args.theme, cfg.get('theme'), None, 'nixos')
    EZCONF_MODE = cfg.get('mode') or None
    STATIC_BUTTONS = cfg.get('buttons') or []
    _term_port = args.terminal_port or cfg.get('terminal_port')
    if _term_port:
        TERMINAL_PORT    = int(_term_port)
        TERMINAL_ENABLED = True
    TERMINAL_SCRIPT = cfg.get('terminal_script')
    TERMINAL_CURRENT_HASH = _compute_file_hash(TERMINAL_SCRIPT)
    # Raw values, not resolved/fallback-applied -- must match terminal.py's own CONFIG_HASH
    # formula exactly, key for key, since these are compared directly (see _ping_payload()).
    # Deliberately excludes `webroot` -- see the matching comment in terminal.py's __main__.
    TERMINAL_CONFIG_HASH = hashlib.sha256(json.dumps(
        {k: cfg.get(k) for k in ('terminal_port', 'session_key_file', 'shell')},
        sort_keys=True, default=str
    ).encode()).hexdigest()[:16]

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

    _sbd = _resolve(args.system_backup_dir, cfg.get('system_backup_dir'), None, None)
    SYSTEM_BACKUP_DIR = os.path.abspath(_sbd) if _sbd else os.path.join(CONFIG_DIR, '.ezconf-system-backups')
    SYSTEM_BACKUP_COUNT = (args.system_backup_count if args.system_backup_count is not None
                            else int(cfg.get('system_backup_count', 5)))

    WEBROOT_HASH = _compute_webroot_hash()

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
    if SYSTEM_BACKUP_COUNT > 0:
        print(f'system backup → {SYSTEM_BACKUP_DIR} (keeping {SYSTEM_BACKUP_COUNT})')
    if AUTH_MODE == 'custom':
        print(f'auth → custom   (username: {LOGIN_USER})')
    elif AUTH_MODE == 'pam':
        print(f'auth → PAM      (system username + password)')
    if ALLOWED_USERS:
        print(f'users → {", ".join(sorted(ALLOWED_USERS))}')
    if TERMINAL_PORT:
        print(f'term  → proxied via {scheme}://localhost:{WEB_PORT}/terminal '
              f'(backend on 127.0.0.1:{TERMINAL_PORT})')
    else:
        print(f'term  → disabled (run terminal.py and set --terminal-port)')
    web_srv.serve_forever()
