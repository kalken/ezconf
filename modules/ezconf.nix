self:
{ config, lib, pkgs, ... }:
let
  cfg      = config.services.ezconf;
  progCfg  = config.programs.ezconf;

  # One start-menu shortcut per entry in programs.ezconf.instances (plus an implicit "default"
  # entry for programs.ezconf.url when programs.ezconf.enable is set -- see instances' own
  # description). Exec= runs xdg-open directly on that instance's url -- no wrapper script/
  # terminal command; a writeShellScriptBin "ezconf-open[-<id>]" wrapper (shaped like NixOS's own
  # nixos-help, which also checks $BROWSER before falling back to xdg-open) was tried first and
  # dropped once it became clear the only reason for it -- a standalone terminal command -- wasn't
  # actually wanted here; a bare xdg-open call is simpler with no real behavior loss for a
  # start-menu-only shortcut.
  #
  # id "default" (programs.ezconf.url) gets the bare "ezconf" desktop-item name, unchanged from
  # before instances existed; every other id gets an "-<id>" suffix, so multiple instances' items
  # never collide with each other. Never named plain "ezconf" for a non-default id anyway, since
  # the ezconf package (`package` above) already provides its own bin/ezconf, the actual web
  # server wired into ezconf.service, a completely different program.
  #
  # Two earlier Exec= approaches were tried and dropped in turn before landing here (back when
  # this was still a single, non-instanced shortcut -- the reasoning is unchanged by instances
  # existing):
  #  1. <browser> --app=<url> --class=ezconf (a chromeless "app mode" window). Confirmed against a
  #     real Brave install that Brave's own "Install page as app" registers the site as an actual
  #     installed PWA with a stable app ID and a `crx_<id>` WM_CLASS Chromium assigns itself
  #     internally -- entirely different from the ephemeral, unregistered window --app=<url>
  #     opens -- so --class=ezconf never took effect, and the running window's taskbar icon fell
  #     back to the browser's generic one. Reproducing the real PWA-install behavior declaratively
  #     is possible (Chromium's WebAppInstallForceList enterprise policy,
  #     create_desktop_shortcut: true) but needs a per-browser policy directory and only takes
  #     effect after the browser is next launched -- not worth that complexity here.
  #  2. Type=Link + URL=<url> (no Exec= at all, opened via whatever handles links system-wide).
  #     Confirmed by a real nixos-rebuild that this simply doesn't show up in the application
  #     menu/grid at all on a real desktop (GNOME's app grid, and app-menu implementations
  #     generally, only treat Type=Application entries as launchable "apps" -- Type=Link is meant
  #     for file-manager bookmarks/desktop shortcuts, not the app menu -- even though the file
  #     itself is perfectly valid and desktop-file-validate accepts it).
  # Icon= reuses the exact same nixos-icons snowflake favicon.svg the web page itself uses (see
  # ezconf-packages.nix's own build step and CLAUDE.md's "Favicon" section) -- Icon= accepts an
  # absolute path per the desktop-entry spec, so no separate copy/derivation is needed.
  # The "default" entry (programs.ezconf.enable/url, unnamed) shows its url in the label, same as
  # before instances existed -- there's no name to show instead. A named instances.<id> entry
  # shows id instead: showing every entry's url would tell two named shortcuts apart just as well,
  # but the whole point of a name from instances is choosing something more legible than a raw url
  # to look at in a menu.
  mkEzconfShortcut = id: url: pkgs.makeDesktopItem {
    name        = "ezconf${lib.optionalString (id != "default") "-${id}"}";
    desktopName = "ezconf (${if id == "default" then url else id})";
    comment     = "NixOS configuration editor";
    icon        = "${package}/share/ezconf/favicon.svg";
    type        = "Application";
    exec        = "${pkgs.xdg-utils}/bin/xdg-open ${lib.escapeShellArg url}";
    categories  = [ "Network" ];
  };

  allEzconfInstances =
    (lib.optionalAttrs progCfg.enable { default = progCfg.url; })
    // progCfg.instances;

  # The host part of programs.ezconf.url's own default. services.ezconf.listen is a single
  # address (never a list -- see its own option), but it can be a wildcard bind address
  # ("0.0.0.0"/"::", set explicitly or automatically once services.ezconf.interfaces is set) that
  # a browser can't actually connect *to* the way it can bind *on* it, so those (and the
  # unset/loopback cases, where "localhost" is just the more conventional thing to show) all fall
  # back to "localhost" -- true either way, since binding a wildcard address still accepts
  # loopback connections too. Any other configured value is assumed to be a real, reachable
  # address/hostname and used as-is, IPv6 literals bracketed for URL syntax.
  ezconfDefaultHost = let listen = config.services.ezconf.listen; in
    if listen == null || builtins.elem listen [ "0.0.0.0" "::" "127.0.0.1" "::1" ] then "localhost"
    else if lib.hasInfix ":" listen then "[${listen}]"
    else listen;

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

  common = import ./ezconf-common.nix {
    inherit self config lib pkgs;
    stateDir = "/var/lib/ezconf";
    runDir   = "/run/ezconf";
    defaults = { group = "root"; configDir = "/etc/nixos/ezconf"; nixosTarget = "/etc/nixos"; };
    shell    = if shell == null then null else shellPath shell;
  };
  inherit (common) package termPkg preStartScript;

in
{

  options.services.ezconf = common.options;

  options.programs.ezconf = {
    enable = lib.mkEnableOption "an app-menu desktop shortcut for url (see instances for additional shortcuts to other ezconf installations)";

    url = lib.mkOption {
      type        = lib.types.str;
      default     = "http${lib.optionalString config.services.ezconf.https "s"}://${ezconfDefaultHost}:${toString config.services.ezconf.ports.web}";
      defaultText = lib.literalExpression ''"http" + optionalString config.services.ezconf.https "s" + "://<services.ezconf.listen, when it's a real address, else localhost>:" + toString config.services.ezconf.ports.web'';
      description = "URL for the shortcut added when enable = true. Defaults to the services.ezconf instance running on this same machine, using services.ezconf.listen as the host when it's set to a real address (falling back to \"localhost\" for an unset/loopback/wildcard listen, since a wildcard bind address like 0.0.0.0 isn't something a browser can connect *to*) -- independent of services.ezconf.enable, so this can also point at a different, remote ezconf instance instead.";
    };

    instances = lib.mkOption {
      type        = lib.types.attrsOf lib.types.str;
      default     = { };
      example     = lib.literalExpression ''{ homelab = "https://homelab.local:9090"; vm2 = "https://192.168.1.50:9090"; }'';
      description = "Extra named ezconf installations to add start-menu shortcuts for, one per attribute -- on top of (and independent of) the single enable/url shortcut above. Lets one machine's start menu hold shortcuts to several different ezconf instances at once, e.g. one per VM/host you administer.";
    };
  };

  config = lib.mkMerge [
   (lib.mkIf (allEzconfInstances != { }) {
      environment.systemPackages = lib.mapAttrsToList mkEzconfShortcut allEzconfInstances;
    })
   (lib.mkIf cfg.enable common.config)
   (lib.mkIf cfg.enable {
      networking.firewall = lib.mkIf cfg.openFirewall (
        # cfg.ports.terminal is deliberately excluded -- terminal.py only ever binds 127.0.0.1
        # (see bin/terminal.py), reached through the web service's own port instead.
        let ports = [ cfg.ports.web ];
        in if cfg.interfaces != []
           then { interfaces = lib.genAttrs cfg.interfaces (_: { allowedTCPPorts = ports; }); }
           else { allowedTCPPorts = ports; }
      );

      system.activationScripts.ezconf.text = common.configDirScript;

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
   })
  ];
}
