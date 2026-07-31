{
  config,
  pkgs,
  lib,
  ...
}: let
  scope = {inherit pkgs lib;};

  # Every *.json file under this directory (including subfolders, for organizing tabs) is an
  # independent config "tab" (edited separately in the UI); custom-options.json is a
  # schema-extension sidecar, not a tab, and dotdirs (e.g. .ezconf-backups, when it lives here)
  # are skipped so backup files never get picked up. They're combined below via lib.mkMerge, so
  # the real NixOS module system performs the merge (list concat, attrset merge, scalar
  # conflict = eval error) — same as splitting configuration.nix across files. Mirrors
  # list_config_files() in bin/server.py.
  walk = dir: prefix:
    lib.concatLists (lib.mapAttrsToList (
      name: type: let relPath = prefix + name; in
        if type == "directory"
        then (if lib.hasPrefix "." name then [] else walk (dir + "/${name}") "${relPath}/")
        else if type == "regular" && lib.hasSuffix ".json" name && name != "custom-options.json"
        then [relPath]
        else []
    ) (builtins.readDir dir));

  jsonFileNames = walk ./. "";

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

  evaluatedConfigs = map (n: resolveExprs (builtins.fromJSON (builtins.readFile (./. + "/${n}")))) jsonFileNames;
in {
  config = lib.mkMerge evaluatedConfigs;
}
