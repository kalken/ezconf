{
  config,
  pkgs,
  lib,
  ...
}: let
  rawJson = builtins.fromJSON (builtins.readFile ./configuration.json);
  scope = {inherit pkgs lib;};

  resolveExprs = val:
    if builtins.isAttrs val && val ? "_expr"
    then
      import (builtins.toFile "expr.nix" ''
        { pkgs, lib }: ${val._expr}
      '')
      scope
    else if builtins.isList val
    then map resolveExprs val
    else if builtins.isAttrs val
    then
      let active = lib.filterAttrs (_: v: !(builtins.isAttrs v && v ? "_disabled")) val;
      in lib.mapAttrs (_: resolveExprs) active
    else val;

  evaluatedConfig = resolveExprs rawJson;
in {
  config = evaluatedConfig;
}
