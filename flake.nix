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
    echo "  - ezconf-dev (Neovim with config from current directory)"
    echo ""
    echo "Build ezconf with: nix build"
    echo "Run ezconf with: nix run (or just 'ezconf' in this shell)"
    
    # Alias for development - uses config from current directory
    alias ezconf-dev="nvim -u $PWD/init.lua --cmd 'set runtimepath^=$PWD'"
  '';
};
