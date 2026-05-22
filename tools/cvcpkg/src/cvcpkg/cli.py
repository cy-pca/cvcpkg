"""cvcpkg command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from cvcpkg import __version__
from cvcpkg.errors import CvcpkgError


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cvcpkg",
        description="Component package manager for libcvc-deps prebuilt dependency bundles.",
    )
    p.add_argument("--version", action="version", version=f"cvcpkg {__version__}")

    sub = p.add_subparsers(dest="command", help="Available commands")

    # ── install ──────────────────────────────────────────────────
    inst = sub.add_parser("install", help="Install component bundles into a prefix")
    inst.add_argument("components", nargs="*", help="Components to install (name[==version])")
    inst.add_argument("--from", dest="from_file", metavar="FILE", help="Requirements YAML file")
    inst.add_argument("--prefix", default="./deps", help="Install prefix (default: ./deps)")
    inst.add_argument("--release", metavar="VER", help="Pin to a libcvc-deps release version")
    inst.add_argument("--platform", default="auto")
    inst.add_argument("--config", default="release", choices=["release", "debug"])
    inst.add_argument("--link", default="shared", choices=["shared", "static"])
    inst.add_argument("--catalog", metavar="URL", help="Override catalog URL")
    inst.add_argument("--catalog-revision", type=int, metavar="REV")
    inst.add_argument("--ignore-abi", action="store_true")

    # ── list ─────────────────────────────────────────────────────
    lst = sub.add_parser("list", help="List components")
    lst_g = lst.add_mutually_exclusive_group()
    lst_g.add_argument("--installed", action="store_true", help="Show installed bundles")
    lst_g.add_argument("--available", action="store_true", help="Show available bundles")
    lst.add_argument("--prefix", default="./deps")

    # ── info ─────────────────────────────────────────────────────
    info = sub.add_parser("info", help="Show component details")
    info.add_argument("component", help="Component name")

    # ── verify ───────────────────────────────────────────────────
    verify = sub.add_parser("verify", help="Verify prefix integrity")
    verify.add_argument("--prefix", default="./deps")

    # ── lock ─────────────────────────────────────────────────────
    sub.add_parser("lock", help="Write or refresh the lockfile")

    # ── sync ─────────────────────────────────────────────────────
    sync = sub.add_parser("sync", help="Ensure prefix matches lockfile")
    sync.add_argument("--prefix", default="./deps")

    # ── catalog ──────────────────────────────────────────────────
    cat = sub.add_parser("catalog", help="Catalog management")
    cat_g = cat.add_mutually_exclusive_group()
    cat_g.add_argument("--refresh", action="store_true")
    cat_g.add_argument("--pin", type=int, metavar="REV")
    cat_g.add_argument("--show", action="store_true")

    # ── gc ───────────────────────────────────────────────────────
    sub.add_parser("gc", help="Prune the local download cache")

    # ── validate ─────────────────────────────────────────────────
    val = sub.add_parser("validate", help="Validate packaging YAML files")
    val.add_argument("target", nargs="?", default="all",
                     help="What to validate: all | components | recipes | recipes/<name>")

    # ── build ────────────────────────────────────────────────────
    bld = sub.add_parser("build", help="Build a recipe from source")
    bld.add_argument("recipe", nargs="+", help="Recipe directory or name")
    bld.add_argument("--platform", default="auto")
    bld.add_argument("--config", default="release", choices=["release", "debug"])
    bld.add_argument("--link", default="shared", choices=["shared", "static"])
    bld.add_argument("--prefix", metavar="DIR", help="Install prefix")
    bld.add_argument("--keep-build-dir", action="store_true")

    # ── pack ─────────────────────────────────────────────────────
    pak = sub.add_parser("pack", help="Build + archive a recipe")
    pak.add_argument("recipe", nargs="+", help="Recipe directory or name")
    pak.add_argument("--platform", default="auto")
    pak.add_argument("--config", default="release", choices=["release", "debug"])
    pak.add_argument("--link", default="shared", choices=["shared", "static"])
    pak.add_argument("--prefix", metavar="DIR", help="Install prefix")
    pak.add_argument("--output-dir", default="./dist", metavar="DIR")
    pak.add_argument("--keep-build-dir", action="store_true")

    # ── recipes ──────────────────────────────────────────────────
    rec = sub.add_parser("recipes", help="List or inspect recipes")
    rec_g = rec.add_mutually_exclusive_group()
    rec_g.add_argument("--list", action="store_true", dest="list_recipes",
                       help="List all recipes")
    rec_g.add_argument("--show", metavar="NAME", help="Show details of a recipe")
    rec_g.add_argument("--validate", action="store_true", dest="validate_recipes",
                       help="Validate all recipes")

    return p


def _cmd_install(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg install``."""
    from cvcpkg.manifest import ComponentReq, Requirements
    from cvcpkg.platform import detect_arch, detect_platform

    prefix = Path(args.prefix).resolve()

    # Build requirements.
    if args.from_file:
        reqs = Requirements.from_yaml(args.from_file)
    else:
        components: list[ComponentReq] = []
        for c in args.components:
            if "==" in c:
                name, ver = c.split("==", 1)
                components.append(ComponentReq(name=name, version=f"=={ver}"))
            else:
                components.append(ComponentReq(name=c))
        reqs = Requirements(
            platform=args.platform,
            config=args.config,
            link=args.link,
            libcvc_deps=args.release or "",
            components=components,
        )

    # Resolve platform.
    platform = reqs.platform if reqs.platform != "auto" else detect_platform()
    arch = reqs.arch if reqs.arch != "auto" else detect_arch()

    print(f"cvcpkg: resolving for {platform}/{arch}/{reqs.config}/{reqs.link}")
    print(f"cvcpkg: target prefix: {prefix}")

    if not reqs.components:
        print("cvcpkg: no components requested, nothing to do.")
        return 0

    # NOTE: full catalog-based resolution is not yet wired up.
    # This is the skeleton that will be completed in Phase 3 once
    # the first catalog index is published.
    print("cvcpkg: catalog-based resolution not yet available (Phase 2 skeleton).")
    print(f"cvcpkg: requested components: {', '.join(c.name for c in reqs.components)}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg list``."""
    prefix = Path(args.prefix).resolve()

    if args.installed:
        lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
        if not lock_path.exists():
            print("cvcpkg: no lockfile found — prefix may not be managed by cvcpkg.")
            return 1
        from cvcpkg.lockfile import Lockfile

        lock = Lockfile.read(lock_path)
        if not lock.bundles:
            print("cvcpkg: no bundles installed.")
        else:
            for e in lock.bundles:
                print(f"  {e.name:20s} {e.version}")
        return 0

    if args.available:
        print("cvcpkg: catalog browsing not yet implemented (Phase 3).")
        return 0

    # Default: show installed if prefix exists, else available.
    print("cvcpkg: use --installed or --available.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg validate`` — delegate to packaging/validate.py logic."""
    import importlib.util
    from pathlib import Path

    # Find the packaging/validate.py relative to the repo root.
    # Walk up from cvcpkg source to find the repo.
    pkg_dir = Path(__file__).resolve().parent
    for ancestor in pkg_dir.parents:
        validate_script = ancestor / "packaging" / "validate.py"
        if validate_script.exists():
            break
    else:
        # Fallback: try CWD
        validate_script = Path.cwd() / "packaging" / "validate.py"

    if not validate_script.exists():
        print("cvcpkg: cannot find packaging/validate.py — run from the libcvc-deps repo root.")
        return 1

    spec = importlib.util.spec_from_file_location("validate", validate_script)
    if spec is None or spec.loader is None:
        print("cvcpkg: cannot load packaging/validate.py")
        return 1
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["cvcpkg-validate", args.target]
    spec.loader.exec_module(mod)
    return mod.main()


def _cmd_verify(args: argparse.Namespace) -> int:
    prefix = Path(args.prefix).resolve()
    lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
    if not lock_path.exists():
        print(f"cvcpkg: no lockfile at {lock_path}")
        return 1
    print(f"cvcpkg: verifying prefix {prefix} ...")
    # Full verification will be added when bundles ship manifests.
    print("cvcpkg: verification not yet fully implemented (Phase 3).")
    return 0


def _cmd_gc(_args: argparse.Namespace) -> int:
    from cvcpkg.cache import default_cache_dir, gc

    cache_dir = default_cache_dir()
    if not cache_dir.is_dir():
        print("cvcpkg: cache is empty.")
        return 0
    removed = gc(cache_dir, set())
    print(f"cvcpkg: pruned {removed} cached archive(s).")
    return 0


def _resolve_recipe_dir(name: str) -> Path:
    """Resolve a recipe name or path to its directory."""
    p = Path(name)
    if p.is_dir() and (p / "recipe.yaml").is_file():
        return p.resolve()
    # Try as a name under the recipes/ directory
    from cvcpkg.builder import find_recipes_dir
    recipes_dir = find_recipes_dir()
    candidate = recipes_dir / name
    if candidate.is_dir() and (candidate / "recipe.yaml").is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"Recipe not found: {name}")


def _cmd_build(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg build <recipe>...``."""
    from cvcpkg.builder import build_recipe
    from cvcpkg.platform import detect_platform

    platform = args.platform if args.platform != "auto" else detect_platform()
    prefix = Path(args.prefix).resolve() if args.prefix else None

    for name in args.recipe:
        recipe_dir = _resolve_recipe_dir(name)
        build_recipe(
            recipe_dir,
            platform=platform,
            config=args.config,
            link=args.link,
            prefix=prefix,
            keep_build_dir=args.keep_build_dir,
        )
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg pack <recipe>...``."""
    from cvcpkg.builder import pack_recipe
    from cvcpkg.platform import detect_platform

    platform = args.platform if args.platform != "auto" else detect_platform()
    prefix = Path(args.prefix).resolve() if args.prefix else None
    output_dir = Path(args.output_dir).resolve()

    for name in args.recipe:
        recipe_dir = _resolve_recipe_dir(name)
        archive, sha, size = pack_recipe(
            recipe_dir,
            platform=platform,
            config=args.config,
            link=args.link,
            prefix=prefix,
            output_dir=output_dir,
            keep_build_dir=args.keep_build_dir,
        )
        print(f"  {archive} ({size:,} bytes, sha256={sha})")
    return 0


def _cmd_recipes(args: argparse.Namespace) -> int:
    """Handle ``cvcpkg recipes``."""
    from cvcpkg.builder import Recipe, find_recipes_dir, list_recipes

    if args.show:
        recipes_dir = find_recipes_dir()
        recipe_dir = recipes_dir / args.show
        if not (recipe_dir / "recipe.yaml").is_file():
            print(f"cvcpkg: recipe '{args.show}' not found.")
            return 1
        recipe = Recipe.load(recipe_dir)
        print(f"Name:     {recipe.name}")
        print(f"Version:  {recipe.full_version}")
        print(f"Source:   {recipe.source.type}")
        if recipe.source.url:
            print(f"URL:      {recipe.source.url}")
        platforms = [m.platform for m in recipe.build_matrix]
        print(f"Platforms: {', '.join(platforms)}")
        deps = recipe.raw.get("depends", {}).get("build", [])
        if deps:
            dep_names = []
            for d in deps:
                if isinstance(d, str):
                    dep_names.append(d)
                else:
                    dep_names.append(d.get("name", "?"))
            print(f"Depends:  {', '.join(dep_names)}")
        return 0

    if args.validate_recipes:
        return _cmd_validate(argparse.Namespace(target="all"))

    # Default: --list
    recipes = list_recipes()
    if not recipes:
        print("cvcpkg: no recipes found.")
        return 1
    print(f"{'Name':<20} {'Version':<18} {'Platforms'}")
    print("-" * 60)
    for r in recipes:
        platforms = ", ".join(m.platform for m in r.build_matrix)
        print(f"{r.name:<20} {r.full_version:<18} {platforms}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        handlers = {
            "install": _cmd_install,
            "list": _cmd_list,
            "validate": _cmd_validate,
            "verify": _cmd_verify,
            "gc": _cmd_gc,
            "build": _cmd_build,
            "pack": _cmd_pack,
            "recipes": _cmd_recipes,
        }
        handler = handlers.get(args.command)
        if handler:
            return handler(args)
        else:
            print(f"cvcpkg: '{args.command}' is not yet implemented.")
            return 0
    except CvcpkgError as e:
        print(f"cvcpkg: ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
