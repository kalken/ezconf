_:
{ config, lib, pkgs, ... }:
let
  cfg      = config.services.ezconf;
  p        = import ./ezconf-packages.nix { inherit pkgs; };
  package  = p.ezconf;
  termPkg  = p."ezconf-terminal";
  mkoptions = p."ezconf-mkoptions";

  esc       = s: lib.replaceStrings [ ''"'' "\\" ] [ ''\"'' "\\\\" ] s;
  str       = s: ''"${esc s}"'';
  toml-list = xs: "[${lib.concatMapStringsSep ", " str xs}]";

  preStartScript = p.mkPrestart { inherit cfg staticToml mkoptions package; };

  staticToml = pkgs.writeText "ezconf.toml" (lib.concatLines (lib.flatten [
    "file = ${str "${cfg.configDir}/configuration.json"}"
    "webroot = ${str cfg.webroot}"
    "autocomplete_dir = ${str "/var/lib/ezconf/autocomplete"}"
    "mkoptions = ${str "${mkoptions}/bin/ezconf-mkoptions"}"
    "nixos_target = ${str cfg.nixosTarget}"
    "auth = ${str cfg.auth.method}"
    "theme = ${str cfg.theme}"
    "session_key_file = ${str "/var/lib/ezconf/session.key"}"
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
    (lib.optional (cfg.shell             != null) "shell = ${str cfg.shell}")
    (lib.optional (cfg.listen           != null) "listen = ${str cfg.listen}")
    (lib.optional (cfg.trustedHosts     != [])   "trusted_hosts = ${toml-list cfg.trustedHosts}")
    ""
    "[ports]"
    "web = ${toString cfg.ports.web}"
    (map (btn: "\n[[buttons]]\nlabel = ${str btn.label}\ncommand = ${str btn.command}${lib.optionalString btn.save_first "\nsave_first = true"}") cfg.buttons)
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
      description = "Directory for configuration.json and default.nix. Should be inside the system flake so pure evaluation can read it.";
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

    trustedHosts = lib.mkOption {
      type        = lib.types.listOf lib.types.str;
      default     = [];
      description = "Hostnames trusted for CSRF check. Required when ezconf is behind a reverse proxy — add your nginx server_name here.";
    };

    shell = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = null;
      description = "Shell for the terminal panel. Defaults to the login shell of the service user.";
    };

    ports = {
      web      = lib.mkOption { type = lib.types.port; default = 9090; };
      terminal = lib.mkOption { type = lib.types.port; default = 9091; };
    };

    buttons = lib.mkOption {
      type = lib.types.listOf (lib.types.submodule {
        options = {
          label      = lib.mkOption { type = lib.types.str;  description = "Button label shown in the UI."; };
          command    = lib.mkOption { type = lib.types.str;  description = "Shell command to run in the terminal."; };
          save_first = lib.mkOption { type = lib.types.bool; default = false; description = "Disable the button while there are unsaved changes."; };
        };
      });
      default     = [];
      description = "Buttons shown in the terminal panel. Requires terminal = true.";
    };
  };

  config = lib.mkIf cfg.enable {
      services.ezconf.generateCert = lib.mkDefault (cfg.https && cfg.cert == null && cfg.key == null);
      services.ezconf.openFirewall  = lib.mkDefault (!builtins.elem cfg.listen [ null "127.0.0.1" "::1" ]);

      networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall (
        [ cfg.ports.web ] ++ lib.optional cfg.terminal cfg.ports.terminal
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

      system.activationScripts.ezconf.text = ''
        mkdir -p ${cfg.configDir}
        cp ${./json2nix.nix} ${cfg.configDir}/default.nix
        chmod 644 ${cfg.configDir}/default.nix
        if [ ! -f ${cfg.configDir}/configuration.json ]; then
          echo '{}' > ${cfg.configDir}/configuration.json
          chmod 644 ${cfg.configDir}/configuration.json
        fi
        chown ${cfg.user}:${cfg.group} ${cfg.configDir}
        chown ${cfg.user}:${cfg.group} ${cfg.configDir}/default.nix
        chown ${cfg.user}:${cfg.group} ${cfg.configDir}/configuration.json
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
