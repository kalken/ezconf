{
  description = "Nix development environment with Neovim, Alejandra, and nixd";

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
    in {
      devShells.default = pkgs.mkShell {
        buildInputs = with pkgs; [
          neovim
          alejandra
          nixd
        ];

        shellHook = ''
          echo "Nix development environment loaded"
          echo "Available tools:"
          echo "  - nvim (Neovim)"
          echo "  - alejandra (Nix formatter)"
          echo "  - nixd (Nix language server)"
        '';
      };
    });
}
