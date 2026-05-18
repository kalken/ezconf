#!/usr/bin/env python3
"""Generate options.json, packages.json, and kernels.json from a NixOS flake."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
RED    = "\033[0;31m"
NC     = "\033[0m"

def info(msg):  print(f"{GREEN}{msg}{NC}", file=sys.stderr)
def warn(msg):  print(f"{YELLOW}Warning: {msg}{NC}", file=sys.stderr)
def error(msg): print(f"{RED}Error: {msg}{NC}", file=sys.stderr); sys.exit(1)


VERBOSE = False
OUTPUT_DIR = "."

def nix_eval(args_list, extra_env=None):
    """Run `nix eval --json <args_list>` and return parsed JSON, or None on failure."""
    env = {**os.environ, **(extra_env or {})}
    r = subprocess.run(
        ["nix", "eval", "--json"] + args_list,
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        if VERBOSE:
            for line in r.stderr.strip().splitlines():
                if not line.startswith(("evaluation warning:", "warning:")):
                    print(f"  {line}", file=sys.stderr)
        return None
    return json.loads(r.stdout)


def get_hosts(target):
    result = nix_eval([f"{target}#nixosConfigurations", "--apply", "builtins.attrNames", "--impure"])
    return result or []


# --- packages ---

EVAL_PKGS_EXPR = r"""
pkgSet:
let
    prefix = builtins.getEnv "PKG_PREFIX";
    mk = n:
        let r = builtins.tryEval (
            let p = pkgSet.${n};
            in if builtins.isAttrs p && (p.type or "") == "derivation"
                  && p ? meta && builtins.isAttrs p.meta && p.meta ? description
               then
                   let desc = p.meta.description;
                   in builtins.seq desc
                       { name = if prefix == "" then n else prefix + "." + n; description = desc; }
               else null
        );
        in if r.success then r.value else null;
in builtins.filter (x: x != null) (map mk
    (builtins.filter (n: n != "") (builtins.attrNames pkgSet)))
"""

DETECT_SETS_EXPR = r"""
pkgs:
let
    isDrv = v: builtins.isAttrs v && (v.type or "") == "derivation";
    isPkgSet = name:
        builtins.any (suffix:
            let l = builtins.stringLength name;
                sl = builtins.stringLength suffix;
            in l > sl && builtins.substring (l - sl) sl name == suffix
        ) [ "Packages" "Plugins" "Gems" "Extensions" ];
    excludeStr = builtins.getEnv "EXCLUDE_PKG_SETS";
    excluded = builtins.filter (s: builtins.isString s && s != "") (builtins.split "[ ,]+" excludeStr);
in builtins.filter (n:
    let r = builtins.tryEval (
        !(builtins.elem n excluded) &&
        isPkgSet n && builtins.isAttrs pkgs.${n} && !(isDrv pkgs.${n})
    );
    in r.success && r.value
) (builtins.filter (n: n != "") (builtins.attrNames pkgs))
"""

def generate_packages(flake_ref, include=None, exclude=None, no_nested=True):
    info("Generating packages.json...")
    packages = {}

    def eval_set(installable, prefix=""):
        return nix_eval([installable, "--apply", EVAL_PKGS_EXPR],
                        extra_env={"PKG_PREFIX": prefix})

    info("  Evaluating top-level packages...")
    for p in eval_set(f"{flake_ref}.pkgs") or []:
        packages[p["name"]] = p

    if not no_nested:
        if include:
            set_names = [s.strip() for s in include.replace(",", " ").split() if s.strip()]
        else:
            info("  Detecting nested package sets...")
            set_names = nix_eval(
                [f"{flake_ref}.pkgs", "--apply", DETECT_SETS_EXPR],
                extra_env={"EXCLUDE_PKG_SETS": exclude or ""},
            ) or []

        for name in set_names:
            info(f"  Evaluating {name}...")
            result = eval_set(f"{flake_ref}.pkgs.{name}", prefix=name)
            if result is not None:
                for p in result:
                    packages.setdefault(p["name"], p)
            else:
                warn(f"skipped {name} (evaluation failed)")

    out = list(packages.values())
    Path(OUTPUT_DIR, "packages.json").write_text(json.dumps(out))
    info(f"  {len(out)} packages")


# --- kernels ---

KERNELS_EXPR = r"""
pkgs:
let
    names = builtins.filter
        (n: builtins.substring 0 13 n == "linuxPackages")
        (builtins.attrNames pkgs);
    safeGet = name:
        let r = builtins.tryEval (
            let p = pkgs.${name}; in
            if p ? kernel
            then { name = name; description = p.kernel.meta.description or null; }
            else null
        );
        in if r.success then r.value else null;
in builtins.filter (x: x != null) (map safeGet names)
"""

def generate_kernels(flake_ref):
    info("Generating kernels.json...")
    result = nix_eval([f"{flake_ref}.pkgs", "--apply", KERNELS_EXPR]) or []
    Path(OUTPUT_DIR, "kernels.json").write_text(json.dumps(result))
    info(f"  {len(result)} kernels")


# --- options ---

def generate_options(target, host):
    info("Generating options.json...")
    expr = f"""
let
    target = builtins.getFlake "path:{target}";
    cfg = target.nixosConfigurations.{host};
    opts = cfg.options;
    lib = target.inputs.nixpkgs.lib;
    unwrapValue = v:
        if builtins.isAttrs v && builtins.elem (v._type or "") [ "literalExpression" "literalMD" "literalDocBook" ]
        then v.text
        else v;
    safeGet = f: opt:
        let result = builtins.tryEval (unwrapValue (f opt));
        in if result.success then result.value else null;
    rawList = lib.optionAttrSetToDocList opts;
in map (opt: {{
    path = opt.name;
    description = opt.description or null;
    type = opt.type or null;
    default = safeGet (o: o.default or null) opt;
    example = safeGet (o: o.example or null) opt;
    required = !(opt ? default) && !(opt.internal or false) && (opt.visible or true) && !(opt.readOnly or false);
}}) rawList
"""
    result = nix_eval(["--impure", "--expr", expr]) or []
    # Clear required on options whose description says they are alternatives to another option.
    # NixOS has no formal "mutually exclusive" metadata; the only signal is prose like
    # "Can be used instead of <foo>" or "Use this instead of <bar>".
    _ALT_RE = re.compile(
        r'\b(can be used instead of|use(?:d)? instead of|alternative(?:ly)? (?:to|for)|'
        r'mutually exclusive)\b',
        re.IGNORECASE,
    )
    for opt in result:
        if opt.get('required') and _ALT_RE.search(opt.get('description') or ''):
            opt['required'] = False
    Path(OUTPUT_DIR, "options.json").write_text(json.dumps(result))
    info(f"  {len(result)} options")


# --- summary ---

def print_summary(files):
    print()
    for f in files:
        p = Path(f)
        if p.exists():
            size  = p.stat().st_size
            count = len(json.loads(p.read_text()))
            kb    = size / 1024
            size_str = f"{kb:.0f}K" if kb < 1024 else f"{kb/1024:.1f}M"
            print(f"{f}: {count} entries ({size_str})")


# --- main ---

def main():
    target = os.environ.get("TARGET", "/etc/nixos")

    parser = argparse.ArgumentParser(
        description="Generate NixOS data files from a flake",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command", nargs="?", default="all",
        choices=["options", "packages", "kernels", "all"],
        help="What to generate (default: all)",
    )
    parser.add_argument("hostname", nargs="?", default="",
                        help="NixOS configuration host name")
    parser.add_argument("-e", "--exclude",
                        help="Nested package sets to skip (comma/space separated)")
    parser.add_argument("-i", "--include",
                        help=(
                            "Only include these nested sets (comma/space separated)\n"
                            "Known sets: gnomeExtensions vimPlugins emacsPackages\n"
                            "            haskellPackages nodePackages nodePackages_latest\n"
                            "            python3Packages perlPackages rubyPackages\n"
                            "            ocamlPackages phpPackages rPackages\n"
                            "            luaPackages beamPackages coqPackages\n"
                            "            kdePackages texlivePackages"
                        ))
    parser.add_argument("-o", "--output", metavar="DIR", default="webroot/autocomplete",
                        help="Directory to write generated files to (default: webroot/autocomplete/)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show full nix error output for failing evaluations")
    nested = parser.add_mutually_exclusive_group()
    nested.add_argument("--nested",    dest="nested", action="store_true",  default=False,
                        help="Include all auto-detected nested package sets")
    nested.add_argument("--no-nested", dest="nested", action="store_false",
                        help="Top-level packages only (default)")
    args = parser.parse_args()

    global VERBOSE, OUTPUT_DIR
    VERBOSE = args.verbose
    OUTPUT_DIR = args.output
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # -i implies nested for the listed sets; --nested enables full auto-detection
    no_nested = not args.nested and not args.include

    if not Path(f"{target}/flake.nix").exists():
        error(f"No flake.nix found at {target}")

    info(f"Using flake: {target}")

    hosts = get_hosts(target)
    if not hosts:
        error("No nixosConfigurations found")
    info(f"Available hosts: {' '.join(hosts)}")

    host = args.hostname
    if not host:
        host = hosts[0]
        warn(f"Using: {host}")
    elif host not in hosts:
        error(f"Host '{host}' not found. Available: {' '.join(hosts)}")

    info(f"Using host: {host}")
    flake_ref = f"{target}#nixosConfigurations.{host}"

    generated = []
    if args.command in ("packages", "all"):
        generate_packages(flake_ref, include=args.include, exclude=args.exclude, no_nested=no_nested)
        generated.append("packages.json")
    if args.command in ("options", "all"):
        generate_options(target, host)
        generated.append("options.json")
    if args.command in ("kernels", "all"):
        generate_kernels(flake_ref)
        generated.append("kernels.json")

    info("Done!")
    print_summary(generated)


if __name__ == "__main__":
    main()
