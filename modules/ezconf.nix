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
  shell     = (config.users.users.${cfg.user} or {}).shell or null;
  shellPath = s: "${s}${s.shellPath}";

  # Fixed rather than user-configurable: there's only ever one terminal session per deployment,
  # so a name just needs to not collide with anything else on the same tmux server.
  tmuxSession = "ezconf";

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
    (lib.optional cfg.terminalPersist "tmux_session = ${str tmuxSession}")
    (lib.optional (cfg.mode != null) "mode = ${str cfg.mode}")
    "session_key_file = ${str "/var/lib/ezconf/session.key"}"
    "backup_dir = ${str cfg.backupDir}"
    "backup_count = ${toString cfg.backupCount}"
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
    (map (btn: "\n[[buttons]]\nlabel = ${str btn.label}\ncommand = ${str btn.command}${lib.optionalString btn.save_first "\nsave_first = true"}${lib.optionalString btn.clear_first "\nclear_first = true"}${lib.optionalString (btn.menu != "") "\nmenu = ${str btn.menu}"}${lib.optionalString (btn.mode != null) "\nmode = ${str btn.mode}"}") cfg.buttons)
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

    terminalPersist = lib.mkOption {
      type        = lib.types.bool;
      default     = false;
      description = "Run the terminal's shell inside a persistent tmux session (ezconf-terminal-session.service, its own systemd unit/cgroup) instead of forking a fresh shell per connection, so a running command survives ezconf-terminal.service restarting (e.g. after nixos-rebuild switch) or a browser reconnect; a (re)connecting client also gets the session's recent tmux scrollback replayed so it doesn't miss what happened while disconnected. Requires tmux; only takes effect when terminal = true. Changing this option itself must be applied via nixos-rebuild switch run from outside the ezconf terminal panel (e.g. SSH or console) -- switching it either direction tears down whichever architecture is currently hosting the connection that's running the switch, killing that command mid-flight if run from inside the panel.";
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

      systemd.services.ezconf-terminal = lib.mkIf cfg.terminal {
        description       = "ezconf terminal WebSocket service";
        wantedBy          = [ "multi-user.target" ];
        after             = [ "ezconf.service" ]
          ++ lib.optionals cfg.terminalPersist [ "ezconf-terminal-session.service" "ezconf-terminal-configure.service" ];
        # Without terminalPersist, restarting this unit kills whatever's running inside it, so a
        # rebuild must never do that automatically -- hence false. With it, the shell lives in
        # ezconf-terminal-session.service instead (a separate cgroup), so restarting this one
        # only drops the viewing WebSocket connection for a moment; reconnecting re-attaches to
        # the same session with nothing lost. Safe to let rebuilds restart it in that case, so it
        # actually picks up package/config changes instead of never restarting at all.
        restartIfChanged  = cfg.terminalPersist;
        serviceConfig = {
          User      = cfg.user;
          Group     = cfg.group;
          ExecStart = "${termPkg}/bin/ezconf-terminal --config /run/ezconf/ezconf.toml";
          Restart   = "on-failure";
        };
      };

      # The actual tmux server lives here, not in ezconf-terminal.service -- a *separate*
      # unit/cgroup is what lets it survive ezconf-terminal.service (or ezconf.service)
      # restarting: systemd's default KillMode=control-group kills every process in a unit's
      # cgroup on stop, including a daemon like tmux's server that's forked and reparented away
      # from this unit's own PID tree, since orphaning doesn't move a process to a different
      # cgroup. Putting the session in its own unit sidesteps that entirely.
      #
      # ExecStart is a *bare* tmux invocation, deliberately never referencing ${termPkg} (or
      # anything else that changes when ezconf's own code does) -- restartIfChanged = false is a
      # backstop, but the real guarantee here is that this unit's definition simply doesn't change
      # across ezconf updates at all, so there's nothing for a rebuild to even consider restarting
      # in the first place. Only a tmux package bump (or changing services.ezconf.shell) would.
      # All of the actual tmux *configuration* (scrollback, mouse, key bindings, remain-on-exit)
      # lives in ezconf-terminal-configure.service instead, precisely so it's free to reference
      # ${termPkg} and restart normally -- it holds nothing persistent of its own.
      #
      # The leading "-" tolerates tmux's own "duplicate session" exit code on every re-run once
      # the session already exists (the common case) -- without it, each periodic re-run below
      # would otherwise report as a failed unit for doing nothing wrong.
      # Type=oneshot + RemainAfterExit=true: this creates the session and exits immediately -- the
      # tmux server it spawned keeps running independently in this unit's cgroup, so the unit
      # itself has nothing to stay running as.
      systemd.services.ezconf-terminal-session = lib.mkIf (cfg.terminal && cfg.terminalPersist) {
        description       = "ezconf persistent terminal session (tmux)";
        wantedBy          = [ "multi-user.target" ];
        restartIfChanged  = false;
        serviceConfig = {
          Type            = "oneshot";
          RemainAfterExit = true;
          User            = cfg.user;
          Group           = cfg.group;
          Environment     = "TERM=xterm-256color";
          ExecStart       = "-${pkgs.tmux}/bin/tmux new-session -d -s ${tmuxSession}"
            + lib.optionalString (shell != null) " ${shellPath shell} -l";
        };
      };

      # Periodically re-fires the oneshot service above (a bare `tmux new-session -d`, itself
      # unchanged) so the session gets recreated if the tmux *server itself* ever disappears --
      # e.g. someone runs `tmux kill-server` directly, or an OOM kill takes out the server
      # process. That's distinct from a single pane's shell exiting, which remain-on-exit/
      # pane-died (configured by ezconf-terminal-configure.service, not this one) already recovers
      # from immediately, no waiting on any timer. A once-a-minute check is deliberately not
      # aggressive: this is a safety net for a rare, usually self-inflicted event, not a hot path.
      systemd.timers.ezconf-terminal-session = lib.mkIf (cfg.terminal && cfg.terminalPersist) {
        description = "Periodically ensure the persistent ezconf terminal session still exists";
        wantedBy    = [ "timers.target" ];
        timerConfig = {
          OnUnitActiveSec = "60s";
          Unit            = "ezconf-terminal-session.service";
        };
      };

      # All of the actual tmux *configuration* -- separated from ezconf-terminal-session.service
      # above specifically so this unit (which references ${termPkg}, unlike that one) is free to
      # restart normally on every ezconf update instead of needing restartIfChanged = false itself:
      # it holds nothing persistent, just reapplies settings to a session created elsewhere, so
      # there's nothing lost by restarting it whenever it likes.
      systemd.services.ezconf-terminal-configure = lib.mkIf (cfg.terminal && cfg.terminalPersist) {
        description = "Apply ezconf's tmux session settings (scrollback, mouse, key bindings, remain-on-exit)";
        wantedBy    = [ "multi-user.target" ];
        after       = [ "ezconf.service" "ezconf-terminal-session.service" ]; # needs the toml (ezconf.service's preStart) and a session to configure
        serviceConfig = {
          Type      = "oneshot";
          User      = cfg.user;
          Group     = cfg.group;
          ExecStart = "${termPkg}/bin/ezconf-terminal --config /run/ezconf/ezconf.toml --configure-session";
        };
      };

      # Same idea as ezconf-terminal-session.timer -- reapplies settings periodically too, so a
      # session recreated by that timer (after the tmux server disappeared) doesn't sit
      # unconfigured (status bar visible, no mouse mode, no remain-on-exit) until the *next*
      # config change happens to restart this unit.
      systemd.timers.ezconf-terminal-configure = lib.mkIf (cfg.terminal && cfg.terminalPersist) {
        description = "Periodically reapply ezconf's tmux session settings";
        wantedBy    = [ "timers.target" ];
        timerConfig = {
          OnUnitActiveSec = "60s";
          Unit            = "ezconf-terminal-configure.service";
        };
      };
  };
}
