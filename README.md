# ezconf

Graphical editor for nix configurations. Zero dependencies, no build step, no framework — just Python and a browser.

**Blog post:** [https://kalken.github.io//ezblog/#ezconf](https://kalken.github.io//ezblog/#ezconf)

## ✨ Features

- Edit NixOS configuration through a clean web UI with option autocomplete
- Search (Ctrl+F or the 🔍 button) across every open file for an option path or a value, jumping straight to it
- Split config across multiple files and folders — organize however you like, merged only at Nix-eval time
- Inline terminal panel with configurable shortcut buttons
- Browse any `.md` files anywhere in the system config (e.g. `README.md`), rendered in a side panel
- PAM auth (system credentials) or custom username/password
- HTTPS with automatic local CA generation and browser trust store installation
- Three themes: NixOS blue, dark, light

## 🚀 Quick Start

Add to your flake inputs:

```nix
inputs.ezconf.url = "github:kalken/ezconf";
inputs.ezconf.inputs.nixpkgs.follows = "nixpkgs";
```

Add the module import to your `flake.nix`'s module list — it defines the `services.ezconf.*` options, so it needs to live somewhere that survives migrating away from `configuration.nix` (see below), unlike the option values themselves:

```nix
nixosConfigurations.myhostname = nixpkgs.lib.nixosSystem {
  modules = [
    inputs.ezconf.nixosModules.default
    ./configuration.nix
    ./ezconf   # created automatically on first start
  ];
};
```

Then enable the service in your NixOS configuration (e.g. `configuration.nix`), same as any other option:

```nix
{ ... }: {
  services.ezconf = {
    enable = true;
    auth.allowedUsers = [ "alice" ];
    buttons = [
      { label = "Rebuild"; command = "nixos-rebuild switch --flake /etc/nixos"; save_first = true; }
    ];
  };
}
```

After `nixos-rebuild switch` the editor is at `https://localhost:9090`. A local CA and certificate are generated automatically, and installed into the browser trust store for each user in `allowedUsers` (set `certUsers` separately if you log in as a different user than the one whose browser needs to trust it).

> **Tip:** In Chrome or any Chromium-based browser, open the address bar menu and choose *Install page as app* to get a standalone desktop app with no browser chrome.

## 🔁 Migrating from configuration.nix

1. Enable the service and rebuild — this creates `/etc/nixos/ezconf/` (empty; nothing is seeded automatically)
2. Open the editor and use the import button to import your existing `configuration.nix` — the "Import into" field accepts any name (e.g. `configuration.json`) and creates that file for you in one step
3. In your `flake.nix`, comment out `./configuration.nix` in the modules list — `./ezconf` and the `ezconf.nixosModules.default` import are already there from Quick Start and don't depend on it
4. Rebuild — your config is now managed through the editor

> **Note:** Any `imports` you had in `configuration.nix` should be moved to your `flake.nix` after migrating — the JSON-based config does not support `imports`.

## 📑 Multiple Config Files

Configuration doesn't have to live in one big `configuration.json` — split it across as many `*.json` files as you like, each one an independent tab in the header with its own save/backup history (Undo/Redo is shared across every tab, not per-file — see below). Files are only combined at Nix-eval time (via `lib.mkMerge`), the same as splitting a hand-written `configuration.nix` across modules: lists and attribute sets merge normally, and a scalar option set differently in two files is a plain Nix eval error, not something ezconf tries to resolve for you. Group files into folders for your own organization (e.g. `services/nginx.json`) — folders are just cosmetic grouping in the tab bar, not part of the Nix module structure.

There's no "+" button anywhere — file and folder management is entirely right-click:

- Right-click empty space in the tab bar (or the empty editor area, if there are no files yet) for **New file** / **New folder**.
- Right-click a folder for **New file here** or **Delete folder** (removes everything inside it).
- Right-click a tab for **Delete**, or **Reload** to discard that one file's unsaved edits and reread it from disk (the header's own reload icon reloads every file plus the folder list in one go — the file list itself can change on disk too, e.g. from an external edit).
- Double-click a tab to rename it inline.
- Drag a tab into a folder (or back out to the root) to move it.
- Drag a section or option onto a different file's tab to move it there, or use **Copy** / **Cut** / **Paste** (also right-click) to duplicate or relocate a section or option to the same path in another file.

None of this touches disk until you hit **Save** — creating, deleting, renaming, moving, or disabling/enabling a file or folder all stay purely in the browser (the tab bar updates immediately) until then, applied to the actual `*.json` files in one batch when you save. Delete has no confirmation prompt for the same reason: it isn't real until Save applies it.

A fresh install starts with zero files — the empty editor area explains how to create the first one, and the Import modal's "Import into" field can create a new file on the spot.

## ↩️ Undo / Redo

One shared undo/redo timeline covers everything, not just field edits — deleting, renaming, moving, or disabling/enabling a file or folder is exactly as undoable (↩ / ↪ in the header, or Ctrl+Z / Ctrl+Y) as changing a value.

It survives reloading the page, too — including a hard/shift-reload. If nothing changed in `CONFIG_DIR` since you were last here (nobody else edited a file, nothing external touched it), your full undo/redo trail comes back, unsaved edits included: they won't reappear as live changes on their own, but a Redo brings them right back. If something *did* change underneath you, the old trail is discarded rather than resumed into a state that no longer matches reality — you just start fresh from whatever's actually on disk now.

## 🖥️ Standalone

```sh
git clone https://github.com/kalken/ezconf
cd ezconf
python3 bin/server.py --file /path/to/configuration.json
```

Or with a config file:

```sh
cp example/ezconf.example.toml ezconf.toml
$EDITOR ezconf.toml
python3 bin/server.py

# Point at a config file in another location
python3 bin/server.py --config /path/to/ezconf.toml
```

Open `http://localhost:9090`. Authentication is always required — without PAM available it falls back to custom mode (set `username` and `password` in `ezconf.toml`).

Optional Python dependencies: `python-pam` (PAM auth), `cryptography` (`--generate-cert`), `tomli` (TOML config on Python < 3.11).

## Nix Expressions

Right-click any field for **Convert to Nix**, which switches it to raw Nix expression mode. In this mode you can type any valid Nix expression directly — useful for freeform options that don't map cleanly to a structured form, such as Samba shares or `extraConfig` strings.

Right-click the field again for **Convert to native** to turn it back into its native type.

## Disable Toggle

Right-click any option or section for **Disable**. Disabled options remain fully visible in the editor (dimmed with the key struck through) but are excluded from the Nix configuration at evaluation time — the value is preserved and can be re-enabled at any time via the same menu's **Re-enable** item.

Disabling a section disables all of its children at once.

## ✏️ Rename

Right-click any option or section for **Rename**, which swaps its key for an inline text field — handy for a wildcard-named entry (e.g. `systemd.services.<name>`, `users.users.<name>`) whose name needs to change without deleting and recreating the whole thing. Sibling order and the value are preserved; renaming to a name that already exists at that level is rejected.

## 📦 Export / Import

A single file tab or a folder each has an **Export** action on its right-click menu, downloading that file as a plain `.json` or that folder's files together as a `.zip`. Exports include whatever's currently in the editor, unsaved changes included.

The header's **⬆ Export** button is a dropdown with two broader options:

- **Files** — every file across every folder, as one `.zip`.
- **System** — a `.zip` of the *entire* NixOS config tree (`nixos_target`, default `/etc/nixos`), not just what's open in the editor: flake.nix, flake.lock, and everything else alongside ezconf's own files. This one comes straight from disk on the server, so it always reflects what's actually saved there, not any unsaved edits. Symlinks (`nix build`'s `result`/`result-*`, which point into `/nix/store`) are always excluded. Dotfiles/dotdirs (`.git`, `.ssh`, age/sops keys, etc.) and `hardware-configuration.nix` (machine-specific — not something you want bundled into a config meant to be reused elsewhere) are excluded by default, configurable via `system_export_exclude_dotfiles`/`system_export_exclude` (or `systemExportExcludeDotfiles`/`systemExportExclude` in the NixOS module).

Server-side system backups and restoring them both live under the **🕐 Restore** button instead (see [Backups](#-backups) below) — the Export/Import dropdowns are purely about moving a `.zip` in or out of the browser.

To import, just drag `.json` file(s), a whole folder, or a `.zip` (your own export, or one built by another tool) onto the window and drop it. Each file is loaded straight into the editor as an unsaved, dirty tab — nothing touches disk until you hit **Save** — so importing is always safe to undo by just not saving. Dropping a file with the same name as an existing tab replaces that tab's in-editor content (again, only once saved).

No drag-and-drop handy? The header's **⬇ Import** button is a dropdown too, mirroring **⬆ Export**:

- **Files** — the "Load file" picker (or paste box) for a single Nix/JSON snippet to merge into one file; it also takes a `.zip`, imported the same way a drop does.
- **System** — the exception to all of the above: pick a `.zip` and it's written straight to `nixos_target` on disk immediately — there's no editor staging step for it to defer to, since these aren't ezconf's own tabs. You'll get a confirmation prompt before anything happens. Existing files with the same name are overwritten, and anything not in the zip is **deleted** — the tree ends up matching exactly what was imported, same as restoring a system backup does. Dotfiles/dotdirs are skipped on the way in too — this can't restore secrets an export never captured in the first place. The current tree is backed up first automatically (see [Backups](#-backups)) — an import is a destructive overwrite, so there's always a way back to what was there a moment ago.

## 🔐 Authentication

Three modes, set via `auth.method`:

- `auto` — PAM if available, else custom (default)
- `pam` — system username + password via `python-pam`
- `custom` — username/password from config

PAM mode with allowed users:

```nix
services.ezconf = {
  enable = true;
  auth.method = "pam";
  auth.allowedUsers = [ "alice" "bob" ];
};
```

Custom credentials:

```nix
services.ezconf = {
  enable = true;
  auth.method       = "custom";
  auth.username     = "admin";
  auth.passwordFile = "/var/lib/ezconf/password";
};
```

Create the password file once:

```sh
echo -n "mypassword" > /var/lib/ezconf/password
chmod 600 /var/lib/ezconf/password
```

> **Note:** The username must be set in your NixOS config. For the password, prefer `auth.passwordFile` over `auth.password` — the latter is stored in the Nix store and world-readable. The runtime config at `/run/ezconf/ezconf.toml` is regenerated on every service start, so editing it directly has no effect.
>
> For **standalone use**, you can set `username` and `password` directly in `ezconf.toml` and they will be picked up on the next start.

## 🖱️ Terminal Panel

The terminal panel runs as a separate service (`ezconf-terminal.service`) and is enabled by default. Configure shortcut buttons to run common commands:

```nix
services.ezconf = {
  enable   = true;
  terminal = true;
  buttons  = [
    { label = "Rebuild"; command = "nixos-rebuild switch --flake /etc/nixos"; save_first = true; }
    { label = "Update";  command = "nix flake update /etc/nixos"; }
    { label = "Check";   command = "nix flake check /etc/nixos"; }
  ];
};
```

`save_first = true` disables the button while there are unsaved changes.

Every button runs "direct" by default — as its own independent OS process, streaming its output live into a panel that pops up from the bottom of the page, separate from the terminal panel — rather than typing the command into the terminal's shell. This matters because a command like `nixos-rebuild switch`/`test` can restart `ezconf-terminal.service` itself partway through (Linux/systemd restarts changed units automatically on a rebuild); typing it into that same shell would kill the command along with the shell it's running in, mid-command. Running direct sidesteps that entirely — it's detached from every one of ezconf's own systemd units, so it survives either one restarting, and you don't need the terminal panel open, or even be looking at it, to watch it run. Requires Linux/systemd.

Set `terminal = true` on a button to opt it back into the old typed-into-the-terminal behavior — for a command that specifically needs the terminal (an interactive prompt, or wanting it to run inline alongside other terminal work) and is known not to restart either of ezconf's own services:

```nix
{ label = "Disk usage"; command = "ncdu /"; terminal = true; }
```

`clear_first` clears the terminal screen right before a `terminal = true` button's command runs, and defaults to `true` — set it to `false` to instead run the command in whatever's already there; it has no effect on a direct button, which has no terminal screen to clear. The terminal service has `restartIfChanged = false`, so a rebuild that only changes the `ezconf-terminal` package itself doesn't restart the running process — whatever's running in the terminal at the time keeps going straight through that.

Closing the browser tab (or losing the connection, reloading the page, or logging out) doesn't kill the shell — reopening the terminal reattaches to the same one (replaying its recent output) rather than starting fresh, so something long-running survives an accidentally-closed tab or a step-away-and-come-back. There's only ever one such shell, shared by *everyone* who connects — not one per browser — so anyone with access sees and can type into the exact same terminal, always; that's consistent with the rest of ezconf, which has no separate identity per person either (one shared login for everyone with access). It survives a page reload and a logout/login cycle, but not a reboot or a manual restart of `ezconf-terminal.service` itself — nothing can make a shell survive the process that owns it actually dying, and anything the shell itself spawned dies right along with it (systemd's own default cgroup-based cleanup on that restart, not anything ezconf does).

Buttons set here (in your NixOS configuration, deploy-time) always show, regardless of which tab is open in the editor — they aren't tied to any one config file.

You can *also* set `services.ezconf.buttons` directly inside a config file — it's a regular NixOS option like any other, editable live in the app, and combines with (rather than replaces) whatever's set above. A button defined this way shows the same way: always, regardless of which tab is active.

These two aren't really separate: if `services.ezconf.buttons` lives inside `configDir`, it *is* the option set above — a `.json` file there gets merged straight into it, so after you save and `nixos-rebuild`, the exact same buttons start arriving from the NixOS config too. Ezconf notices and shows each one once, not twice.

Give several buttons the same `menu = "Name"` to group them into one dropdown instead of each getting its own slot in the bar:

```nix
buttons = [
  { label = "Rebuild"; command = "nixos-rebuild switch --flake /etc/nixos"; save_first = true; menu = "Deploy"; }
  { label = "Boot";    command = "nixos-rebuild boot --flake /etc/nixos";   save_first = true; menu = "Deploy"; }
  { label = "Test";    command = "nixos-rebuild test --flake /etc/nixos";   save_first = true; menu = "Deploy"; }
];
```

That shows a single "Deploy" button; clicking it opens a dropdown of "Rebuild"/"Boot"/"Test". Each item still honors its own `save_first`/`clear_first`/`terminal` independently — all three run direct here (the default), which is fine for all of them: `Rebuild` and `Test` both activate the new configuration on the running system, so direct is what keeps them from being able to kill their own execution mid-command, while `Boot` only sets the next-boot default without touching anything currently running, so direct isn't strictly necessary for it but doesn't hurt either.

Use `/` in `menu` to nest a submenu inside that dropdown, e.g. `menu = "Disk/Advanced";` adds an "Advanced" item to the "Disk" dropdown that opens a further flyout — handy for burying rarely-used or destructive commands (like a partition wipe) a click deeper than the everyday ones.

Set `mode = "install"` on a button, and set `mode = "install"` (or `services.ezconf.mode = "install";` in the NixOS module) at the top level too, to keep some buttons out of the ordinary row entirely until an install image needs them:

```nix
mode = "install"; # deploy-time only — no in-GUI toggle
buttons = [
  { label = "Rebuild"; command = "nixos-rebuild switch --flake /etc/nixos"; save_first = true; }
  { label = "Wipe disk and reinstall"; command = "disko-install --flake /etc/nixos"; mode = "install"; }
];
```

With the top-level `mode` set this way, "Wipe disk and reinstall" shows up in its own row, and every ordinary button (like "Rebuild") is shown too but greyed out. This is baked into the page at load — there's no in-GUI toggle, since it's meant for a dedicated install image, not something to flip on an already-running instance.

Running that "Rebuild" button (or `nixos-rebuild switch` from anywhere) restarts `ezconf.service` itself, not just the terminal session — the page you're looking at is still running the old code. Ezconf notices on its own — but most rebuilds don't actually change anything about ezconf's own settings, so most of the time nothing visible happens at all (or, if `buttons` did change, they just quietly update in place). A real reload only happens if something that genuinely can't be applied live changed too: theme, terminal/autocomplete/backup availability, the target flake path, or — this is also how it picks up an ezconf version upgrade itself, not just a settings change — the actual frontend code (HTML/CSS/JS). Even then, it reloads immediately if you have nothing unsaved, or once you save/undo back to clean if you do, rather than reload out from under you.

## 🔒 HTTPS

HTTPS is enabled by default. When no `cert` or `key` are provided a local CA and certificate are generated automatically in `/var/lib/ezconf/`. With `installCerts = true` (the default) the CA is installed into `~/.pki/nssdb` (Chrome/Chromium-family browsers) and into each detected Firefox profile's own certificate database, for each user in `certUsers` (defaults to `auth.allowedUsers`), so browsers trust it without a warning. Set `certUsers` explicitly if you log into ezconf as a different OS user than the one whose browser you actually use — e.g. logging in as `root` over PAM while browsing as your own normal user account.

The login page shows a **Download CA certificate** link when a generated CA is available — use this to import the CA into browsers or devices that aren't covered by `installCerts` (e.g. macOS or other machines on the network). The CA is stable and never regenerated unless deleted, so this is a one-time import. The server cert is regenerated automatically when `listen` or `certNames` change, with no browser action needed.

To use your own certificate:

```nix
services.ezconf = {
  enable = true;
  https  = true;
  cert   = "/path/to/cert.pem";
  key    = "/path/to/key.pem";
};
```

For dev use without the NixOS module:

```sh
# Local CA + cert (install localhost-ca.pem in your browser once)
python3 bin/server.py --generate-ca

# Or self-signed (browser will warn)
python3 bin/server.py --generate-cert
```

## 🌐 Accessing from other devices

To reach ezconf from other devices on your network, either set `listen` to a static LAN IP or `0.0.0.0`, or set `interfaces` to the NIC(s) you want reachable and leave `listen` alone — `listen` then defaults to `0.0.0.0` automatically (only when `interfaces` is non-empty; it stays `127.0.0.1` otherwise). Either way, the firewall is opened automatically and a TLS certificate is generated.

`interfaces` is the better fit on a DHCP-configured machine, where the interface *name* is stable but the IP isn't — the firewall rule (and thus actual reachability) is scoped by interface, not by an address that can drift on the next lease renewal. It's enough on its own:

```nix
services.ezconf = {
  enable     = true;
  interfaces = [ "enp3s0" ];   # listen defaults to 0.0.0.0 because this is set
  auth.allowedUsers = [ "alice" ];
};
```

Setting `interfaces` also defaults `trustedHosts` to `[ "*" ]` (disabling the Host-header check that otherwise protects every save/import against DNS-rebinding CSRF) *unless* you give it `certNames` instead, since a DHCP address isn't worth pinning either the cert or the Host check to and there's usually no fixed hostname to give it. If you do have a stable one (mDNS/avahi, a DNS entry, a reservation), set it and you get real protection back along with a cert that actually matches it:

```nix
services.ezconf = {
  enable     = true;
  interfaces = [ "enp3s0" ];
  certNames  = [ "myhost.local" ];   # covers both the cert SAN and the Host check
  auth.allowedUsers = [ "alice" ];
};
```

Use `listen` directly when the machine has a fixed IP instead:

```nix
services.ezconf = {
  enable    = true;
  listen    = "192.168.1.2";   # included in the generated cert's SANs automatically
  auth.allowedUsers = [ "alice" ];
};
```

Either way, the editor ends up reachable at `https://<address>:9090` from any device on the network (or, with `interfaces` set, from any device reaching it through one of those NICs specifically).

**Trusting the certificate on other devices**: the login page shows a **Download CA certificate** link. Download `ezconf-ca.pem` and import it once on each device:

- **macOS**: open the file in Keychain Access → set trust to *Always Trust*
- **Windows**: double-click → *Install Certificate* → *Local Machine* → *Trusted Root Certification Authorities*
- **Firefox (any OS)**: Settings → Privacy & Security → View Certificates → Authorities → Import
- **Android**: Settings → Security → Install from storage

The CA never changes, so this is a one-time step per device.

If you access ezconf by hostname rather than IP, add the hostname to `certNames` so the certificate covers it:

```nix
services.ezconf = {
  enable    = true;
  listen    = "192.168.1.2";
  certNames = [ "myserver.local" ];
};
```

## 🔄 Autocomplete Data

The editor loads NixOS option, package, and kernel data from `autocomplete_dir` if set in the config, otherwise `autocomplete/` under the webroot. The NixOS module sets `autocomplete_dir` to `/var/lib/ezconf/autocomplete/` and generates the data on first start, unless `generateAutocomplete = false`. To regenerate from the UI, the `↻ Autocomplete` button appears automatically when `mkoptions` is configured (the module sets this up) — this is also how to populate it the first time if `generateAutocomplete` was turned off. If the run fails, or completes with warnings (e.g. an option that failed to evaluate and was skipped), the full output is shown in a popup — no need for the terminal panel to see what went wrong.

For standalone use:

```sh
# Writes to webroot/autocomplete/ by default
nix run .#ezconf-mkoptions

# Against a specific flake + hostname
TARGET=/path/to/flake nix run .#ezconf-mkoptions -- all myhostname
```

## 💾 Backups

Every time a config file is saved, the server copies its previous contents into a backup directory, keeping the `backup_count` most recent copies *per file* (default 5; set to 0 to disable). Backups are per-tab — with multiple config files, each gets its own independent history.

- **A file's own backups** live on that tab's own right-click menu — **Restore**, alongside Export/Disable/Delete — once file backups are enabled. Each entry is labeled with its timestamp and size; picking one loads it straight into the editor as an unsaved edit, no confirmation prompt, and it doesn't have to be the currently-open tab — restoring into a different file switches to it. Nothing is written to disk until you hit Save, and you can review or tweak it first (or Undo to go back).
- **The whole system's backups** live under the header's **🕐 Restore** button, which appears automatically once system backups are enabled. Every existing backup of the *entire* NixOS config tree (`nixos_target`), not just what's open in the editor: flake.nix, flake.lock, and everything else alongside ezconf's own files, using the same file selection as system export/import (symlinks always excluded; dotfiles/dotdirs and `hardware-configuration.nix` excluded by default, see [Export / Import](#-export--import)). There's no manual "back up now" action — a system backup is only ever made automatically, right before a system import or another system restore overwrites something (see below), so this list is really a history of what the tree looked like just before each of those. Old ones are pruned to `system_backup_count` (default 5), keeping the newest. Clicking one **restores** it directly (there's no separate download action — a system backup is only ever meant to be restored, not exported for its own sake).

Restoring writes the backup straight into `nixos_target` immediately, with a confirmation prompt first — the same class of irreversible action as **⬇ Import → System**, but stronger: restoring also *deletes* any file that isn't in the backup, so the tree ends up exactly as it was when the backup was taken, not just merged with whatever's there now. (A plain **⬇ Import → System** never deletes anything — that stays a pure merge, since an arbitrary uploaded zip isn't necessarily built with the same file selection a backup always has.)

Every system import or restore backs up the current tree automatically, first, before writing anything — so there's always a way back to what was there a moment before the overwrite, with nothing you need to have remembered to do beforehand.

Standalone: set `backup_dir` / `backup_count` in `ezconf.toml`, or pass `--backup-dir` / `--backup-count`. Backups default to a shared `.ezconf-backups/` directory inside the config directory (one subset per file). The NixOS module stores them in `/var/lib/ezconf/backups` by default (`backupDir` / `backupCount` options).

The whole-`nixos_target` backups work the same way, just manual instead of on-save (aside from the automatic pre-import/restore snapshot above): `system_backup_dir` / `system_backup_count` in `ezconf.toml`, or `--system-backup-dir` / `--system-backup-count`, defaulting to a shared `.ezconf-system-backups/` directory inside the config directory (`systemBackupDir` / `systemBackupCount` in the NixOS module, default `/var/lib/ezconf/system-backups`).

## 🎨 Theme

```nix
services.ezconf = {
  enable = true;
  theme  = "dark";  # nixos (default) | dark | light
};
```

## ⚙️ NixOS Module Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | bool | `false` | Enable ezconf |
| `user` / `group` | str | `"root"` | User and group for the services |
| `configDir` | str | `"/etc/nixos/ezconf"` | Directory for the `*.json` tabs and `default.nix`; starts empty — create your first file in the editor |
| `defaultFile` | str | `"configuration.json"` | File (relative to `configDir`) preselected in the editor when a browser has no prior tab remembered; a hint only, nothing creates it automatically |
| `webroot` | str | `"${package}/share/ezconf"` | Directory to serve static assets from |
| `auth.method` | str | `"auto"` | `auto`, `pam`, or `custom` |
| `auth.username` | str or null | `null` | Username for `custom` auth |
| `auth.password` | str or null | `null` | Password for `custom` auth (stored in Nix store — prefer `passwordFile`) |
| `auth.passwordFile` | path or null | `null` | File containing the password for `custom` auth |
| `auth.allowedUsers` | list of str | `[]` | Users allowed to log in (PAM mode); defaults to the service user |
| `theme` | str | `"nixos"` | `nixos`, `dark`, or `light` |
| `mode` | null or `"install"` | `null` | Set to `"install"` to show `mode = "install"` buttons in their own row and grey out ordinary ones, from page load — deploy-time only, no in-GUI toggle |
| `terminal` | bool | `true` | Enable terminal panel and `ezconf-terminal.service`. The shared shell always persists across a dropped/closed connection (browser closed, network drop, logout) — reconnecting reattaches instead of starting fresh |
| `buttons` | list | `[]` | Shortcut buttons shown in the terminal panel |
| `https` | bool | `true` | Enable HTTPS |
| `generateCert` | bool | auto | Generate a local CA + cert in `/var/lib/ezconf/` (set automatically when `https = true` and no cert/key provided) |
| `certNames` | list of str | `[]` | Extra hostnames or IPs to include in the generated cert (e.g. `[ "myserver.local" ]`); `localhost`, `127.0.0.1`, and `listen` are always included |
| `installCerts` | bool | `true` | Install generated CA into `~/.pki/nssdb` and each Firefox profile's own database, for each user in `certUsers` |
| `certUsers` | list of str | `[]` (falls back to `auth.allowedUsers`) | OS users to install the generated CA for; set separately from `allowedUsers` if you log in as a different user than the one browsing |
| `cert` | str or null | `null` | Path to TLS certificate (PEM) |
| `key` | str or null | `null` | Path to TLS private key (PEM) |
| `listen` | str or null | `null` | IP address to listen on (default: `127.0.0.1`, or `0.0.0.0` automatically when `interfaces` is set; use `0.0.0.0` explicitly for all interfaces with no `interfaces` restriction) |
| `openFirewall` | bool | `false` | Open the web service's firewall port; enabled automatically when `listen` is set to a non-localhost address. The terminal service's port is never opened — it only binds `127.0.0.1` and is reached through the web service's own port |
| `interfaces` | list of str | `[]` | Network interfaces to open firewall ports on (e.g. `[ "eth0" "wg0" ]`); when set, ports are opened only on those interfaces instead of all interfaces |
| `trustedHosts` | list of str | `[]` (or `[ "*" ]` when `interfaces` is set and `certNames` isn't) | Extra hostnames trusted for CSRF check — required when behind a reverse proxy; `listen` and `certNames` are trusted automatically. `[ "*" ]` disables the check entirely (accepts any Host header) — for cases like an installer ISO, or a DHCP machine with no fixed address to trust ahead of time |
| `nixosTarget` | str | `"/etc/nixos"` | Flake path passed to `ezconf-mkoptions`; also what system export zips up |
| `generateAutocomplete` | bool | `true` | Run `ezconf-mkoptions` automatically the first time the service starts (when `/var/lib/ezconf/autocomplete` doesn't exist yet). Set `false` to skip this and rely on the `↻ Autocomplete` button instead — useful if evaluating `nixosTarget` is slow enough to be worth not doing unconditionally on every fresh boot/state wipe |
| `systemExportExcludeDotfiles` | bool | `true` | Exclude dotfiles/dotdirs from the system export zip |
| `systemExportExclude` | list of str | `["hardware-configuration.nix"]` | Basenames the system export zip always skips, anywhere in the tree |
| `backupDir` | str | `"/var/lib/ezconf/backups"` | Directory to store config file backups (one subset per file) |
| `backupCount` | int | `5` | Number of backups to keep, made on every save; `0` disables backups |
| `systemBackupDir` | str | `"/var/lib/ezconf/system-backups"` | Directory to store whole-`nixosTarget` zip backups, made automatically right before a system import or restore overwrites anything |
| `systemBackupCount` | int | `5` | Number of system backups to keep; `0` hides the feature entirely |
| `ports.web` | port | `9090` | Web server port |
| `ports.terminal` | port | `9091` | Terminal WebSocket port. Internal only — the terminal service binds `127.0.0.1` on this port and is never reached directly; the browser always connects through the web service's own port instead, which proxies it through over loopback |

## 📝 Notes

- `configDir` is created automatically with a `default.nix` that applies whatever `*.json` files end up there — it starts with none; create your first one from the editor (right-click the tab bar, or the empty editor area, for "New file"). Add `./ezconf` to your `nixosSystem` modules list in `flake.nix` to wire it in.
- Autocomplete data is generated on first service start into `/var/lib/ezconf/autocomplete/` (set `generateAutocomplete = false` to skip this) and can be refreshed from the UI.
- The terminal service has `restartIfChanged = false` — it forks the shell directly as its own child, so restarting it (e.g. `systemctl restart ezconf-terminal` to pick up a package update) kills whatever's running inside it. A rebuild never does this automatically; restart it yourself when you need to pick up a change. A **"Terminal needs a restart"** notification appears in the top-right corner (alongside the usual reload notification, if that's showing too) whenever the running terminal service is actually out of date — click it to restart, same one-command effect as above. There's also a plain restart icon next to the terminal panel's own size buttons for restarting it on demand any time, whether or not that notification is showing.
- `auth.password` is stored in the Nix store (world-readable). Use `auth.passwordFile` for anything real.
- PAM mode defaults `allowedUsers` to the user running the service if the list is empty.
- The editor always requires authentication — there is no unauthenticated mode.
- The service runs as `root` by default. This is intentional — it allows the terminal panel to run `nixos-rebuild` and other system commands without additional privilege escalation.

_Edit your NixOS configuration from a browser — autocompletion and documentation built in._
