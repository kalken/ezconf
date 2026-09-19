{ pkgs, version ? "dev" }:
rec {
  python = pkgs.python3.withPackages (ps: [ ps.python-pam ps.cryptography ]);

  ezconf = pkgs.stdenv.mkDerivation {
    pname             = "ezconf";
    inherit version;
    src              = ../.;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = {
      description = "Web-based NixOS configuration editor";
      license     = pkgs.lib.licenses.mit;
      maintainers = [ { github = "kalken"; } ];
    };
    installPhase     = ''
      mkdir -p $out/share
      cp -r webroot $out/share/ezconf
      install -Dm644 bin/server.py -t $out/share/ezconf/
      echo -n "${version}" > $out/share/ezconf/VERSION
      makeWrapper ${python}/bin/python3 $out/bin/ezconf \
        --add-flags "$out/share/ezconf/server.py" \
        --add-flags "--webroot $out/share/ezconf"
    '';
  };

  ezconf-terminal = pkgs.stdenv.mkDerivation {
    name             = "ezconf-terminal";
    src              = ../bin/terminal.py;
    dontUnpack       = true;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    installPhase     = ''
      install -Dm644 $src $out/share/ezconf-terminal/terminal.py
      makeWrapper ${pkgs.python3}/bin/python3 $out/bin/ezconf-terminal \
        --add-flags "$out/share/ezconf-terminal/terminal.py"
    '';
  };

  ezconf-mkoptions = pkgs.writeShellApplication {
    name           = "ezconf-mkoptions";
    runtimeInputs  = [ pkgs.nix python ];
    text           = ''
      export TARGET="''${TARGET:-/etc/nixos}"
      exec python3 "${../bin/generate-nixos-data.py}" "$@"
    '';
  };

  ezconf-mkcerts = pkgs.writeShellApplication {
    name          = "ezconf-mkcerts";
    runtimeInputs = [ pkgs.mkcert pkgs.nssTools ];
    text          = ''
      mkcert -install
      mkcert -key-file localhost-key.pem -cert-file localhost.pem localhost 127.0.0.1
      echo "cert → localhost.pem"
      echo "key  → localhost-key.pem"
      echo "Restart your browser for the CA to take effect."
    '';
  };

  mkPrestart = { cfg, staticToml, mkoptions, package }:
    # certUsers' own option default stays a plain [] (rather than defaultText referencing
    # auth.allowedUsers) so the editor's GUI can pre-fill a freshly-added certUsers field with a
    # real, editable [] instead of an unparseable Nix expression string -- see certUsers'
    # description in ezconf.nix. The actual "falls back to allowedUsers" behavior lives here
    # instead, as a plain runtime fallback, with the exact same effective result.
    let certUsers = if cfg.certUsers != [] then cfg.certUsers else cfg.auth.allowedUsers; in
    pkgs.writeShellScript "ezconf-prestart" ''
      ${pkgs.lib.optionalString cfg.generateCert ''
        ${package}/bin/ezconf --generate-ca /var/lib/ezconf \
          ${pkgs.lib.optionalString (cfg.listen != null && !builtins.elem cfg.listen ["0.0.0.0" "::"]) "--san ${cfg.listen}"} \
          ${pkgs.lib.concatMapStringsSep " " (san: "--san ${pkgs.lib.escapeShellArg san}") cfg.certNames}
        chmod 600 /var/lib/ezconf/ca-key.pem /var/lib/ezconf/localhost-key.pem
        chmod 644 /var/lib/ezconf/ca.pem /var/lib/ezconf/localhost.pem
        chown ${cfg.user}:${cfg.group} /var/lib/ezconf/ca.pem \
          /var/lib/ezconf/ca-key.pem /var/lib/ezconf/localhost.pem \
          /var/lib/ezconf/localhost-key.pem
      ''}
      ${pkgs.lib.optionalString (cfg.generateCert && cfg.installCerts && certUsers != []) ''
        # Runs on every activation, not just when the CA is freshly generated -- this is what lets
        # a user added to certUsers *after* the CA already existed still get the cert installed on
        # their next rebuild, instead of only ever on the one activation that generated the CA in
        # the first place. Cheap to repeat since _ezconf_install_ca below skips real no-ops.
        if [ -f /var/lib/ezconf/ca.pem ]; then
          # Compares what's already trusted under this nickname against the current ca.pem before
          # touching anything, and skips the delete+add entirely when they already match -- this
          # runs on every single ezconf.service (re)start (boot, manual restart, a crash-loop via
          # Restart=on-failure), not just when the CA actually changes, so skipping a real no-op
          # avoids needlessly rewriting every user's database that often. A missing or different
          # cert just fails the comparison (cmp against empty/wrong output) and falls through to a
          # normal reinstall, so this can only ever skip a genuine no-op, never a needed update.
          _ezconf_install_ca() {
            # certutil's own -a (armored/PEM) export uses CRLF line endings, while ca.pem (written
            # by Python's cryptography library) uses plain LF -- confirmed empirically: comparing
            # the two directly never matches even for an identical cert, which would have made
            # this skip check pure dead code, always falling through to reinstall. tr strips that
            # difference out before the comparison.
            if timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$1" -L -a -n "ezconf Local CA" 2>/dev/null \
                | tr -d '\r' | cmp -s - /var/lib/ezconf/ca.pem; then
              return 0
            fi
            timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$1" -D -n "ezconf Local CA" 2>/dev/null || true
            timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$1" -A -t "CT,," \
              -n "ezconf Local CA" -i /var/lib/ezconf/ca.pem || true
          }
          ${pkgs.lib.concatMapStrings (user:
            let home = "/home/${user}"; in ''
            _dir="${home}/.pki/nssdb"
            if [ -d "${home}" ]; then
              if [ ! -d "$_dir" ]; then
                mkdir -p "$_dir"
                timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_dir" -N -f /dev/null 2>/dev/null || true
                chown -R ${pkgs.lib.escapeShellArg user} "${home}/.pki"
              fi
              if timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_dir" -L >/dev/null 2>&1; then
                _ezconf_install_ca "$_dir"
              else
                echo "ezconf: WARNING: NSS database at ${home}/.pki/nssdb appears corrupt; skipping cert install for ${user}" >&2
              fi
              # Firefox keeps its own certificate database per profile, entirely separate from
              # the shared ~/.pki/nssdb above -- Chrome/Chromium-family browsers (Brave included)
              # read from that shared one, but Firefox never does, on any platform. Installing
              # only into ~/.pki/nssdb leaves Firefox not trusting the CA at all -- confirmed by a
              # real report: the terminal panel's WSS connection failed in Firefox specifically
              # (worked the whole time in Brave) until the cert was trusted there too, by hand.
              # mkcert's own -install handles this the same way: glob every profile directory and
              # add the cert to each one's own database, creating it first via -N if that profile
              # has never triggered NSS to make one yet -- a brand new, never-launched profile has
              # no database at all, nothing here can install into one that doesn't exist yet.
              # Two possible locations: the classic ~/.mozilla/firefox, and ~/.config/mozilla/firefox
              # on newer XDG-Base-Directory-compliant Firefox builds -- confirmed by a real report
              # of a profile living at the latter, not the former, on which the loop below found
              # nothing until this second glob was added. A non-matching glob is left as a literal
              # "*"-containing string by bash (no nullglob here), which the [ -d ] check right
              # below already filters out, so adding a pattern that happens to match nothing on a
              # given system is always safe.
              for _ffdir in "${home}"/.mozilla/firefox/*/ "${home}"/.config/mozilla/firefox/*/; do
                [ -d "$_ffdir" ] || continue
                _ffdir="''${_ffdir%/}"
                if [ ! -f "$_ffdir/cert9.db" ] && [ ! -f "$_ffdir/cert8.db" ]; then
                  timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -N -f /dev/null 2>/dev/null || true
                  chown ${pkgs.lib.escapeShellArg user} "$_ffdir"/cert9.db "$_ffdir"/key4.db 2>/dev/null || true
                fi
                if timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -L >/dev/null 2>&1; then
                  _ezconf_install_ca "$_ffdir"
                fi
              done
            fi
          '') certUsers}
        fi
      ''}
      ${pkgs.lib.optionalString cfg.generateAutocomplete ''
        # Generate autocomplete data on first start
        if [ ! -d /var/lib/ezconf/autocomplete ]; then
          TARGET=${pkgs.lib.escapeShellArg cfg.nixosTarget} \
            ${mkoptions}/bin/ezconf-mkoptions -o /var/lib/ezconf/autocomplete
        fi
      ''}
      # Always fix ownership (handles user/group changes)
      [ -d /var/lib/ezconf/autocomplete ] && \
        chown -R ${cfg.user}:${cfg.group} /var/lib/ezconf/autocomplete

      # Persist session key across reboots in state dir; always fix ownership
      if [ ! -f /var/lib/ezconf/session.key ]; then
        ${pkgs.python3}/bin/python3 -c \
          "import secrets,sys; sys.stdout.write(secrets.token_hex(32))" \
          > /var/lib/ezconf/session.key
      fi
      chmod 600 /var/lib/ezconf/session.key
      chown ${cfg.user}:${cfg.group} /var/lib/ezconf/session.key

      # Write runtime TOML
      cp ${staticToml} /run/ezconf/ezconf.toml
      ${pkgs.lib.optionalString (cfg.auth.passwordFile != null) ''
        echo "password = \"$(cat ${pkgs.lib.escapeShellArg (toString cfg.auth.passwordFile)})\"" \
          >> /run/ezconf/ezconf.toml
      ''}
      chmod 600 /run/ezconf/ezconf.toml
      chown ${cfg.user}:${cfg.group} /run/ezconf/ezconf.toml
    '';
}
