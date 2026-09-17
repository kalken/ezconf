self:
{ config, lib, pkgs, ... }:
let
  cfg      = config.services.ezconf;
  p        = import ./ezconf-packages.nix { inherit pkgs; version = self.shortRev or "dev"; };
  package  = p.ezconf;
  termPkg  = p."ezconf-terminal";
  mkoptions = p."ezconf-mkoptions";

  esc       = s: lib.replaceStrings [ ''"'' "\\" ] [ ''\"'' "\\\\" ] s;
  str       = s: ''"${esc s}"'';
  toml-list = xs: "[${lib.concatMapStringsSep ", " str xs}]";

  # The service user's own configured shell, not an ezconf-specific option -- users.users.<name>
  # .shell already defaults to users.defaultUserShell for any user without its own override, so
  # this transparently respects either a per-user shell or a system-wide default shell change
  # with no need for ezconf to duplicate that resolution logic (or offer a separate, easily-
  # forgotten override that would silently diverge from it). `or {}`/`or null` guard cfg.user not
  # being a NixOS-managed account at all (e.g. one created some other way). shellPath resolves it
  # to the actual executable path via the package's own shellPath, same convention nixpkgs
  # already defines for common shells.
  #
  # pkgs.shadow is nixpkgs' own placeholder for "no real shell configured" -- users.defaultUserShell
  # (and so users.users.<name>.shell, root included) defaults to it on any system that hasn't set
  # a real one. It's a legitimate "can't log in interactively" shell in general, but running it as
  # the terminal panel's shell would just immediately print its message and exit. Treated the same
  # as unset, falling back to terminal.py's own SHELL resolution (the account's /etc/passwd entry,
  # then $SHELL, then /bin/sh) instead of passing this broken value through explicitly.
  shell = let s = (config.users.users.${cfg.user} or {}).shell or null;
          in if s == null || s == pkgs.shadow then null else s;
  shellPath = s: "${s}${s.shellPath}";

  preStartScript = p.mkPrestart { inherit cfg staticToml mkoptions package; };

  staticToml = pkgs.writeText "ezconf.toml" (lib.concatLines (lib.flatten [
    "file = ${str cfg.configDir}"
    "default_file = ${str cfg.defaultFile}"
    "webroot = ${str cfg.webroot}"
    "autocomplete_dir = ${str "/var/lib/ezconf/autocomplete"}"
    "mkoptions = ${str "${mkoptions}/bin/ezconf-mkoptions"}"
    "nixos_target = ${str cfg.nixosTarget}"
    "system_export_exclude_dotfiles = ${lib.boolToString cfg.systemExportExcludeDotfiles}"
    "system_export_exclude = ${toml-list cfg.systemExportExclude}"
    "auth = ${str cfg.auth.method}"
    "theme = ${str cfg.theme}"
    (lib.optional (cfg.mode != null) "mode = ${str cfg.mode}")
    "session_key_file = ${str "/var/lib/ezconf/session.key"}"
    "backup_dir = ${str cfg.backupDir}"
    "backup_count = ${toString cfg.backupCount}"
    "system_backup_dir = ${str cfg.systemBackupDir}"
    "system_backup_count = ${toString cfg.systemBackupCount}"
    (lib.optional cfg.terminal "terminal_port = ${toString cfg.ports.terminal}")
    (lib.optional (cfg.auth.username     != null) "username = ${str cfg.auth.username}")
    (lib.optional (cfg.auth.password     != null) "password = ${str cfg.auth.password}")
    (lib.optional (cfg.auth.allowedUsers != [])   "allowed_users = ${toml-list cfg.auth.allowedUsers}")
    (lib.optionalString cfg.https
      (if cfg.generateCert then "cert = ${str "/var/lib/ezconf/localhost.pem"}"
       else lib.optionalString (cfg.cert != null) "cert = ${str cfg.cert}"))
    (lib.optionalString cfg.https
      (if cfg.generateCert then "key = ${str "/var/lib/ezconf/localhost-key.pem"}"
       else lib.optionalString (cfg.key != null) "key = ${str cfg.key}"))
    (lib.optional cfg.generateCert "ca_file = ${str "/var/lib/ezconf/ca.pem"}")
    (lib.optional (shell                 != null) "shell = ${str (shellPath shell)}")
    (lib.optional (cfg.listen           != null) "listen = ${str cfg.listen}")
    (let allTrusted = cfg.trustedHosts ++ cfg.certNames;
     in lib.optional (allTrusted != []) "trusted_hosts = ${toml-list allTrusted}")
    ""
    "[ports]"
    "web = ${toString cfg.ports.web}"
    (map (btn: "\n[[buttons]]\nlabel = ${str btn.label}\ncommand = ${str btn.command}${lib.optionalString btn.save_first "\nsave_first = true"}${lib.optionalString btn.clear_first "\nclear_first = true"}${lib.optionalString (btn.menu != "") "\nmenu = ${str btn.menu}"}${lib.optionalString (btn.mode != null) "\nmode = ${str btn.mode}"}${lib.optionalString btn.static "\nstatic = true"}") cfg.buttons)
  ]));

in
{

  options.services.ezconf = {
    enable = lib.mkEnableOption "ezconf NixOS configuration editor";

    user = lib.mkOption {
      type        = lib.types.str;
      default     = "root";
      description = "User to run the services as.";
    };

    group = lib.mkOption {
      type        = lib.types.str;
      default     = "root";
      description = "Group to run the services as.";
    };

    configDir = lib.mkOption {
      type        = lib.types.str;
      default     = "/etc/nixos/ezconf";
      description = "Directory for the *.json config files (tabs) plus default.nix. Starts empty — the editor's UI is used to create the first file. Should be inside the system flake so pure evaluation can read it. Any *.json file here (besides custom-options.json) is an independently editable tab in the UI, merged at eval time.";
    };

    defaultFile = lib.mkOption {
      type        = lib.types.str;
      default     = "configuration.json";
      description = "File (relative to configDir) to prefer as the initially-selected tab when the editor opens and no file has been picked before in that browser. Purely a hint — nothing creates this file automatically; configDir starts empty and the editor explains how to create the first file.";
    };

    webroot = lib.mkOption {
      type        = lib.types.str;
      default     = "${package}/share/ezconf";
      description = "Directory to serve static assets from.";
    };

    nixosTarget = lib.mkOption {
      type        = lib.types.str;
      default     = "/etc/nixos";
      description = "Flake path passed as TARGET to ezconf-mkoptions when generating autocomplete data.";
    };

    systemExportExcludeDotfiles = lib.mkOption {
      type        = lib.types.bool;
      default     = true;
      description = "Exclude dotfiles/dotdirs (.git, .ssh, age/sops keys, etc.) from the \"Export system\" zip.";
    };

    systemExportExclude = lib.mkOption {
      type        = lib.types.listOf lib.types.str;
      default     = [ "hardware-configuration.nix" ];
      description = "Basenames to exclude from the \"Export system\" zip, anywhere in the tree. Defaults to hardware-configuration.nix, since it's machine-specific and shouldn't be bundled into a config meant to be reused elsewhere.";
    };

    backupDir = lib.mkOption {
      type        = lib.types.str;
      default     = "/var/lib/ezconf/backups";
      description = "Directory to store configuration.json backups.";
    };

    backupCount = lib.mkOption {
      type        = lib.types.ints.unsigned;
      default     = 5;
      description = "Number of backups to keep, made on every save. 0 disables backups.";
    };

    systemBackupDir = lib.mkOption {
      type        = lib.types.str;
      default     = "/var/lib/ezconf/system-backups";
      description = "Directory to store whole-nixosTarget zip backups, made on demand via the editor's Export menu (not automatically, unlike backupDir).";
    };

    systemBackupCount = lib.mkOption {
      type        = lib.types.ints.unsigned;
      default     = 5;
      description = "Number of system backups to keep. 0 disables the feature (hides the UI action for it).";
    };

    auth = {
      method = lib.mkOption {
        type        = lib.types.enum [ "auto" "pam" "custom" ];
        default     = "auto";
        description = "Authentication method. \"auto\" uses PAM if available, else custom. \"pam\" uses system credentials. \"custom\" uses username/password from config.";
      };

      username = lib.mkOption {
        type        = lib.types.nullOr lib.types.str;
        default     = null;
        description = "Username for auth.method = \"custom\".";
      };

      password = lib.mkOption {
        type        = lib.types.nullOr lib.types.str;
        default     = null;
        description = "Password for auth.method = \"custom\". Stored in the Nix store — use passwordFile for secrets.";
      };

      passwordFile = lib.mkOption {
        type        = lib.types.nullOr lib.types.path;
        default     = null;
        description = "File containing the password for auth.method = \"custom\". Read at service start.";
      };

      allowedUsers = lib.mkOption {
        type        = lib.types.listOf lib.types.str;
        default     = [];
        description = "Users allowed to log in (PAM mode only). Defaults to the user running the service.";
      };
    };

    theme = lib.mkOption {
      type        = lib.types.enum [ "nixos" "dark" "light" ];
      default     = "nixos";
      description = "UI theme. \"nixos\" (dark blue), \"dark\" (black), or \"light\" (white).";
    };

    mode = lib.mkOption {
      type        = lib.types.nullOr (lib.types.enum [ "install" ]);
      default     = null;
      description = "Set to \"install\" to show buttons with mode = \"install\" (see the buttons option) in their own row, with ordinary buttons shown too but greyed out. Deploy-time only, baked into index.html on load — not a runtime toggle, so it's meant for a dedicated install image rather than something to flip on an already-running instance.";
    };

    terminal = lib.mkOption {
      type    = lib.types.bool;
      default = true;
    };

    https = lib.mkOption {
      type        = lib.types.bool;
      default     = true;
      description = "Enable HTTPS. When neither cert nor key are set, a local CA and certificate are generated automatically.";
    };

    generateCert = lib.mkOption {
      type        = lib.types.bool;
      default     = false;
      description = "Generate a local CA and TLS certificate in /var/lib/ezconf/. Set automatically when https = true and no cert/key are provided; override to false to disable.";
    };

    certNames = lib.mkOption {
      type        = lib.types.listOf lib.types.str;
      default     = [];
      description = "Extra hostnames or IP addresses to include in the generated TLS certificate SANs. Only applies when generateCert = true. localhost and 127.0.0.1 are always included; listen is included automatically.";
    };

    installCerts = lib.mkOption {
      type        = lib.types.bool;
      default     = true;
      description = "Install the generated CA certificate into ~/.pki/nssdb for each user in auth.allowedUsers so web browsers trust it. Only has effect when generateCert = true.";
    };

    cert = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = null;
      description = "Path to TLS certificate (PEM). Requires https = true. Ignored when generateCert = true.";
    };

    key = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = null;
      description = "Path to TLS private key (PEM). Requires https = true. Ignored when generateCert = true.";
    };

    listen = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = null;
      description = "IP address to listen on (default: 127.0.0.1). Set to 0.0.0.0 to listen on all interfaces.";
    };

    openFirewall = lib.mkOption {
      type        = lib.types.bool;
      default     = false;
      description = "Open firewall ports for the web and terminal services. Enabled automatically when listen is set to a non-localhost address.";
    };

    interface = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = null;
      description = "Network interface to open firewall ports on (e.g. \"eth0\"). When set, ports are opened only on that interface; when unset, ports are opened on all interfaces.";
    };

    trustedHosts = lib.mkOption {
      type        = lib.types.listOf lib.types.str;
      default     = [];
      description = "Hostnames trusted for CSRF check. Required when ezconf is behind a reverse proxy — add your nginx server_name here. Set to [ \"*\" ] to disable the check entirely (accept any Host header) when the reachable address can't be known ahead of time, e.g. an installer ISO getting a DHCP lease.";
    };

    ports = {
      web      = lib.mkOption { type = lib.types.port; default = 9090; };
      terminal = lib.mkOption { type = lib.types.port; default = 9091; };
    };

    buttons = lib.mkOption {
      type = lib.types.listOf (lib.types.submodule {
        options = {
          label       = lib.mkOption { type = lib.types.str;  description = "Button label shown in the UI."; };
          command     = lib.mkOption { type = lib.types.str;  description = "Shell command to run in the terminal."; };
          save_first  = lib.mkOption { type = lib.types.bool; default = false; description = "Disable the button while there are unsaved changes."; };
          clear_first = lib.mkOption { type = lib.types.bool; default = false; description = "Clear the terminal before running this button's command."; };
          menu        = lib.mkOption { type = lib.types.str;  default = "";    description = "Group this button into a dropdown menu with this name, instead of giving it its own slot in the button bar. Every button sharing the same menu name appears as one item in that dropdown. Use \"/\" to nest further, e.g. \"Disk/Advanced\" adds an \"Advanced\" submenu inside the \"Disk\" dropdown."; };
          mode        = lib.mkOption { type = lib.types.nullOr (lib.types.enum [ "install" ]); default = null; description = "Set to \"install\" to move this button into its own row, shown only when services.ezconf.mode = \"install\"."; };
          static      = lib.mkOption { type = lib.types.bool; default = false; description = "Show this deploy-time button unconditionally. Only meaningful for a button declared directly in Nix (not through an ezconf-managed *.json file): the terminal panel otherwise only shows deploy-time buttons that are also currently present in a loaded *.json file (so editing that file's buttons is a live preview with no stale duplicates), and shows every *.json-declared button regardless of this option."; };
        };
      });
      default     = [];
      description = "Buttons shown in the terminal panel. Requires terminal = true.";
    };
  };

  config = lib.mkIf cfg.enable {
      services.ezconf.generateCert  = lib.mkDefault (cfg.https && cfg.cert == null && cfg.key == null);
      services.ezconf.openFirewall  = lib.mkDefault (!builtins.elem cfg.listen [ null "127.0.0.1" "::1" ]);
      services.ezconf.installCerts  = lib.mkDefault (builtins.elem cfg.listen [ null "127.0.0.1" "::1" ]);

      networking.firewall = lib.mkIf cfg.openFirewall (
        let ports = [ cfg.ports.web ] ++ lib.optional cfg.terminal cfg.ports.terminal;
        in if cfg.interface != null
           then { interfaces.${cfg.interface}.allowedTCPPorts = ports; }
           else { allowedTCPPorts = ports; }
      );

      assertions = [
        {
          assertion = cfg.auth.method != "custom" || (cfg.auth.username != null && (cfg.auth.password != null || cfg.auth.passwordFile != null));
          message   = "services.ezconf: auth.method = \"custom\" requires auth.username and auth.password (or auth.passwordFile).";
        }
        {
          assertion = !(cfg.auth.password != null && cfg.auth.passwordFile != null);
          message   = "services.ezconf: set either auth.password or auth.passwordFile, not both.";
        }
        {
          assertion = (cfg.cert == null) == (cfg.key == null);
          message   = "services.ezconf: cert and key must be set together.";
        }
        {
          assertion = !cfg.https || cfg.generateCert || (cfg.cert != null && cfg.key != null);
          message   = "services.ezconf: https = true requires either cert+key or generateCert = true.";
        }
      ];

      # Deliberately does not seed defaultFile — doing so on every activation fought the
      # editor's own rename/move features: renaming defaultFile away just made the next
      # nixos-rebuild/reboot recreate it, which could then collide with a later rename back to
      # that name. An empty configDir is a valid, if inert, state — the editor's UI explains how
      # to create the first file when there are none.
      system.activationScripts.ezconf.text = ''
        mkdir -p ${cfg.configDir}
        cp ${./json2nix.nix} ${cfg.configDir}/default.nix
        chmod 644 ${cfg.configDir}/default.nix
        chown ${cfg.user}:${cfg.group} ${cfg.configDir}
        chown ${cfg.user}:${cfg.group} ${cfg.configDir}/default.nix
      '';

      systemd.services.ezconf = {
        description = "ezconf NixOS configuration editor";
        wantedBy    = [ "multi-user.target" ];
        after       = [ "network.target" ];

        serviceConfig = {
          ExecStartPre             = "+${preStartScript}";
          User                     = cfg.user;
          Group                    = cfg.group;
          ExecStart                = "${package}/bin/ezconf --config /run/ezconf/ezconf.toml";
          Restart                  = "on-failure";
          StateDirectory           = "ezconf";
          RuntimeDirectory         = "ezconf";
          RuntimeDirectoryMode     = "0700";
        };
      };

      # restartIfChanged = false: this forks the shell directly as its own child, so restarting
      # the unit kills whatever's running inside it -- a rebuild must never do that automatically.
      systemd.services.ezconf-terminal = lib.mkIf cfg.terminal {
        description       = "ezconf terminal WebSocket service";
        wantedBy          = [ "multi-user.target" ];
        after             = [ "ezconf.service" ];
        restartIfChanged  = false;
        serviceConfig = {
          User      = cfg.user;
          Group     = cfg.group;
          ExecStart = "${termPkg}/bin/ezconf-terminal --config /run/ezconf/ezconf.toml";
          Restart   = "on-failure";
        };
      };
  };
}
