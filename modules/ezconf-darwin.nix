self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.ezconf;

  stateDir = "/var/lib/ezconf";
  # Not /run: that only exists on macOS as a symlink nix-darwin itself sets up.
  runDir   = "/var/run/ezconf";
  toml     = "${runDir}/ezconf.toml";

  # nix-darwin's users.users.<name>.shell is null unless set explicitly (macOS, not nix-darwin,
  # owns an existing account's shell), and can be either a shell package or a plain path. Unset
  # falls back to terminal.py's own resolution: the account's real shell, then $SHELL, then
  # /bin/sh.
  shell = let s = (config.users.users.${cfg.user} or {}).shell or null;
          in if s == null then null
             else if lib.isDerivation s then "${s}${s.shellPath}"
             else toString s;

  terminalLabel = config.launchd.daemons.ezconf-terminal.serviceConfig.Label;

  common = import ./ezconf-common.nix {
    inherit self config lib pkgs stateDir runDir shell;
    defaults  = { group = "wheel"; configDir = "/etc/nix-darwin/ezconf"; nixosTarget = "/etc/nix-darwin"; };
    # What server.py's "Restart Terminal" hands to launchctl kickstart.
    extraToml = lib.optional cfg.terminal ''terminal_launchd_label = "${terminalLabel}"'';
  };
  inherit (common) package termPkg preStartScript;

  # A launchd daemon gets a bare /usr/bin:/bin:/usr/sbin:/sbin and nothing else.
  servicePath = "/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin:/usr/sbin:/sbin";

  # The stable path the terminal daemon is started through (see launchd.daemons.ezconf-terminal).
  terminalBin = "/run/current-system/sw/bin/ezconf-terminal";

in
{

  options.services.ezconf = common.options;

  config = lib.mkMerge [
   (lib.mkIf cfg.enable common.config)
   (lib.mkIf cfg.enable {
      # nix-darwin only runs the activation scripts it knows by name, so this can't be its own
      # system.activationScripts.ezconf the way it is on NixOS.
      system.activationScripts.postActivation.text = common.configDirScript;

      environment.systemPackages = lib.optional cfg.terminal termPkg;

      # launchd has no ExecStartPre and no way to run part of a job as a different user, so this
      # always starts as root, does what ezconf.service's own ExecStartPre does on NixOS, and
      # only then drops to cfg.user for the server itself.
      launchd.daemons.ezconf = {
        script = ''
          mkdir -p ${stateDir} ${runDir}
          chmod 700 ${runDir}
          chown ${cfg.user}:${cfg.group} ${stateDir} ${runDir}
          ${preStartScript}
          exec ${lib.optionalString (cfg.user != "root") "/usr/bin/sudo -u ${lib.escapeShellArg cfg.user} -- "}${package}/bin/ezconf --config ${toml}
        '';
        environment.PATH = servicePath;
        serviceConfig = {
          RunAtLoad         = true;
          KeepAlive         = true;
          StandardOutPath   = "/var/log/ezconf.log";
          StandardErrorPath = "/var/log/ezconf.log";
        };
      };

      # The counterpart of restartIfChanged = false on NixOS: this forks the shell directly as its
      # own child, so restarting it kills whatever's running inside it, and a rebuild must never
      # do that on its own. nix-darwin reloads a daemon whenever its plist's text changes, so
      # nothing in this one may change from one build to the next -- no store paths at all. It
      # starts whatever ezconf-terminal the current system profile provides, through a path that
      # stays the same across rebuilds; only an explicit restart picks up a new one.
      #
      # The loop stands in for After=ezconf.service: ezconf.toml is written by the daemon above,
      # and launchd has no ordering between jobs.
      launchd.daemons.ezconf-terminal = lib.mkIf cfg.terminal {
        serviceConfig = {
          ProgramArguments = [
            "/bin/sh" "-c"
            "while [ ! -f ${toml} ] || [ ! -x ${terminalBin} ]; do sleep 1; done; exec ${terminalBin} --config ${toml}"
          ];
          EnvironmentVariables.PATH = servicePath;
          RunAtLoad         = true;
          KeepAlive         = true;
          StandardOutPath   = "/var/log/ezconf-terminal.log";
          StandardErrorPath = "/var/log/ezconf-terminal.log";
        } // lib.optionalAttrs (cfg.user != "root") {
          UserName  = cfg.user;
          GroupName = cfg.group;
        };
      };
   })
  ];
}
