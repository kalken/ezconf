self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.ezconf;

  stateDir = "/var/lib/ezconf";
  # Not /run: that only exists on macOS as a symlink nix-darwin itself sets up.
  runDir   = "/var/run/ezconf";
  toml     = "${runDir}/ezconf.toml";

  primaryUser = let u = config.system.primaryUser or null; in if u == null then "root" else u;

  # Whose login session the terminal runs in, or null for the old arrangement (a root daemon,
  # like on NixOS). See the terminalUser option.
  termUser  = cfg.terminalUser;
  inSession = cfg.terminal && termUser != null;

  # Where the session terminal reads its own small config from (see termSetupScript).
  termRunDir = "/var/run/ezconf-terminal";
  termToml   = if inSession then "${termRunDir}/ezconf.toml" else toml;

  # nix-darwin's users.users.<name>.shell is null unless set explicitly (macOS, not nix-darwin,
  # owns an existing account's shell), and can be either a shell package or a plain path. Unset
  # falls back to terminal.py's own resolution: the shell of the account it runs as, then
  # $SHELL, then /bin/sh.
  shell = let s = (config.users.users.${if inSession then termUser else cfg.user} or {}).shell or null;
          in if s == null then null
             else if lib.isDerivation s then "${s}${s.shellPath}"
             else toString s;

  terminalLabel = (if inSession then config.launchd.user.agents else config.launchd.daemons)
    .ezconf-terminal.serviceConfig.Label;

  common = import ./ezconf-common.nix {
    inherit self config lib pkgs stateDir runDir shell;
    # Nothing here needs root once the terminal isn't a root daemon: the editor only has to be
    # able to write its own files, and macOS checks a password for an ordinary user's process
    # too (unlike Linux, where that takes root). So it runs as the person whose Mac it is; only
    # the daemon's short start-up step stays root (see launchd.daemons.ezconf). No primary user
    # declared leaves it root, as before.
    defaults  = { user = primaryUser; group = if primaryUser == "root" then "wheel" else "staff"; configDir = "/etc/nix-darwin/ezconf"; nixosTarget = "/etc/nix-darwin"; theme = "osx"; };
    # What server.py's "Restart Terminal" hands to launchctl kickstart.
    extraToml = lib.optional cfg.terminal ''terminal_launchd_label = "${terminalLabel}"''
      ++ lib.optional inSession ''terminal_launchd_user = "${termUser}"'';
  };
  inherit (common) package termPkg preStartScript;

  # A launchd daemon gets a bare /usr/bin:/bin:/usr/sbin:/sbin and nothing else.
  servicePath = "/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin:/usr/sbin:/sbin";

  # Chrome, Brave and Safari take their trust from the system keychain, and macOS only lets a
  # root be added to it with a person's approval -- a password dialog. The daemon can't get one
  # (it has no login session: "no user interaction was possible"), but activation normally runs
  # from the terminal of whoever typed darwin-rebuild, where the dialog can appear. So the CA is
  # made here as well as in the daemon's pre-start, and trusted here, once: verify-cert passes
  # from then on and this is skipped. Run from somewhere with no session (ezconf's own Rebuild
  # button, which goes through the terminal daemon), the attempt fails and says what to run by
  # hand instead; it never fails the activation.
  caFile = "${stateDir}/ca.pem";
  trustCaScript = lib.optionalString (cfg.generateCert && cfg.installCerts) ''
    mkdir -p ${stateDir}
    ${common.generateCaScript}
    if [ -f ${caFile} ] && ! /usr/bin/security verify-cert -c ${caFile} >/dev/null 2>&1; then
      echo "ezconf: trusting its local certificate authority (macOS may ask for your password)..." >&2
      /usr/bin/security add-trusted-cert -d -r trustRoot \
          -k /Library/Keychains/System.keychain ${caFile} \
        || echo "ezconf: could not do that from here. Run this once in Terminal:
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ${caFile}" >&2
    fi
  '';

  # The stable path the terminal is started through (see terminalJob).
  terminalBin = "/run/current-system/sw/bin/ezconf-terminal";

  # The terminal in a login session runs as an ordinary user, who must not read the server's own
  # ezconf.toml (it can hold the custom-auth password) and can't read the root-only session key.
  # So it gets a config of its own holding only the keys terminal.py reads -- the same lines,
  # verbatim, which is what keeps its CONFIG_HASH equal to the one server.py computes from the
  # full file -- and read access to the session key through an ACL entry for that one user.
  termSetupScript = lib.optionalString inSession ''
    mkdir -p ${termRunDir}
    chown ${lib.escapeShellArg termUser} ${termRunDir}
    chmod 700 ${termRunDir}
    # Finished and readable before it appears under the name the terminal is waiting for.
    grep -E '^(terminal_port|session_key_file|shell|nixos_target) = ' ${toml} > ${termRunDir}/ezconf.toml.new
    chown ${lib.escapeShellArg termUser} ${termRunDir}/ezconf.toml.new
    chmod 600 ${termRunDir}/ezconf.toml.new
    mv ${termRunDir}/ezconf.toml.new ${termRunDir}/ezconf.toml
    /bin/chmod -a "${termUser} allow read" ${stateDir}/session.key 2>/dev/null || true
    /bin/chmod +a "${termUser} allow read" ${stateDir}/session.key
    /bin/chmod -a "${termUser} allow search" ${stateDir} 2>/dev/null || true
    /bin/chmod +a "${termUser} allow search" ${stateDir}
  '';

  # The counterpart of restartIfChanged = false on NixOS: this forks the shell directly as its
  # own child, so restarting it kills whatever's running inside it, and a rebuild must never do
  # that on its own. nix-darwin reloads a job whenever its plist's text changes, so nothing in
  # this one may change from one build to the next -- no store paths at all. It starts whatever
  # ezconf-terminal the current system profile provides, through a path that stays the same
  # across rebuilds; only an explicit restart picks up a new one.
  #
  # The loop stands in for After=ezconf.service: the config is written by the ezconf daemon, and
  # launchd has no ordering between jobs.
  terminalJob = logFile: {
    ProgramArguments = [
      "/bin/sh" "-c"
      "while [ ! -f ${termToml} ] || [ ! -x ${terminalBin} ]; do sleep 1; done; exec ${terminalBin} --config ${termToml}"
    ];
    EnvironmentVariables.PATH = servicePath;
    RunAtLoad         = true;
    KeepAlive         = true;
    StandardOutPath   = logFile;
    StandardErrorPath = logFile;
  };

in
{

  options.services.ezconf = common.options // {
    terminalUser = lib.mkOption {
      type        = lib.types.nullOr lib.types.str;
      default     = config.system.primaryUser or null;
      defaultText = lib.literalExpression "config.system.primaryUser";
      description = "The account whose login session the terminal panel's shell runs in, as that user. macOS only lets a program update installed apps, or show a permission or password dialog, from inside a logged-in desktop session -- so darwin-rebuild fails from a background service the moment there are Nix-installed apps. Run this way the terminal behaves like Terminal.app: it exists while that user is logged in, and commands that need root take sudo. Set to null for a root background service instead, as on NixOS: always there, no sudo needed, but only able to rebuild a system with no apps to update -- for a Mac nobody logs in to.";
    };
  };

  config = lib.mkMerge [
   (lib.mkIf cfg.enable common.config)
   (lib.mkIf cfg.enable {
      # nix-darwin only runs the activation scripts it knows by name, so this can't be its own
      # system.activationScripts.ezconf the way it is on NixOS.
      # The files already in configDir have to follow a change of user too, or the editor can
      # read its tabs but not save them.
      system.activationScripts.postActivation.text = common.configDirScript + ''
        chown -R ${cfg.user}:${cfg.group} ${cfg.configDir}
      '' + trustCaScript;

      environment.systemPackages = lib.optional cfg.terminal termPkg;

      # launchd has no ExecStartPre and no way to run part of a job as a different user, so this
      # always starts as root, does what ezconf.service's own ExecStartPre does on NixOS, and
      # only then drops to cfg.user for the server itself.
      launchd.daemons.ezconf = {
        script = ''
          mkdir -p ${stateDir} ${runDir}
          chmod 700 ${runDir}
          # -R: backups and the like were created by whoever the service ran as before.
          chown -R ${cfg.user}:${cfg.group} ${stateDir}
          chown ${cfg.user}:${cfg.group} ${runDir}
          ${preStartScript}
          ${termSetupScript}
          exec ${lib.optionalString (cfg.user != "root") "/usr/bin/sudo -H -u ${lib.escapeShellArg cfg.user} -- "}${package}/bin/ezconf --config ${toml}
        '';
        environment.PATH = servicePath;
        serviceConfig = {
          RunAtLoad         = true;
          KeepAlive         = true;
          StandardOutPath   = "/var/log/ezconf.log";
          StandardErrorPath = "/var/log/ezconf.log";
        };
      };

      # The terminal: in the terminalUser's login session, or (terminalUser = null) a daemon that
      # runs as the service user, like on NixOS. See terminalJob and the terminalUser option.
      launchd.user.agents.ezconf-terminal = lib.mkIf inSession {
        serviceConfig = terminalJob "/tmp/ezconf-terminal.log";
      };

      launchd.daemons.ezconf-terminal = lib.mkIf (cfg.terminal && !inSession) {
        serviceConfig = terminalJob "/var/log/ezconf-terminal.log"
          // lib.optionalAttrs (cfg.user != "root") {
            UserName  = cfg.user;
            GroupName = cfg.group;
          };
      };

      assertions = [ {
        assertion = !inSession || (config.system.primaryUser or null) == termUser;
        message   = "services.ezconf.terminalUser must be system.primaryUser (nix-darwin only installs per-user launchd agents for that account), or null.";
      } ];
   })
  ];
}
