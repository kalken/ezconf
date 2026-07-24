# ezconf

Graphical editor for nix configurations. Zero dependencies, no build step, no framework — just Python and a browser.

**Blog post:** [https://kalken.github.io//ezblog/#ezconf](https://kalken.github.io//ezblog/#ezconf)

## ✨ Features

- Edit NixOS configuration through a clean web UI with option autocomplete
- Inline terminal panel with configurable shortcut buttons
- PAM auth (system credentials) or custom username/password
- HTTPS with automatic local CA generation and browser trust store installation
- Three themes: NixOS blue, dark, light

## 🚀 Quick Start

Add to your flake inputs:

```nix
inputs.ezconf.url = "github:kalken/ezconf";
inputs.ezconf.inputs.nixpkgs.follows = "nixpkgs";
```

Enable the service in your NixOS configuration:

```nix
{ inputs, ... }: {
  imports = [ inputs.ezconf.nixosModules.default ];

  services.ezconf = {
    enable = true;
    auth.allowedUsers = [ "alice" ];
    buttons = [
      { label = "Rebuild"; command = "nixos-rebuild switch --flake /etc/nixos"; save_first = true; }
    ];
  };
}
```

Then add the generated config as a module in your `flake.nix`:

```nix
nixosConfigurations.myhostname = nixpkgs.lib.nixosSystem {
  modules = [
    ./configuration.nix
    ./ezconf   # created automatically on first start
  ];
};
```

After `nixos-rebuild switch` the editor is at `https://localhost:9090`. A local CA and certificate are generated automatically, and installed into the browser trust store for each user in `allowedUsers`.

> **Tip:** In Chrome or any Chromium-based browser, open the address bar menu and choose *Install page as app* to get a standalone desktop app with no browser chrome.

## 🔁 Migrating from configuration.nix

1. Enable the service and rebuild — this creates `/etc/nixos/ezconf/configuration.json`
2. Open the editor and use the import button to import your existing `configuration.nix`
3. In your `flake.nix`, comment out `./configuration.nix` and add `./ezconf` instead
4. Rebuild — your config is now managed through the editor

> **Note:** Any `imports` you had in `configuration.nix` should be moved to your `flake.nix` after migrating — the JSON-based config does not support `imports`.

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

## `{ }` Nix Expressions

Any field in the editor has a `{ }` button that switches it to raw Nix expression mode. In this mode you can type any valid Nix expression directly — useful for freeform options that don't map cleanly to a structured form, such as Samba shares or `extraConfig` strings.

Press `⌫` on the field to convert it back to its native type.

## `#` Disable Toggle

Any option or section has a `#` button that disables it. Disabled options remain fully visible in the editor (dimmed with the key struck through) but are excluded from the Nix configuration at evaluation time — the value is preserved and can be re-enabled at any time by pressing `#` again.

Disabling a section disables all of its children at once.

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

`save_first = true` disables the button while there are unsaved changes. The terminal service has `restartIfChanged = false` so active sessions survive `nixos-rebuild switch`.

## 🔒 HTTPS

HTTPS is enabled by default. When no `cert` or `key` are provided a local CA and certificate are generated automatically in `/var/lib/ezconf/`. With `installCerts = true` (the default) the CA is installed into `~/.pki/nssdb` for each user in `auth.allowedUsers` so browsers trust it without a warning.

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

To reach ezconf from other devices on your network, set `listen` to a LAN IP or `0.0.0.0` for all interfaces. The firewall is opened and a TLS certificate covering the listen address is generated automatically. Set `interface` to restrict the firewall rule to a specific network interface instead of opening the port on all interfaces:

```nix
services.ezconf = {
  enable    = true;
  listen    = "192.168.1.2";
  interface = "enp3s0";        # optional: restrict firewall rule to this interface
  auth.allowedUsers = [ "alice" ];
};
```

The editor is then reachable at `https://192.168.1.2:9090` from any device on the network.

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

The editor loads NixOS option, package, and kernel data from `autocomplete_dir` if set in the config, otherwise `autocomplete/` under the webroot. The NixOS module sets `autocomplete_dir` to `/var/lib/ezconf/autocomplete/` and generates the data on first start. To regenerate from the UI, the `↻ Autocomplete` button appears automatically when `mkoptions` is configured (the module sets this up).

For standalone use:

```sh
# Writes to webroot/autocomplete/ by default
nix run .#ezconf-mkoptions

# Against a specific flake + hostname
TARGET=/path/to/flake nix run .#ezconf-mkoptions -- all myhostname
```

## 💾 Backups

Every time `configuration.json` is saved (including a restore, see below), the server copies the previous contents into a backup directory, keeping the `backup_count` most recent copies (default 5; set to 0 to disable). The `🕐 Backups` button appears in the header automatically once backups are enabled — it lists past saves with their timestamp and size, and lets you restore any of them with one click. Restoring itself makes a backup of the current state first, so a restore is never a one-way trip.

Standalone: set `backup_dir` / `backup_count` in `ezconf.toml`, or pass `--backup-dir` / `--backup-count`. Backups default to `.ezconf-backups/` next to `configuration.json`. The NixOS module stores them in `/var/lib/ezconf/backups` by default (`backupDir` / `backupCount` options).

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
| `configDir` | str | `"/etc/nixos/ezconf"` | Directory for `configuration.json` and `default.nix` |
| `auth.method` | str | `"auto"` | `auto`, `pam`, or `custom` |
| `auth.username` | str or null | `null` | Username for `custom` auth |
| `auth.password` | str or null | `null` | Password for `custom` auth (stored in Nix store — prefer `passwordFile`) |
| `auth.passwordFile` | path or null | `null` | File containing the password for `custom` auth |
| `auth.allowedUsers` | list of str | `[]` | Users allowed to log in (PAM mode); defaults to the service user |
| `theme` | str | `"nixos"` | `nixos`, `dark`, or `light` |
| `terminal` | bool | `true` | Enable terminal panel and `ezconf-terminal.service` |
| `shell` | str or null | `null` | Shell for the terminal (defaults to the login shell of `user`) |
| `buttons` | list | `[]` | Shortcut buttons shown in the terminal panel |
| `https` | bool | `true` | Enable HTTPS |
| `generateCert` | bool | auto | Generate a local CA + cert in `/var/lib/ezconf/` (set automatically when `https = true` and no cert/key provided) |
| `certNames` | list of str | `[]` | Extra hostnames or IPs to include in the generated cert (e.g. `[ "myserver.local" ]`); `localhost`, `127.0.0.1`, and `listen` are always included |
| `installCerts` | bool | `true` | Install generated CA into `~/.pki/nssdb` for each user in `allowedUsers` |
| `cert` | str or null | `null` | Path to TLS certificate (PEM) |
| `key` | str or null | `null` | Path to TLS private key (PEM) |
| `listen` | str or null | `null` | IP address to listen on (default: `127.0.0.1`; use `0.0.0.0` for all interfaces) |
| `interface` | str or null | `null` | Network interface to open firewall ports on (e.g. `"eth0"`); when set, ports are opened only on that interface instead of all interfaces |
| `trustedHosts` | list of str | `[]` | Extra hostnames trusted for CSRF check — required when behind a reverse proxy; `listen` and `certNames` are trusted automatically |
| `nixosTarget` | str | `"/etc/nixos"` | Flake path passed to `ezconf-mkoptions` |
| `backupDir` | str | `"/var/lib/ezconf/backups"` | Directory to store `configuration.json` backups |
| `backupCount` | int | `5` | Number of backups to keep, made on every save; `0` disables backups |
| `ports.web` | port | `9090` | Web server port |
| `ports.terminal` | port | `9091` | Terminal WebSocket port |

## 📝 Notes

- `configDir` is created automatically with a `configuration.json` and a `default.nix` that applies it. Add `./ezconf` to your `nixosSystem` modules list in `flake.nix` to wire it in.
- Autocomplete data is generated on first service start into `/var/lib/ezconf/autocomplete/` and can be refreshed from the UI.
- The terminal service has `restartIfChanged = false` — active terminal sessions survive `nixos-rebuild switch`.
- `auth.password` is stored in the Nix store (world-readable). Use `auth.passwordFile` for anything real.
- PAM mode defaults `allowedUsers` to the user running the service if the list is empty.
- The editor always requires authentication — there is no unauthenticated mode.
- The service runs as `root` by default. This is intentional — it allows the terminal panel to run `nixos-rebuild` and other system commands without additional privilege escalation.

_Edit your NixOS configuration from a browser — autocompletion and documentation built in._
