{ pkgs }:
rec {
  python = pkgs.python3.withPackages (ps: [ ps.python-pam ps.cryptography ]);

  ezconf = pkgs.stdenv.mkDerivation {
    name             = "ezconf";
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
          ${pkgs.lib.optionalString (cfg.listen != null && !builtins.elem cfg.listen ["0.0.0.0" "::"]) "--san ${cfg.listen}"}
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
            fi
          '') cfg.auth.allowedUsers}
        fi
      ''}
      # Generate autocomplete data on first start
      if [ ! -d /var/lib/ezconf/autocomplete ]; then
        TARGET=${pkgs.lib.escapeShellArg cfg.nixosTarget} \
          ${mkoptions}/bin/ezconf-mkoptions -o /var/lib/ezconf/autocomplete
      fi
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
