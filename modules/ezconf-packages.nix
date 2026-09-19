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
    pkgs.writeShellScript "ezconf-prestart" ''
      ${pkgs.lib.optionalString cfg.generateCert ''
        _cert_new=0
        [ -f /var/lib/ezconf/ca.pem ] || _cert_new=1
        ${package}/bin/ezconf --generate-ca /var/lib/ezconf \
          ${pkgs.lib.optionalString (cfg.listen != null && !builtins.elem cfg.listen ["0.0.0.0" "::"]) "--san ${cfg.listen}"} \
          ${pkgs.lib.concatMapStringsSep " " (san: "--san ${pkgs.lib.escapeShellArg san}") cfg.certNames}
        chmod 600 /var/lib/ezconf/ca-key.pem /var/lib/ezconf/localhost-key.pem
        chmod 644 /var/lib/ezconf/ca.pem /var/lib/ezconf/localhost.pem
        chown ${cfg.user}:${cfg.group} /var/lib/ezconf/ca.pem \
          /var/lib/ezconf/ca-key.pem /var/lib/ezconf/localhost.pem \
          /var/lib/ezconf/localhost-key.pem
      ''}
      ${pkgs.lib.optionalString (cfg.generateCert && cfg.installCerts && cfg.auth.allowedUsers != []) ''
        if [ "$_cert_new" = "1" ] && [ -f /var/lib/ezconf/ca.pem ]; then
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
                timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_dir" -D -n "ezconf Local CA" 2>/dev/null || true
                timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_dir" -A -t "CT,," \
                  -n "ezconf Local CA" -i /var/lib/ezconf/ca.pem || true
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
              for _ffdir in "${home}"/.mozilla/firefox/*/; do
                [ -d "$_ffdir" ] || continue
                _ffdir="''${_ffdir%/}"
                if [ ! -f "$_ffdir/cert9.db" ] && [ ! -f "$_ffdir/cert8.db" ]; then
                  timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -N -f /dev/null 2>/dev/null || true
                  chown ${pkgs.lib.escapeShellArg user} "$_ffdir"/cert9.db "$_ffdir"/key4.db 2>/dev/null || true
                fi
                if timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -L >/dev/null 2>&1; then
                  timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -D -n "ezconf Local CA" 2>/dev/null || true
                  timeout 10 ${pkgs.nssTools}/bin/certutil -d "sql:$_ffdir" -A -t "CT,," \
                    -n "ezconf Local CA" -i /var/lib/ezconf/ca.pem || true
                fi
              done
            fi
          '') cfg.auth.allowedUsers}
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
