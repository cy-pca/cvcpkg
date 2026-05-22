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
