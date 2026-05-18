# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git

Do not add Claude as co-author in commit messages.

## What this is

A zero-dependency, single-page NixOS configuration editor. No build step, no framework, no package manager. The app is served by `bin/server.py` and edits a configuration JSON file specified via `--file`.

## Running

```sh
# With a config file (copy ezconf.example.toml → ezconf.toml and edit)
python3 bin/server.py

# Or pass settings as arguments (overrides ezconf.toml)
python3 bin/server.py --webroot /path/to/webroot --file /path/to/configuration.json

# Generate a self-signed cert for dev use in DIR (default: current directory)
python3 bin/server.py --generate-cert [DIR]

# Generate a local CA + server cert (used by the NixOS service)
python3 bin/server.py --generate-ca [DIR]

# Or via the Nix flake (webroot defaults to installed share dir)
nix run .#ezconf -- --file /path/to/configuration.json
```

Python dependencies: `python-pam` (optional, enables PAM auth), `cryptography` (optional, enables `--generate-cert`/`--generate-ca`), `tomli` (optional, enables TOML config on Python < 3.11 — not needed on 3.11+). All included in the flake devShell.

**Config file**: `ezconf.toml` in the working directory (or `--config FILE`). See `ezconf.example.toml`. CLI args always override the config file.

Auth: `--auth auto` (default — PAM if available, else custom), `--auth custom` (username/password from config), `--auth pam` (system username/password via python-pam). `allowed_users` in config restricts logins in PAM mode and renders a dropdown instead of a free-text username field at login. Authentication is always required.

Terminal: a separate process (`bin/terminal.py`). Pass `--terminal-port PORT` to `server.py` to enable the terminal panel; run `terminal.py --config ezconf.toml` separately on the same port.

## Generating data files

`bin/generate-nixos-data.py` (Python 3, no extra deps) generates the JSON files the editor needs. Via the flake it is exposed as `ezconf-mkoptions`:

```sh
ezconf-mkoptions                          # all files → webroot/autocomplete/
TARGET=/path/to/flake ezconf-mkoptions all myhostname
ezconf-mkoptions options                  # only autocomplete/options.json
ezconf-mkoptions packages                 # only autocomplete/packages.json
ezconf-mkoptions kernels                  # only autocomplete/kernels.json
ezconf-mkoptions --nested                 # include nested pkg sets (slow)
ezconf-mkoptions -v                       # verbose nix errors

# Or directly:
python3 bin/generate-nixos-data.py [args...]
```

Shells out to `nix eval --json`. The `TARGET` env var sets the flake path (default `/etc/nixos`). Default output is `autocomplete/` relative to CWD (override with `-o DIR`).

The generated `autocomplete/` directory belongs inside the WEBROOT so the server can serve it. Both defaults now align: `ezconf-mkoptions` writes to `webroot/autocomplete/` and the server defaults to `./webroot`. It is gitignored since it contains user-specific generated data.

## Server architecture (`bin/server.py`)

Single `ThreadingHTTPServer` bound to `127.0.0.1:9090` (or `WEB_PORT`). Serves static assets via `StaticHandler` (subclass of `SimpleHTTPRequestHandler`) and handles the API.

**API endpoints** (all require auth):
- `POST /api/v1/save-config` — writes `CONFIG_FILE`
- `POST /api/v1/update-autocomplete` — runs `MKOPTIONS_CMD` to regenerate autocomplete data; only available when `--mkoptions` is set

**File routing**: `StaticHandler.translate_path` sets `self.directory = WEBROOT`. `/configuration.json` is served from `CONFIG_FILE` (not WEBROOT). `/autocomplete/*` is served from `AUTOCOMPLETE_DIR` when set.

**Key globals**:
- `WEBROOT` — directory for static assets (set by `--webroot`)
- `CONFIG_FILE` — JSON file being edited (set by `--file`)
- `AUTOCOMPLETE_DIR` — override for autocomplete file serving (set by `--autocomplete-dir`)
- `MKOPTIONS_CMD` — path to mkoptions binary; enables the update-autocomplete endpoint
- `TERMINAL_PORT` — when set, enables the terminal panel in the frontend and points it at this port
- `THEME` — UI theme injected into `index.html`; set by `--theme` or `theme` in config (default `nixos`)
- `_SESSION_KEY` — random hex key generated at startup (or loaded from `--session-key-file`); used as the expected value of the `ezconf_session` cookie

**Auth flow**: The login form POSTs to `/login`. On success the server sets `Set-Cookie: ezconf_session=<SESSION_KEY>; HttpOnly; SameSite=Strict; Path=/`. All subsequent requests (browser and API) are authenticated by that cookie. `check_auth()` reads the `ezconf_session` cookie from the `Cookie` header and compares it to `_SESSION_KEY`.

**PAM auth**: `check_pam()` creates a fresh `pam.pam()` instance on every call — do not use a global instance. The global instance segfaults on Linux after the first `authenticate()` call due to libpam memory management.

## Terminal service (`bin/terminal.py`)

Separate process from the web server. Listens on its own port (default 9091) and handles WebSocket connections that upgrade a PTY session. `_terminal_ws()` forks the configured shell into a PTY, then bridges it over WebSocket frames. Resize messages (`{"type":"resize","cols":N,"rows":M}`) from the client call `fcntl.ioctl(TIOCSWINSZ)`. The `pty_to_ws` thread polls the master fd with `select` (0.5s timeout).

Reads the same `ezconf.toml` as the web server (`--config FILE`). The WebSocket upgrade request is authenticated via the `ezconf_session` cookie — the browser sends it automatically on the upgrade request. In the NixOS service this runs as `ezconf-terminal.service`, separate from `ezconf.service`, with `restartIfChanged = false` so terminal sessions survive `nixos-rebuild switch`.

## Frontend architecture (`webroot/index.html`)

All application logic is in the `<script>` block at the bottom (~1400 lines). No modules, no imports, no build step.

**Data model**: `config` (plain JS object mirroring `configuration.json`), `options` (array of `{path, type, description, default, example}`), `packages` (array of `{name, description}`). The `_expr` sentinel `{ _expr: "..." }` represents a raw Nix expression wherever a value would normally go.

**Key subsystems:**

| Subsystem | Functions |
|---|---|
| Path helpers | `getAtPath`, `setAtPath`, `deleteAtPath`, `traverseForSet` |
| Type inference | `typeOf`, `typeFromNix`, `isFreeformType`, `isNullableString`, `parseEnumOptions` |
| Option lookup | `findOption`, `optionSearch`, `isValidOptionPath`, `getWildcardBoundary`, `blankObjectFromOptions` — wildcard segments (`<name>`, `<n>`, `*`) match any concrete key |
| Nix default parsing | `parseNixDefault` — converts Nix expression strings to JS values; falls back to `defaultForType` |
| Add panel | `initAddPanel`, `doAdd`, `doForceAdd`, `doForceAddWithType` |
| Editor rendering | `renderEditor` → `renderObj` → `renderSection` / `renderField` / `renderArray` / `renderPkgArray` |
| Tree sidebar | `renderTree`, `renderTreeLevel` |
| Drag-and-drop | `makeDraggable`, `reorderKey` |

**`_expr` objects** appear as scalar fields toggled to raw Nix (via the `{ }` button) and as elements of package arrays (`{ _expr: "pkgs.foo" }`). `isExprPkg(v)` distinguishes the two. `renderPkgArray` handles arrays at paths ending in `systemPackages`, `packages`, `extraPackages`, `extraPlugins`, or `users.users.<name>.packages`.

**`traverseForSet`** navigates/creates intermediate path nodes. It preserves existing arrays rather than replacing with `{}`, and uses `emptyContainerFor` (consults `findOption`) to decide if missing nodes should be `[]` or `{}`.

**Autocomplete data** is fetched from `/autocomplete/options.json`, `/autocomplete/packages.json`, and `/autocomplete/kernels.json` — served from `AUTOCOMPLETE_DIR` when set, otherwise `autocomplete/` under WEBROOT.

**Rendering** is always a full re-render via `renderAll()` — no virtual DOM or diffing.

## Terminal panel

The in-page terminal uses **xterm.js 6.0.0** (`@xterm/xterm`) with `@xterm/addon-fit` 0.11.0 (layout) and `@xterm/addon-webgl` 0.19.0 (GPU rendering). All three are bundled locally in `webroot/addons/` (no CDN dependency). The WebGL addon is loaded opportunistically — if the browser doesn't support it, xterm falls back to Canvas 2D.

UMD globals: `Terminal` (class, spread directly onto `window`), `FitAddon.FitAddon` (class inside module object), `WebglAddon.WebglAddon` (same pattern). Use `new Terminal()`, `new FitAddon.FitAddon()`, `new WebglAddon.WebglAddon()`.

Key constraints:
- `ResizeObserver` on `#term-output` is debounced 200ms so `fit()` only fires once after CSS transitions settle (the panel height transition is 180ms).
- `fit()` is the only call that triggers a PTY resize — it fires `_term.onResize` → sends `{"type":"resize",...}` → server calls `TIOCSWINSZ`.
- Do not use a continuous `requestAnimationFrame` loop for terminal rendering — on Linux without GPU acceleration this causes CPU usage proportional to canvas size.

**CSS file roles**: `style.css` = app layout, `theme-nixos.css` / `theme-dark.css` / `theme-light.css` = per-theme variables (colors, radii, xterm palette), `addons/xterm.css` = vendor file (unmodified).

**Theming rule**: Any new CSS values that a user might want to customize (colors, radii, sizes, spacing) must be exposed as CSS variables defined in all three per-theme files (`theme-nixos.css`, `theme-dark.css`, `theme-light.css`). Hard-coded values in `style.css` are only acceptable for structural/layout properties that should never vary. When adding new UI elements, always check whether their visual properties belong in the theme files.

## Nix flake

`flake.nix` exposes four packages (`modules/ezconf-packages.nix`):
- `ezconf` — web server + assets wrapped with `makeWrapper`; `--webroot` defaults to the Nix store share dir.
- `ezconf-terminal` — `bin/terminal.py` wrapped as a standalone binary; separate derivation so CSS/asset changes don't trigger a terminal service restart.
- `ezconf-mkoptions` — `bin/generate-nixos-data.py` wrapped with nix + python-pam + cryptography in PATH.
- `ezconf-mkcerts` — shell script that runs `mkcert -install` + generates `localhost.pem`/`localhost-key.pem` in CWD (dev convenience only).

The `ezconf` derivation installs `webroot/` (HTML, CSS, JS, xterm addons) and `bin/server.py`. Autocomplete data (`autocomplete/`) is not installed — it is user-generated and belongs in a writable directory.

## NixOS module (`modules/ezconf.nix`)

Defines two systemd services when `services.ezconf.enable = true`:
- `ezconf.service` — the web server
- `ezconf-terminal.service` — the terminal WebSocket service (only when `terminal = true`; `restartIfChanged = false`)

Key options:
- `user` / `group` — service user/group (default `root`)
- `https` — enable TLS (default `true`); when `true` and no `cert`/`key` are set, `generateCert` is automatically enabled
- `generateCert` — generate a local CA + server cert in `/var/lib/ezconf/` (set automatically by `https`)
- `installCerts` — install the generated CA into `~/.pki/nssdb` for each user in `auth.allowedUsers` (default `true`; only has effect when `generateCert = true`)
- `cert` / `key` — explicit TLS cert/key paths (require `https = true`; must be set together)
- `auth.method` / `auth.username` / `auth.password` / `auth.passwordFile` / `auth.allowedUsers`
- `theme` — UI theme: `nixos`, `dark`, or `light` (default `nixos`)
- `terminal` — enable terminal panel and `ezconf-terminal.service` (default `true`)
- `shell` — shell for the terminal panel (default: login shell of `user`)
- `nixosTarget` — flake path passed to `ezconf-mkoptions` (default `/etc/nixos`)
- `ports.web` / `ports.terminal` — service ports (defaults `9090` / `9091`)
- `configDir` — directory for `configuration.json` and `default.nix` (default `/etc/nixos/ezconf`)
- `buttons` — list of `{label, command, save_first}` shortcuts shown in the terminal panel

The activation script creates `configDir`, generates certs if needed, and installs the CA into allowed users' NSS databases. The `preStart` script generates autocomplete data on first run, creates the session key, and writes the runtime TOML to `/run/ezconf/ezconf.toml`.
