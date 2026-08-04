# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git

Do not add Claude as co-author in commit messages.

All changes must be committed to `develop` first. Only merge `develop` into `master` — never commit directly to `master`.

## What this is

A zero-dependency, single-page NixOS configuration editor. No build step, no framework, no package manager. The app is served by `bin/server.py` and edits the `*.json` config files under a directory specified via `--file`, discovered recursively (except `custom-options.json`, a schema-extension sidecar, and dotdirs like the default `.ezconf-backups`) — each one an independently editable/saveable/backed-up "file," chosen via a dropdown in the header, merged together only at Nix-eval time via `lib.mkMerge` in `json2nix.nix`. Files can be nested in subfolders (e.g. `services/nginx.json`) purely for the user's own organization — root-level files are plain entries in the dropdown, files in a folder are grouped under an `<optgroup>`. `--file` can also name a specific `*.json` file inside that directory (kept for compatibility with older invocations) — its directory becomes the working set and it's preselected initially.

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

Single `ThreadingHTTPServer` bound to `BIND_ADDR:WEB_PORT` (default `127.0.0.1:9090`). Serves static assets via `StaticHandler` (subclass of `SimpleHTTPRequestHandler`) and handles the API.

**API endpoints**:
- `GET /download-ca` — serves `CA_FILE` as a downloadable PEM (no auth required; only available when `CA_FILE` is set)
- `GET /api/v1/files` — `{"files": [...], "folders": [...], "default": NAME|null}`; `files` lists the `*.json` tabs in `CONFIG_DIR` (`list_config_files()`); `folders` lists *every* subdirectory (`list_config_folders()`), including ones with no `*.json` file in them yet — this is what lets an empty folder created via `POST /api/v1/folder/create` still show up as a tab group after a reload, since `files` alone would never imply it; `default` is the file `--file` named explicitly (if any), used to pick the initial tab (auth required)
- `GET /api/v1/file?file=NAME` — serves the resolved config file's raw JSON content (falls back to `DEFAULT_FILE` when `NAME` is omitted); not wrapped in a JSON envelope, just the file's bytes with `Content-Type: application/json` (auth required)
- `POST /api/v1/file/save?file=NAME` — writes the resolved config file (falls back to `DEFAULT_FILE` when `NAME` is omitted), backing up the previous contents first via `backup_config()`; creates the file if it doesn't exist yet, which is how a new tab gets persisted (auth required)
- `POST /api/v1/autocomplete/update` — runs `MKOPTIONS_CMD` to regenerate autocomplete data; only available when `--mkoptions` is set (auth required)
- `GET /api/v1/backups?file=NAME` — lists backups in `BACKUP_DIR` whose name is prefixed with `NAME`'s stem (name, mtime, size), newest first (auth required)
- `POST /api/v1/backup/create` — body `{"file": NAME}`; backs up that file's current on-disk contents on demand (via `backup_config()`, the same call `file/save` makes as a side effect) — snapshots what's on disk, not any unsaved in-memory edits; rejects if `BACKUP_COUNT` is 0 (unreachable from the UI itself, since the whole Backups feature — button included — is hidden in that case, but the endpoint guards it independently) (auth required)
- `POST /api/v1/backup/restore` — body `{"file": NAME, "name": BACKUP_NAME}`; copies a named backup over the resolved config file; does not itself create a backup (that state is only saved if/when the user later hits Save); rejects backup names outside `BACKUP_DIR` (auth required)
- `POST /api/v1/backup/delete` — deletes a named backup file; rejects names outside `BACKUP_DIR` (auth required)
- `POST /api/v1/file/delete` — body `{"file": NAME}`; deletes a whole config file; does not itself create a backup (that state is only saved if/when the user later hits Save on another tab); deleting the last remaining file is allowed — zero files is a valid state, see `.editor-empty-state` (auth required)
- `POST /api/v1/file/rename` — body `{"from": NAME, "to": NAME}`; renames/moves a config file via `os.rename` (moving into/out of a subfolder is just a path change, so one endpoint covers both); both names are validated through `resolve_config_path()`; auto-creates destination subdirectories; rejects if the destination already exists (auth required)
- `POST /api/v1/folder/create` — body `{"folder": NAME}`; creates an (initially empty) subfolder under `CONFIG_DIR` via `os.makedirs(..., exist_ok=True)`; validated through `resolve_folder_path()` (same rules as `resolve_config_path()` minus the `.json` requirement); rejects if a file already exists at that path (auth required)
- `POST /api/v1/folder/delete` — body `{"folder": NAME}`; deletes a subfolder and everything in it via `shutil.rmtree()` (so nested subfolders go too); validated through `resolve_folder_path()`; rejects if the resolved path doesn't exist or isn't a directory; deleting a folder that holds every remaining file is allowed — same "zero files is valid" rule as `file/delete` (auth required)

**File routing**: `StaticHandler.translate_path` sets `self.directory = WEBROOT`. `GET /api/v1/file?file=NAME` is served from the resolved config path (not WEBROOT) — `resolve_config_path()` accepts `NAME` as a relative path (e.g. `services/nginx.json`) and keeps writes inside `CONFIG_DIR` (a correctness guard, not a security boundary — an authenticated user here already has full terminal access to the machine); falls back to `DEFAULT_FILE` when `NAME` is omitted. `list_config_files()` walks `CONFIG_DIR` recursively via `os.walk`, skipping dotdirs. Backup filenames flatten the relative path (`services/nginx.json` → stem `services--nginx`) via `_flatten_stem()` so `BACKUP_DIR` itself stays a single flat directory even with nested tabs. `/autocomplete/*` is served from `AUTOCOMPLETE_DIR` when set.

**Key globals**:
- `WEBROOT` — directory for static assets (set by `--webroot`)
- `CONFIG_DIR` — directory of JSON config files (tabs); set by `--file` (its dirname, if `--file` names a specific file)
- `DEFAULT_FILE` — basename to prefer as the initial tab; set when `--file` names a specific file rather than a directory, or explicitly via `--default-file`/`default_file` in config when `--file` is a directory (unset otherwise); used to pick which tab the editor opens on and as `resolve_config_path()`'s fallback when no `file` is given
- `AUTOCOMPLETE_DIR` — override for autocomplete file serving (set by `--autocomplete-dir`)
- `MKOPTIONS_CMD` — path to mkoptions binary; enables the autocomplete/update endpoint
- `TERMINAL_PORT` — when set, enables the terminal panel in the frontend and points it at this port
- `THEME` — UI theme injected into `index.html`; set by `--theme` or `theme` in config (default `nixos`)
- `BIND_ADDR` — IP address to listen on; set by `listen` in config (default `127.0.0.1`); automatically added to `TRUSTED_HOSTS`
- `TRUSTED_HOSTS` — extra hostnames accepted by `_valid_host` for CSRF check; set by `trusted_hosts` in config; always includes `BIND_ADDR` and any `--san` values
- `CA_FILE` — path to the CA cert served at `/download-ca`; set automatically by `--generate-ca` or via `ca_file` in config
- `BACKUP_DIR` — directory for config file backups, one subset per file (named `<stem>-<timestamp>.json`); set by `--backup-dir` or `backup_dir` in config (default: `.ezconf-backups` inside `CONFIG_DIR`)
- `BACKUP_COUNT` — number of backups kept per save; set by `--backup-count` or `backup_count` in config (default `5`; `0` disables backups)
- `_SESSION_KEY` — random hex key generated at startup (or loaded from `--session-key-file`); used as the expected value of the `ezconf_session` cookie

**Auth flow**: The login form POSTs to `/login`. On success the server sets `Set-Cookie: ezconf_session=<SESSION_KEY>; HttpOnly; SameSite=Strict; Path=/`. All subsequent requests (browser and API) are authenticated by that cookie. `check_auth()` reads the `ezconf_session` cookie from the `Cookie` header and compares it to `_SESSION_KEY`.

**PAM auth**: `check_pam()` creates a fresh `pam.pam()` instance on every call — do not use a global instance. The global instance segfaults on Linux after the first `authenticate()` call due to libpam memory management.

## Terminal service (`bin/terminal.py`)

Separate process from the web server. Listens on its own port (default 9091) and handles WebSocket connections that upgrade a PTY session. `_terminal_ws()` forks the configured shell into a PTY, then bridges it over WebSocket frames. Resize messages (`{"type":"resize","cols":N,"rows":M}`) from the client call `fcntl.ioctl(TIOCSWINSZ)`. The `pty_to_ws` thread polls the master fd with `select` (0.5s timeout).

Reads the same `ezconf.toml` as the web server (`--config FILE`). The WebSocket upgrade request is authenticated via the `ezconf_session` cookie — the browser sends it automatically on the upgrade request. In the NixOS service this runs as `ezconf-terminal.service`, separate from `ezconf.service`, with `restartIfChanged = false` so terminal sessions survive `nixos-rebuild switch`.

## Frontend architecture (`webroot/index.html`)

All application logic is in the `<script>` block at the bottom (~1400 lines). No modules, no imports, no build step.

**Data model**: `config` (plain JS object mirroring the active tab's JSON file), `options` (array of `{path, type, description, default, example}`), `packages` (array of `{name, description}`). Two sentinel object shapes are used:
- `{ _expr: "..." }` — raw Nix expression replacing a normal value
- `{ _disabled: true, _value: <original> }` — option disabled in the UI; filtered out by `json2nix.nix` at evaluation time, preserving the original value for re-enabling

**Multi-file state**: every file's config is loaded into `fileConfigs[name]` up front (not just the active one), so edits on inactive files survive switching and Save can persist all of them at once. `config` is always the same object as `fileConfigs[activeFile]` — code paths that reassign `config` wholesale (import, undo/redo, reload) must be reflected back into `fileConfigs[activeFile]`; `renderAll()` does this unconditionally as a safety net, since virtually every mutation calls it. `fileSaved[name]` holds the last-saved-to-disk JSON snapshot per file; `isAnyDirty()` compares every file against it, driving both the status dot and every `save_first` terminal button (disabled while *any* file is dirty, not just the active one). `fileHistories[name]` holds independent undo/redo stacks per file. `saveToServer()` POSTs every dirty file, not just the active one. `name` keys throughout are `CONFIG_DIR`-relative paths (e.g. `services/nginx.json`) — `renderFileSelector()` renders a first `.tab-row.tab-row-root` for root-level files, then one `.tab-folder-group` (`dataset.folder` set to its name) per top-level folder — the union of folders implied by `files` and every entry in the independent `folders` array, so an empty folder still gets a row — each holding a `.tab-folder-label` on its own line above a `.tab-row` of that folder's tab buttons (`_makeTab()`). A tab's label (`_tabLabel()`) drops the folder prefix and the `.json` suffix, with the full relative path shown as its `title` tooltip.

There are no "+"/"new file" buttons anywhere in the tab bar — everything is right-click. Right-clicking blank tab-bar space (bound on `#tab-bar` itself) offers "New file"/"New folder"; right-clicking a `.tab-folder-group` (bound to the whole group — header + row — so blank space inside an otherwise-empty folder still works, same pattern as `.section`'s contextmenu) offers "New file here" (creates the file already inside that folder, `createFile(folder)`) and "Delete folder" (`deleteFolder()`). A tab's own contextmenu handler calls `showContextMenu()` (which stops propagation) before either of those ancestor handlers ever sees the event, *except* when there's only one file left — it deliberately returns without calling `showContextMenu()`, letting the click bubble up to the tab-bar's New-file/New-folder menu instead of showing nothing.

Tabs are plain `<div>`s, not `<button>`s (rename mode swaps the label for a live `<input>`, which is invalid inside a real `<button>`). Click switches files; double-click calls `_startTabRename()` to swap the label for an inline `<input>` (prefilled with the current basename, text selected, sized to its own text via `_autosizeInput()`'s `size` attribute rather than a fixed CSS width) — Enter/blur commits, Escape cancels, and a `/` in the typed value is rejected with a status message rather than reinterpreted as a move, since moving is drag's job. Dragging a tab (a custom `mousedown`/`mousemove`/`mouseup` implementation local to `_makeTab`, separate from `makeDraggable`) more than 5px starts a ghost-following drag (reusing the `.drag-ghost`/`.is-dragging` classes); hovering a `.tab-folder-group` or the root row highlights it (`.tab-move-target`) as the drop target, and dropping calls `_relocateFile(oldName, newName)` with the folder swapped and basename kept. A `suppressClick` flag (cleared via `setTimeout(…, 0)` after `mouseup`, so it's still true when the browser's own synthesized `click` fires) stops that drag from also being treated as a plain click.

`_relocateFile()` is the shared core behind both the drag-move and the inline-rename commit — the same `os.rename`-backed `POST /api/v1/file/rename` call as before, re-keying `files`/`fileConfigs`/`fileSaved`/`fileHistories` client-side and updating `activeFile`/`localStorage` when the active file is the one moved. A file that was never saved to disk (absent from `fileSaved`) skips the backend call entirely, mirroring `deleteFile()`'s same check. `createFile(folder = '')` no longer prompts — it creates `untitled.json` (or `untitled-N.json` if taken, scoped to whichever folder it's being created in) and immediately calls `_startTabRename()` so the placeholder name is ready to be typed over. `createFolder()`/`_startFolderRename()` follow the same placeholder-then-inline-edit pattern, but unlike a file (staged in memory until Save) a bare folder has no config data to stage, so committing its name calls `POST /api/v1/folder/create` immediately, same as a real `mkdir`. `deleteFolder()` calls `POST /api/v1/folder/delete` (a `shutil.rmtree`, so nested subfolders go too) and then cleans up every client-side file/folder entry under that prefix regardless of whether each was ever saved to disk; deleting a folder that holds every remaining file is allowed, same as `deleteFile()` — zero files afterward just shows `.editor-empty-state`, not an error.

**Key subsystems:**

| Subsystem | Functions |
|---|---|
| Path helpers | `getAtPath`, `setAtPath`, `deleteAtPath`, `traverseForSet` |
| Type inference | `typeOf`, `typeFromNix`, `isFreeformType`, `isNullableString`, `parseEnumOptions` |
| Option lookup | `findOption`, `optionSearch`, `isValidOptionPath`, `getWildcardBoundary`, `blankObjectFromOptions` — wildcard segments (`<name>`, `<n>`, `*`) match any concrete key |
| Nix default parsing | `parseNixDefault` — converts Nix expression strings to JS values; falls back to `defaultForType` |
| Add panel | `initAddPanel`, `doAdd`, `doForceAdd`, `doForceAddWithType` — the topbar's add-input/Add-button/force-type-bar; `showAddOptionMenu(event, path)` (see Context menu below) temporarily relocates these same elements to a floating panel at the cursor rather than reimplementing search separately |
| Editor rendering | `renderEditor` → `renderObj` → `renderSection` / `renderField` / `renderArray` / `renderPkgArray` / `renderDisabled` |
| Tooltips | `buildTooltip(opt, typeLabel)` builds the popup shown for a section/field's title; `_wireTooltipAnchor(anchor, tooltip)` toggles a `.tt-visible` class on mouseenter/mouseleave rather than relying on CSS `:hover`, because `anchor` (the tight-fit `.tt-anchor` span wrapping just the title text, so hovering blank space in the wider reserved key-column doesn't also trigger it) sits inside `.key-label`, which needs `overflow: hidden` for ellipsis-truncating long names — a `:hover`-shown absolutely-positioned tooltip nested in there would get clipped right along with the truncated text. The tooltip element itself is kept as a sibling of `.key-label`, not a descendant, so it escapes that clipping |
| Tree sidebar | `renderTree`, `renderTreeLevel`, `toggleTree` — hidden by default; `#sidebar`'s `hidden` class is toggled by the plain-text "Tree" button (the rightmost button in `.topbar`) and the preference persists per-browser via `localStorage['ezconf-tree-visible']` (unlike flat mode's URL-param persistence, since tree visibility isn't tied to which path/tab you're on). `renderTree()` still runs unconditionally on every `renderAll()` regardless of visibility. `.topbar` and `.force-type-bar` are full-width siblings of `.layout` (not nested inside `.main`), so they — and the Tree button itself — never resize or shift when the sidebar is shown/hidden; only `.layout`'s two children (`#sidebar` and `.main`/`#editor-area`) sit side by side below them |
| Drag-and-drop | `makeDraggable`, `reorderKey`, `moveValueToFile` |
| Copy/cut/paste | `copyPath`, `cutPath`, `pastePath`, `_clipboard` — moves/duplicates a section or option between files, always at the same path it was copied from. If the destination already has a value there, `_mergeOrReplace()` deep-merges the two (via `_nixDeepMerge`, same as `doImport()`'s merge) when both sides are plain objects — the incoming/copied value wins on any leaf conflict, and the destination's unrelated keys are kept rather than clobbered wholesale. `moveValueToFile()` (the drag-to-file-tab equivalent, see Drag-and-drop below) uses the same helper. Non-object values (scalars, arrays, `_expr`) just get replaced outright — neither operation prompts for confirmation |
| Context menu | `showContextMenu`, `_commonMenuItems`, `showAddOptionMenu` — right-click on a section/field/array-field row for its actions (add option, convert to expr, disable/re-enable, copy, cut, delete, add item). A section's menu is bound to the whole `.section` div (header + body), not just the header, so right-clicking blank space inside it still targets that section — nested sections/fields stop propagation in their own handler first. There's no dedicated Paste button anywhere — `renderEditor()` also binds `#editor-area`'s `oncontextmenu` to offer "Paste" (only when `_clipboard` is set; otherwise it doesn't call `preventDefault()`, so the browser's native menu shows instead), which is what lets you paste into a file with nothing rendered in it yet. "Add option…"/"Add field" call `showAddOptionMenu(event, path)`, which relocates the real topbar add-input (+ its autocomplete dropdown, Add button, and force-type bar — see `_detachForFloat`/`_closeFloatingAddPanel`) into a `.floating-add-container` at the cursor, prefilled with `path`, rather than reimplementing search: full parity with the global add bar (live fuzzy search, `<name>` wildcard prompts, Tab-completion, force-add) for free. Closes and restores the topbar on a successful add, Escape, or an outside click |
| Import | `doImport`, `_normalizeFileName`, `_nixDeepMerge` — parses pasted (or file-loaded) Nix/JSON and deep-merges it into the file named in the "Import into" field (`#import-target-file`, defaulting to `activeFile` when opened via `showImportModal()`); a name not already in `files` is created on the fly (same in-memory-only, no `fileSaved` entry, until Save as `createFile()`) and switched to after import so the result is immediately visible |

**`_expr` objects** appear as scalar fields toggled to raw Nix (via the "Convert to Nix expression" context-menu item) and as elements of package arrays (`{ _expr: "pkgs.foo" }`). `isExprPkg(v)` distinguishes the two. `renderPkgArray` handles arrays at paths ending in `systemPackages`, `packages`, `extraPackages`, `extraPlugins`, or `users.users.<name>.packages`.

**`_disabled` objects** (`{ _disabled: true, _value: <original> }`) are rendered by `renderDisabled`, which delegates to the normal render function for the inner value and then adds the `is-disabled` CSS class. Unlike the old always-visible `#` button, the context menu's "Disable"/"Re-enable" item is computed fresh at menu-open time from `isDisabled(getAtPath(config, path))`, so `renderDisabled` doesn't need to patch anything up after the fact. `isDisabled(v)` also guards the check in `renderObj` and `renderTreeLevel` (disabled nodes are treated as leaves in the tree).

**Right-click context menu** (`showContextMenu`, in place of the old always-visible icon-button row): most row types in the normal (non-flat) tree use it — sections, fields, array-field-rows, `array-obj-item`, and plain scalar array items all bind their own `oncontextmenu` instead of rendering a visible ✕/+/`{ }` button. `oncontextmenu` on the row calls `showContextMenu(event, items)`, which renders a fixed-position `.context-menu` at the cursor and wires a capture-phase `mousedown` listener (`_ctxMenuOutsideHandler`) to close it — that listener specifically checks the click is outside the menu (`!_ctxMenuEl.contains(e.target)`), since a plain document-wide close-on-mousedown would fire on the menu's own items first and remove them before their click handler runs. `_commonMenuItems(path)` builds the shared Disable/Re-enable + Copy/Cut + Delete tail every row's menu ends with; each render function prepends its own type-specific items (convert-to-expr, convert-back-to-native-type, add-item) before it. Two exceptions: flat mode (the ≡ toggle) still uses the old always-visible icon-button row (`makeFlatSubHeader`/`makeFlatGroupHeader`), left untouched throughout this migration; and package chips (`renderPkgArray`/`makePkgRow`) have a visible `✕` delete button rather than a context-menu item.

**`traverseForSet`** navigates/creates intermediate path nodes. It preserves existing arrays rather than replacing with `{}`, and uses `emptyContainerFor` (consults `findOption`) to decide if missing nodes should be `[]` or `{}`.

**Drag-and-drop** (`makeDraggable`): no element — section, field-row, array-field-row, or `array-obj-item` (an object element inside an array, e.g. each entry of `services.ezconf.buttons`) — has a dedicated drag handle. `mousedown` is bound to the whole element, and starts a drag unless the event target is inside `_DRAG_EXCLUDE_SELECTOR` (inputs, buttons, toggles, custom-select controls, etc.), so clicking/typing into a value still works while grabbing anywhere else on the row (background, key label, section header, or an `array-obj-item`'s own padding/border) reorders it. During a drag, hovering over a sibling with the same parent path highlights it (`.drag-over`) for a same-file reorder (`reorderKey`); hovering over a *different* file's tab in `#tab-bar` (each tab carries `dataset.file`) highlights it (`.tab-drop-target`) and dropping calls `moveValueToFile(path, targetFile)` — merges the value into that file's `fileConfigs` at the same path (via `traverseForSet` and `_mergeOrReplace()`, see Copy/cut/paste above) and deletes it from the current file. Only object-keyed rows can be dropped on a file tab this way — a plain array item (numeric-index key) has no stable meaning as a path in another file's config, so `canMoveToFile` is false for those and only same-array reordering applies. `array-obj-item`'s own right-click menu offers "Add field" (via the same `triggerAdd`-style prefill as sections) followed by `_commonMenuItems(itemPath)`.

**`_commonMenuItems(path, { skipDisable })`** omits "Disable" whenever `path`'s parent container is actually an array (checked via `Array.isArray(getAtPath(config, path.slice(0, -1)))`, not by guessing from the segment's own shape — a real object key can itself look numeric) — disabling a single array item isn't supported end-to-end: `json2nix.nix`'s `resolveExprs` only filters `_disabled` wrappers out of attrsets, not array elements, and `renderArray`/`renderFlatArray` don't check `isDisabled()` before treating a value as an object item either, so it would neither render nor evaluate correctly. This applies uniformly wherever `_commonMenuItems` is called with an array-indexed path — `array-obj-item`, and any leaf rendered at a numeric path (e.g. a raw scalar sitting inside an otherwise-object array in flat mode). "Re-enable" and "Delete" are unaffected; `skipDisable` remains available for callers that want to suppress the offer for other reasons.

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
- `backupDir` / `backupCount` — directory and retention count for `configuration.json` backups (defaults `/var/lib/ezconf/backups`, `5`; `backupCount = 0` disables backups)
- `listen` — IP address to bind to (default `127.0.0.1`; use `0.0.0.0` for all interfaces); `openFirewall` is enabled automatically for non-localhost addresses
- `interface` — network interface to open firewall ports on (e.g. `"eth0"`); when set, uses `networking.firewall.interfaces.<name>.allowedTCPPorts` instead of the global `allowedTCPPorts`; works with both iptables and nftables backends
- `ports.web` / `ports.terminal` — service ports (defaults `9090` / `9091`)
- `configDir` — directory for the `*.json` tabs plus `default.nix` (default `/etc/nixos/ezconf`); `services.ezconf.file` is set to this directory — a fresh install starts with **zero** tabs (`configDir` is deliberately not seeded with a file; see below)
- `defaultFile` — file (relative to `configDir`) preferred as the initially-selected tab when a browser has no prior choice remembered (default `configuration.json`); a hint only — nothing creates this file automatically
- `buttons` — list of `{label, command, save_first, always_show}` shortcuts shown in the terminal panel; `always_show` (default `true`) controls whether a button set in one tab's config also shows while other tabs are active; `save_first` disables the button while *any* tab has unsaved changes, not just the active one (Save persists every dirty tab, not just the active one)

The activation script creates `configDir` (with `default.nix`, but no seeded `*.json` file — see below), generates certs if needed, and installs the CA into allowed users' NSS databases. The `preStart` script generates autocomplete data on first run, creates the session key, and writes the runtime TOML to `/run/ezconf/ezconf.toml`.

**Why `configDir` starts empty**: an earlier version seeded `defaultFile` with `{}` on every activation if missing. That fought the editor's rename/move features directly — renaming `defaultFile` away just meant the *next* `nixos-rebuild`/reboot silently recreated it, which could then collide with a later rename back to that name ("a file already exists at the destination"). `json2nix.nix`'s `lib.mkMerge` over zero `*.json` files evaluates to `{}` just fine, so an empty `configDir` is a completely valid (if inert) state. `renderEditor()` shows a persistent `.editor-empty-state` message with instructions when `!activeFile` (no files at all), and its `oncontextmenu` — like `#tab-bar`'s — offers "New file"/"New folder" directly, so the empty state is itself actionable rather than a dead end.
