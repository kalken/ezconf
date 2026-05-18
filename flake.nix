{
  description = "ezconf — NixOS configuration editor";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems      = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      mkApp        = drv: bin: { type = "app"; program = "${drv}/bin/${bin}"; };

      perSystem = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          p    = import ./modules/ezconf-packages.nix { inherit pkgs; };
        in {
          packages = {
            default              = p.ezconf;
            inherit (p) ezconf ezconf-terminal ezconf-mkoptions ezconf-mkcerts;
          };

          apps = {
            default              = mkApp p.ezconf           "ezconf";
            ezconf            = mkApp p.ezconf           "ezconf";
            ezconf-terminal   = mkApp p.ezconf-terminal  "ezconf-terminal";
            ezconf-mkoptions  = mkApp p.ezconf-mkoptions "ezconf-mkoptions";
            ezconf-mkcerts    = mkApp p.ezconf-mkcerts   "ezconf-mkcerts";
          };

          devShells.default = pkgs.mkShell {
            packages  = with p; [ ezconf ezconf-mkoptions ezconf-mkcerts python pkgs.nix pkgs.jq ];
            shellHook = ''
              echo "NixOS Data Generator"
              echo "Usage: ezconf-mkoptions [hostname]"
              echo "Set TARGET env var to use a different flake (default: /etc/nixos)"
              echo ""
              echo "Available hosts:"
              nix eval "''${TARGET:-/etc/nixos}#nixosConfigurations" --apply 'builtins.attrNames' --impure 2>/dev/null \
                | tr -d '[]"' | tr ',' '\n' | sed 's/^/  - /' || echo "  (could not detect)"
              echo ""
              echo "Generate certs: ezconf-mkcerts"
            '';
          };
        });
    in {
      packages  = builtins.mapAttrs (_: s: s.packages)  perSystem;
      apps      = builtins.mapAttrs (_: s: s.apps)      perSystem;
      devShells = builtins.mapAttrs (_: s: s.devShells) perSystem;

      nixosModules.default = import ./modules/ezconf.nix self;
    };
}
