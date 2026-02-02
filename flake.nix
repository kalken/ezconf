{
  description = "ezconf - Neovim configuration package";
  
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };
  
  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
      };
      
      ezconf = pkgs.stdenv.mkDerivation {
        pname = "ezconf";
        version = "1.0.0";
        
        src = ./.;
        
        nativeBuildInputs = with pkgs; [ makeWrapper ];
        buildInputs = with pkgs; [ nixd alejandra ];
        
        installPhase = ''
          mkdir -p $out/share/nvim
          cp -r * $out/share/nvim/
          
          mkdir -p $out/bin
          makeWrapper ${pkgs.neovim}/bin/nvim $out/bin/ezconf \
            --add-flags "-u $out/share/nvim/init.lua" \
            --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.nixd pkgs.alejandra ]} \
            --add-flags "--cmd 'set runtimepath^=$out/share/nvim'"
        '';
        
        meta = with pkgs.lib; {
          description = "My Neovim configuration";
          license = licenses.mit;
          maintainers = [ ];
        };
      };
      
    in {
      packages.default = ezconf;
      
      devShells.default = pkgs.mkShell {
        buildInputs = with pkgs; [
          neovim
          alejandra
          nixd
          ezconf
        ];
        
        shellHook = ''
          echo "Nix development environment loaded"
          echo "Available tools:"
          echo "  - nvim (Neovim)"
          echo "  - alejandra (Nix formatter)"
          echo "  - nixd (Nix language server)"
          echo "  - ezconf (your configured Neovim)"
          echo ""
          echo "Build ezconf with: nix build"
          echo "Run ezconf with: nix run (or just 'ezconf' in this shell)"
        '';
      };
    });
}
