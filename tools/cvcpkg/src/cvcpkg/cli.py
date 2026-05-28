"""cvcpkg command-line interface (click-based).

This module defines the entire CLI surface for cvcpkg, the component
package manager for libcvc-deps.  It provides two main workflows:

**Consumer workflow** (downstream projects like libcvc, TexMol)::

    # Install prebuilt bundles from the catalog
    cvcpkg install --from cvc-requirements.yaml --prefix ./deps
    cvcpkg install --from cvc-requirements.yaml --config debug  # override config
    cvcpkg list --installed --prefix ./deps
    cvcpkg verify --prefix ./deps

**Producer workflow** (libcvc-deps maintainers)::

    # Build all recipes from source into a shared prefix
    cvcpkg build-all --platform linux --config release --link shared --prefix ./prefix
    # Package into per-component archives for the catalog
    cvcpkg pack-all --platform linux --config release --link shared --output-dir ./dist
    # Publish archives to the cvcpkg-server
    cvcpkg publish dist/*.tar.gz --server https://pkg.tx.wtf --token cvctok_...
    # Inspect and validate recipes
    cvcpkg recipes --list
    cvcpkg recipes --show grpc
    cvcpkg validate
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

from cvcpkg import __version__
from cvcpkg.errors import CvcpkgError

# ── Shared option decorators ────────────────────────────────────
#
# These are reusable click.option decorators applied to multiple
# commands.  Each produces a keyword argument of the same name in
# the decorated function.  "auto" sentinels are resolved at runtime
# by detect_platform() / detect_arch().

_VALID_PLATFORMS = ["auto", "linux", "macos", "windows", "wasm", "freebsd", "openbsd", "netbsd"]
_VALID_ARCHES = ["auto", "x86_64", "arm64"]

_platform_opt = click.option(
    "--platform",
    type=click.Choice(_VALID_PLATFORMS, case_sensitive=False),
    default="auto",
    help="Target platform.  'auto' detects the current OS.",
)
_config_opt = click.option(
    "--config",
    type=click.Choice(["release", "debug"], case_sensitive=False),
    default="release",
    help="Build configuration (maps to CMAKE_BUILD_TYPE).",
)
_link_opt = click.option(
    "--link",
    type=click.Choice(["shared", "static"], case_sensitive=False),
    default="shared",
    help="Link mode — shared (.so/.dylib/.dll) or static (.a/.lib).",
)
_prefix_opt = click.option(
    "--prefix",
    type=click.Path(),
    default="./deps",
    help="Install prefix directory (will contain bin/, lib/, include/).",
)
_keep_build_opt = click.option(
    "--keep-build-dir",
    is_flag=True,
    help="Keep intermediate build directories for debugging.",
)
_recipes_dir_opt = click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help=(
        "Path to a recipes/ directory.  May be specified multiple times "
        "to overlay directories (later directories win on name conflicts).  "
        "Auto-detected from the repo root if omitted."
    ),
)
_maintainer_opt = click.option(
    "--maintainer",
    type=str,
    default="",
    help="Override the maintainer field in the package manifest.",
)


def _validate_org_slug(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Click callback that validates --org using GitHub username rules."""
    if not value:
        return value
    from cvcpkg.server.models import validate_org_slug

    err = validate_org_slug(value)
    if err:
        raise click.BadParameter(err)
    return value


# ── Root group ──────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="cvcpkg")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Component package manager for libcvc-deps prebuilt dependency bundles.

    cvcpkg resolves, downloads, and installs prebuilt component bundles
    from the libcvc-deps catalog, or builds them from source recipes.

    \b
    Quick start (downstream consumer):
      cvcpkg install --from cvc-requirements.yaml
      cmake -B build -DCMAKE_PREFIX_PATH=./deps

    \b
    Quick start (libcvc-deps maintainer):
      cvcpkg build-all --prefix ./prefix --recipes-dir recipes
      cvcpkg validate
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── install ─────────────────────────────────────────────────────


@cli.command()
@click.argument("components", nargs=-1)
@click.option(
    "--from",
    "from_file",
    type=click.Path(exists=True),
    help="Path to a cvc-requirements.yaml file listing components to install.",
)
@_prefix_opt
@click.option(
    "--release",
    metavar="VER",
    help="Pin to a specific libcvc-deps release version (e.g. 1.2.0).",
)
@_platform_opt
@click.option(
    "--arch",
    type=click.Choice(_VALID_ARCHES, case_sensitive=False),
    default="auto",
    help="Target architecture.  'auto' detects the current CPU.",
)
@_config_opt
@_link_opt
@click.option(
    "--catalog",
    metavar="URL",
    help="Override catalog URL or path to a local catalog YAML file.",
)
@click.option(
    "--catalog-revision",
    type=int,
    metavar="REV",
    help="Pin to a specific catalog revision number.",
)
@click.option(
    "--source",
    type=click.Choice(["auto", "server", "github"], case_sensitive=False),
    default="auto",
    help=(
        "Catalog source strategy.  'server' uses pkg.tx.wtf only; "
        "'github' uses GitHub Pages/Releases only; 'auto' tries "
        "server first then falls back to GitHub (default)."
    ),
)
@click.option(
    "--ignore-abi",
    is_flag=True,
    help="Skip ABI compatibility checks (C++ standard, runtime, etc.).",
)
@click.option(
    "--verify-signatures/--no-verify-signatures",
    default=False,
    help="Verify Ed25519 signatures on downloaded archives.",
)
@click.option(
    "--fallback-to-source/--no-fallback-to-source",
    default=False,
    help="Build from source recipe when no prebuilt binary is available.",
)
@_recipes_dir_opt
def install(
    components: tuple[str, ...],
    from_file: str | None,
    prefix: str,
    release: str | None,
    platform: str,
    arch: str,
    config: str,
    link: str,
    catalog: str | None,
    catalog_revision: int | None,
    source: str,
    ignore_abi: bool,
    verify_signatures: bool,
    fallback_to_source: bool,
    recipes_dirs: tuple[str, ...],
) -> None:
    """Install component bundles into a prefix.

    Downloads and extracts prebuilt component archives from the
    libcvc-deps release catalog.  Components can be specified as
    positional arguments or loaded from a cvc-requirements.yaml
    file via --from.

    \b
    Examples:
      # Install from a requirements file
      cvcpkg install --from cvc-requirements.yaml --prefix ./deps

      # Override config for a debug build (file says release)
      cvcpkg install --from cvc-requirements.yaml --config debug

      # Install individual components by name
      cvcpkg install zlib boost --prefix ./deps

      # Pin a specific component version
      cvcpkg install zlib==1.3.1+cvc.1 --prefix ./deps

    When using --from, the CLI flags --platform, --arch, --config,
    and --link override the corresponding values in the requirements
    file if explicitly provided on the command line.
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import (
        GITHUB_CATALOG_URL,
        catalog_entries,
        fetch_catalog,
        load_catalog_from_file,
    )
    from cvcpkg.config import (
        load_user_config,
        merge_cli_overrides,
    )
    from cvcpkg.errors import InstallError, IntegrityError
    from cvcpkg.installer import build_from_source_fallback, install_entry
    from cvcpkg.lockfile import LockEntry, Lockfile
    from cvcpkg.manifest import CatalogEntry, ComponentReq, Requirements
    from cvcpkg.platform import detect_arch, detect_platform

    ctx = click.get_current_context()
    prefix_path = Path(prefix).resolve()

    # ── Load or build the Requirements object ──
    #
    # Either parse a cvc-requirements.yaml file (--from) or construct
    # one from positional COMPONENTS arguments + CLI flags.
    if from_file:
        reqs = Requirements.from_yaml(from_file)
    else:
        comp_list: list[ComponentReq] = []
        for c in components:
            if "==" in c:
                name, ver = c.split("==", 1)
                comp_list.append(ComponentReq(name=name, version=f"=={ver}"))
            else:
                comp_list.append(ComponentReq(name=c))
        reqs = Requirements(
            platform=platform,
            arch=arch,
            config=config,
            link=link,
            libcvc_deps=release or "",
            components=comp_list,
        )

    # ── CLI flag overrides ──
    #
    # When --from is used, the requirements file provides defaults for
    # platform/arch/config/link.  Explicit CLI flags take precedence.
    # For platform/arch, "auto" is the sentinel (default).  For config
    # and link, we check Click's ParameterSource to distinguish "user
    # passed --config debug" from "defaulted to release".
    if platform != "auto":
        reqs.platform = platform
    if arch != "auto":
        reqs.arch = arch
    if ctx.get_parameter_source("config") == click.core.ParameterSource.COMMANDLINE:
        reqs.config = config
    if ctx.get_parameter_source("link") == click.core.ParameterSource.COMMANDLINE:
        reqs.link = link

    # Resolve "auto" sentinels to concrete values.
    plat = reqs.platform if reqs.platform != "auto" else detect_platform()
    arc = reqs.arch if reqs.arch != "auto" else detect_arch()

    click.echo(f"cvcpkg: resolving for {plat}/{arc}/{reqs.config}/{reqs.link}")
    click.echo(f"cvcpkg: target prefix: {prefix_path}")

    if not reqs.components:
        click.echo("cvcpkg: no components requested, nothing to do.")
        return

    # ── Fetch the catalog and resolve dependencies ──
    #
    # The catalog is a YAML index of all published component bundles.
    # We filter entries by the target platform tuple (platform/arch/
    # config/link), then run the backtracking SAT-style resolver to
    # pick compatible versions for all requested components.
    #
    # --source controls catalog source strategy:
    #   auto   → primary (pkg.tx.wtf) with GitHub Pages fallback
    #   server → pkg.tx.wtf only, no fallback
    #   github → GitHub Pages only, no fallback
    catalog_url = catalog or ""
    catalog_failed = False
    try:
        if catalog_url and Path(catalog_url).is_file():
            cat = load_catalog_from_file(catalog_url)
        else:
            cat = fetch_catalog(catalog_url, cache_dir=default_cache_dir())
    except Exception as exc:
        if not fallback_to_source:
            raise
        click.echo(f"cvcpkg: catalog unavailable ({exc}), will build from source.")
        catalog_failed = True
        cat = {"bundles": []}

    picked: dict[str, CatalogEntry] = {}
    source_only: list[str] = []
    requested_names = [c.name for c in reqs.components]

    if not catalog_failed:
        entries = catalog_entries(
            cat,
            platform=plat,
            arch=arc,
            build_type=reqs.config,
            link=reqs.link,
        )

        # Group candidate entries by component name for the resolver.
        candidates: dict[str, list] = {}
        for e in entries:
            candidates.setdefault(e.name, []).append(e)

        if not entries and not fallback_to_source:
            raise click.ClickException("no bundles found in catalog for this platform tuple.")

        if entries:
            from cvcpkg.resolver import resolve

            # Only resolve components that have candidates in the catalog.
            resolvable = [c for c in reqs.components if c.name in candidates]
            if resolvable:
                result = resolve(resolvable, candidates)
                picked = result.picked

            # Components not in the catalog need source build.
            if fallback_to_source:
                source_only = [c.name for c in reqs.components if c.name not in picked]
        elif fallback_to_source:
            source_only = requested_names
    else:
        # Catalog was unreachable — all requested components must be
        # built from source (the fallback_to_source flag is already
        # verified above, so this branch is only reachable when the
        # flag is set).
        source_only = requested_names

    if picked:
        click.echo(f"cvcpkg: resolved {len(picked)} component(s) from catalog:")
        for name in sorted(picked):
            entry = picked[name]
            display = entry.qualified_name if hasattr(entry, "qualified_name") else name
            click.echo(f"  {display} == {entry.version}")
    if source_only:
        click.echo(
            f"cvcpkg: {len(source_only)} component(s) will be built from source: "
            + ", ".join(source_only)
        )
    if not picked and not source_only:
        raise click.ClickException("no bundles found in catalog for this platform tuple.")

    # ── Download and extract each resolved bundle ──
    cache_dir = default_cache_dir()
    lock_entries: list[LockEntry] = []
    rdirs = [Path(d) for d in recipes_dirs] if recipes_dirs else None
    for name in sorted(picked):
        entry = picked[name]
        click.echo(f"cvcpkg: installing {name} {entry.version} ...")
        try:
            install_entry(entry, prefix_path, cache_dir, verify_signatures=verify_signatures)
        except (InstallError, IntegrityError) as exc:
            if not fallback_to_source:
                raise
            click.echo(f"cvcpkg: download failed for {name} ({exc}), building from source...")
            build_from_source_fallback(
                name,
                prefix_path,
                platform=plat,
                config=reqs.config,
                link=reqs.link,
                recipes_dirs=rdirs,
            )
            lock_entries.append(
                LockEntry(
                    name=entry.name,
                    version=entry.version,
                    upstream_version=entry.upstream_version,
                    source_release="source-build",
                    sha256="",
                    size_bytes=0,
                    archive_url="",
                )
            )
            continue
        lock_entries.append(
            LockEntry(
                name=entry.name,
                version=entry.version,
                upstream_version=entry.upstream_version,
                source_release=entry.source_release,
                sha256=entry.sha256,
                size_bytes=entry.size_bytes,
                archive_url=entry.archive_url,
            )
        )

    # ── Build from source for components not in the catalog ──
    for name in source_only:
        click.echo(f"cvcpkg: building {name} from source...")
        build_from_source_fallback(
            name,
            prefix_path,
            platform=plat,
            config=reqs.config,
            link=reqs.link,
            recipes_dirs=rdirs,
        )
        lock_entries.append(
            LockEntry(
                name=name,
                version="source",
                upstream_version="",
                source_release="source-build",
                sha256="",
                size_bytes=0,
                archive_url="",
            )
        )

    # ── Write lockfile ──
    #
    # The lockfile records exactly which bundles were installed, their
    # SHA-256 hashes, and archive URLs.  It enables 'cvcpkg verify'
    # (integrity check) and 'cvcpkg sync' (re-download missing bundles).
    lock = Lockfile(
        platform=plat,
        arch=arc,
        config=reqs.config,
        link=reqs.link,
        catalog_revision=cat.get("revision", 0),
        bundles=lock_entries,
    )
    lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
    lock.write(lock_path)
    click.echo(f"cvcpkg: lockfile written to {lock_path}")
    click.echo(f"cvcpkg: done — {len(picked)} component(s) installed to {prefix_path}")


# ── list ────────────────────────────────────────────────────────


@cli.command("list")
@click.option(
    "--installed",
    "mode",
    flag_value="installed",
    help="Show bundles installed in the prefix (reads the lockfile).",
)
@click.option(
    "--available",
    "mode",
    flag_value="available",
    help="Show all bundles published in the catalog.",
)
@_prefix_opt
def list_cmd(mode: str | None, prefix: str) -> None:
    """List installed or available components.

    \b
    Examples:
      cvcpkg list --installed --prefix ./deps
      cvcpkg list --available
    """
    prefix_path = Path(prefix).resolve()

    if mode == "installed":
        lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
        if not lock_path.exists():
            raise click.ClickException("no lockfile found — prefix may not be managed by cvcpkg.")
        from cvcpkg.lockfile import Lockfile

        lock = Lockfile.read(lock_path)
        if not lock.bundles:
            click.echo("cvcpkg: no bundles installed.")
        else:
            for e in lock.bundles:
                click.echo(f"  {e.name:20s} {e.version}")
        return

    if mode == "available":
        from cvcpkg.cache import default_cache_dir
        from cvcpkg.catalog import catalog_entries, fetch_catalog

        cat = fetch_catalog(cache_dir=default_cache_dir())
        entries = catalog_entries(cat)
        if not entries:
            click.echo("cvcpkg: no bundles in catalog.")
        else:
            seen: dict[str, list[str]] = {}
            for e in entries:
                seen.setdefault(e.qualified_name, []).append(e.version)
            for name in sorted(seen):
                versions = sorted(set(seen[name]))
                click.echo(f"  {name:20s} {', '.join(versions)}")
        return

    click.echo("cvcpkg: use --installed or --available.")


# ── info ────────────────────────────────────────────────────────


@cli.command()
@click.argument("component")
def info(component: str) -> None:
    """Show component details from the catalog.

    Displays the latest version, upstream version, dependencies, and
    all available versions for COMPONENT.

    \b
    Example:
      cvcpkg info grpc
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import catalog_entries, fetch_catalog

    cat = fetch_catalog(cache_dir=default_cache_dir())
    entries = catalog_entries(cat)
    # Support both plain "zlib" and org-qualified "myorg/zlib" lookups.
    if "/" in component:
        lookup_org, lookup_name = component.split("/", 1)
        matches = [e for e in entries if e.name == lookup_name and e.org == lookup_org]
    else:
        matches = [e for e in entries if e.name == component]
    if not matches:
        raise click.ClickException(f"component '{component}' not found in catalog.")

    from cvcpkg.semver import Version

    matches.sort(key=lambda e: Version.parse(e.version), reverse=True)
    latest = matches[0]

    click.echo(f"Name:             {latest.qualified_name}")
    click.echo(f"Latest version:   {latest.version}")
    click.echo(f"Upstream version: {latest.upstream_version}")
    click.echo(f"Source release:   {latest.source_release}")
    if latest.required_deps:
        deps = ", ".join(
            f"{d.name}" + (f" {d.version}" if d.version else "") for d in latest.required_deps
        )
        click.echo(f"Dependencies:     {deps}")
    click.echo(f"Available versions: {', '.join(sorted({e.version for e in matches}))}")


# ── validate ────────────────────────────────────────────────────


@cli.command()
@click.argument("target", default="all")
def validate(target: str) -> None:
    """Validate packaging YAML files against their JSON Schemas.

    Checks recipe.yaml files for schema conformance, verifies that
    referenced build scripts and patches exist, and validates the
    dependency graph for cycles or missing dependencies.

    \b
    TARGET can be:
      all              Validate everything (default)
      components       Validate components.yaml only
      recipes          Validate all recipe.yaml files
      recipes/<name>   Validate a single recipe

    \b
    Examples:
      cvcpkg validate
      cvcpkg validate recipes/grpc
    """
    import importlib.util

    pkg_dir = Path(__file__).resolve().parent
    for ancestor in pkg_dir.parents:
        validate_script = ancestor / "packaging" / "validate.py"
        if validate_script.exists():
            break
    else:
        validate_script = Path.cwd() / "packaging" / "validate.py"

    if not validate_script.exists():
        raise click.ClickException(
            "cannot find packaging/validate.py — run from the libcvc-deps repo root."
        )

    spec = importlib.util.spec_from_file_location("validate", validate_script)
    if spec is None or spec.loader is None:
        raise click.ClickException("cannot load packaging/validate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["cvcpkg-validate", target]
    spec.loader.exec_module(mod)
    ret = mod.main()
    if ret != 0:
        raise SystemExit(ret)


# ── verify ──────────────────────────────────────────────────────


@cli.command()
@_prefix_opt
def verify(prefix: str) -> None:
    """Verify prefix integrity against the lockfile.

    Checks that every bundle recorded in the lockfile has a matching
    manifest.yaml in the prefix with the correct version.  Use this
    after install to confirm nothing is missing or corrupted.

    \b
    Example:
      cvcpkg verify --prefix ./deps
    """
    prefix_path = Path(prefix).resolve()
    lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
    if not lock_path.exists():
        raise click.ClickException(f"no lockfile at {lock_path}")

    from cvcpkg.lockfile import Lockfile
    from cvcpkg.manifest import BundleManifest

    lock = Lockfile.read(lock_path)
    click.echo(f"cvcpkg: verifying prefix {prefix_path} ({len(lock.bundles)} bundle(s)) ...")

    ok = True
    for entry in lock.bundles:
        manifest_path = prefix_path / "share" / "libcvc-deps" / entry.name / "manifest.yaml"
        if not manifest_path.exists():
            click.echo(f"  MISSING  {entry.name} — no manifest.yaml")
            ok = False
            continue
        manifest = BundleManifest.from_yaml(str(manifest_path))
        if manifest.version != entry.version:
            click.echo(
                f"  MISMATCH {entry.name}: lockfile says {entry.version}, "
                f"manifest says {manifest.version}"
            )
            ok = False
        else:
            click.echo(f"  OK       {entry.name} == {entry.version}")

    if ok:
        click.echo("cvcpkg: prefix verified.")
    else:
        raise click.ClickException("verification found issues.")


# ── lock ────────────────────────────────────────────────────────


@cli.command()
def lock() -> None:
    """Write or refresh the lockfile.

    The lockfile is written automatically by 'cvcpkg install'.
    This command is a convenience reminder.
    """
    click.echo("cvcpkg: lockfile is written automatically by 'cvcpkg install'.")
    click.echo("cvcpkg: to re-lock, run 'cvcpkg install --from <requirements>'.")


# ── sync ────────────────────────────────────────────────────────


@cli.command()
@_prefix_opt
def sync(prefix: str) -> None:
    """Ensure prefix matches lockfile.

    Re-downloads and extracts any bundles that are recorded in the
    lockfile but missing from the prefix.  Useful after a clean
    checkout or if the prefix was partially deleted.

    \b
    Example:
      cvcpkg sync --prefix ./deps
    """
    prefix_path = Path(prefix).resolve()
    lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
    if not lock_path.exists():
        raise click.ClickException(f"no lockfile at {lock_path}")

    from cvcpkg.cache import default_cache_dir
    from cvcpkg.installer import install_entry
    from cvcpkg.lockfile import Lockfile
    from cvcpkg.manifest import CatalogEntry

    lock = Lockfile.read(lock_path)
    cache_dir = default_cache_dir()
    installed = 0

    for entry in lock.bundles:
        manifest_path = prefix_path / "share" / "libcvc-deps" / entry.name / "manifest.yaml"
        if manifest_path.exists():
            continue
        if not entry.archive_url:
            raise click.ClickException(f"cannot sync {entry.name} — no archive_url in lockfile.")
        cat_entry = CatalogEntry(
            name=entry.name,
            version=entry.version,
            upstream_version=entry.upstream_version,
            cvc_revision=1,
            platform=lock.platform,
            arch=lock.arch,
            build_type=lock.config,
            link=lock.link,
            sha256=entry.sha256,
            size_bytes=entry.size_bytes,
            archive_url=entry.archive_url,
            source_release=entry.source_release,
        )
        click.echo(f"cvcpkg: syncing {entry.name} {entry.version} ...")
        install_entry(cat_entry, prefix_path, cache_dir)
        installed += 1

    if installed:
        click.echo(f"cvcpkg: synced {installed} bundle(s).")
    else:
        click.echo("cvcpkg: prefix is in sync.")


# ── catalog ─────────────────────────────────────────────────────


@cli.command()
@click.option("--refresh", is_flag=True, help="Re-fetch the catalog from the remote server.")
@click.option("--pin", type=int, metavar="REV", help="Pin to a specific catalog revision number.")
@click.option(
    "--show", is_flag=True, help="Show catalog revision, bundle count, and component names."
)
def catalog(refresh: bool, pin: int | None, show: bool) -> None:
    """Manage the component catalog.

    The catalog is a YAML index published to GitHub Pages that lists
    all available prebuilt component bundles with their versions,
    hashes, and download URLs.

    \b
    Examples:
      cvcpkg catalog --show
      cvcpkg catalog --refresh
      cvcpkg catalog --pin 42
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import fetch_catalog

    cache_dir = default_cache_dir()

    if refresh:
        cat = fetch_catalog(cache_dir=cache_dir)
        rev = cat.get("revision", "?")
        n = len(cat.get("bundles", []))
        click.echo(f"cvcpkg: catalog refreshed — revision {rev}, {n} bundle(s).")
        return

    if pin is not None:
        base = "https://transfix.github.io/libcvc-deps/catalog"
        url = f"{base}/{pin}.yaml"
        cat = fetch_catalog(url, cache_dir=cache_dir)
        rev = cat.get("revision", pin)
        n = len(cat.get("bundles", []))
        click.echo(f"cvcpkg: pinned to catalog revision {rev} ({n} bundle(s)).")
        return

    if show:
        cat = fetch_catalog(cache_dir=cache_dir)
        rev = cat.get("revision", "?")
        n = len(cat.get("bundles", []))
        click.echo(f"Catalog revision: {rev}")
        click.echo(f"Total bundles:    {n}")
        names = sorted({b["name"] for b in cat.get("bundles", [])})
        if names:
            click.echo(f"Components:       {', '.join(names)}")
        return

    click.echo("cvcpkg: use 'catalog --show', 'catalog --refresh', or 'catalog --pin REV'.")


# ── gc ──────────────────────────────────────────────────────────


@cli.command()
def gc() -> None:
    """Prune the local download cache.

    Removes downloaded archives from ~/.cache/cvcpkg/ that are no
    longer referenced by any installed prefix.  Safe to run at
    any time — bundles will be re-downloaded if needed.
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.cache import gc as run_gc

    cache_dir = default_cache_dir()
    if not cache_dir.is_dir():
        click.echo("cvcpkg: cache is empty.")
        return
    removed = run_gc(cache_dir, set())
    click.echo(f"cvcpkg: pruned {removed} cached archive(s).")


# ── push ────────────────────────────────────────────────────────


@cli.command()
@click.argument("archives", nargs=-1, required=True)
@click.option(
    "--dest",
    required=True,
    metavar="URI",
    help="Destination URI (e.g. s3://bucket/prefix, sftp://host/path, file:///local).",
)
def push(archives: tuple[str, ...], dest: str) -> None:
    """Push bundle archive(s) to a storage backend.

    Uploads each ARCHIVE file to DEST using the storage backend
    registered for its URI scheme.

    \b
    Examples:
      cvcpkg push dist/*.tar.zst --dest s3://my-bucket/cvcpkg/
      cvcpkg push dist/*.tar.zst --dest sftp://builds.example.com/pub/cvcpkg/
    """
    from cvcpkg.storage import get_backend

    backend = get_backend(dest)

    for archive in archives:
        p = Path(archive)
        if not p.is_file():
            raise click.ClickException(f"file not found: {archive}")
        dest_uri = dest.rstrip("/") + "/" + p.name
        click.echo(f"cvcpkg: uploading {p.name} -> {dest_uri}")
        try:
            with open(p, "rb") as f:
                backend.put(dest_uri, f)
        except NotImplementedError:
            raise click.ClickException(
                f"backend for {dest} does not support uploads (put)."
            ) from None
        click.echo(f"  done ({p.stat().st_size:,} bytes)")

    click.echo(f"cvcpkg: pushed {len(archives)} archive(s).")


# ── publish ─────────────────────────────────────────────────────


@cli.command()
@click.argument("archives", nargs=-1, required=True)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL (e.g. https://pkg.tx.wtf).  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token with publisher or admin role.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--release-tag",
    default="",
    help="Release tag (e.g. 'v1.3.0').  Empty for live builds.",
)
@click.option(
    "--chunked-threshold",
    default=10 * 1024 * 1024,
    type=int,
    help="Files larger than this (bytes) use chunked upload.  [default: 10MB]",
    show_default=True,
)
@click.option(
    "--org",
    default="",
    callback=_validate_org_slug,
    expose_value=True,
    is_eager=False,
    help="Organization slug to publish packages under.",
)
def publish(
    archives: tuple[str, ...],
    server: str,
    token: str,
    release_tag: str,
    chunked_threshold: int,
    org: str,
) -> None:
    """Publish bundle archive(s) to a cvcpkg-server via its REST API.

    Reads the embedded manifest.yaml from each archive to extract
    component metadata (name, version, platform, arch, config, link),
    then uploads the archive to the server.

    Small archives (< 10 MB by default) are uploaded in a single request
    via ``POST /v1/publish``.  Larger archives use chunked upload with
    automatic resume on transient failures.

    Archives are produced by ``cvcpkg pack``.

    \b
    Examples:
      cvcpkg publish dist/*.tar.gz --server https://pkg.tx.wtf --token cvctok_...
      CVCPKG_SERVER_URL=https://pkg.tx.wtf CVCPKG_TOKEN=cvctok_... cvcpkg publish dist/*.tar.gz
    """
    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    ok = 0
    failed: list[str] = []

    for archive in archives:
        p = Path(archive)
        if not p.is_file():
            raise click.ClickException(f"file not found: {archive}")

        # Extract manifest from archive
        manifest = _extract_manifest(p)
        bundle = manifest.get("bundle", {})
        name = bundle.get("name", "")
        version = bundle.get("version", "")
        plat = bundle.get("platform", "")
        arch = bundle.get("arch", "")
        build_type = bundle.get("config", "release")
        link = bundle.get("link", "shared")
        recipe_version = manifest.get("meta", {}).get("recipe_sha256", "")
        meta = manifest.get("meta", {})
        manifest_org = bundle.get("org", "")

        if not name or not version:
            raise click.ClickException(f"{p.name}: manifest missing name or version")

        file_size = p.stat().st_size
        display_name = f"{org or manifest_org}/{name}" if (org or manifest_org) else name
        label = f"{display_name}=={version} ({plat}/{arch}/{build_type}/{link})"

        # Pre-check: skip if this exact variant already exists on the server
        if _variant_exists(base, headers, name, version, plat, arch, build_type, link):
            click.echo(f"cvcpkg: skipping {label} (already on server)")
            continue

        click.echo(f"cvcpkg: publishing {label} " f"[{file_size / 1024 / 1024:.1f} MB] -> {base}")

        params = {
            "name": name,
            "version": version,
            "platform": plat,
            "arch": arch,
            "build_type": build_type,
            "link": link,
            "release_tag": release_tag,
            "recipe_version": recipe_version,
            "description": meta.get("description", ""),
            "homepage": meta.get("homepage", ""),
            "license": meta.get("license", ""),
            "maintainer": meta.get("maintainer", ""),
            "tags": meta.get("tags", ""),
            "org": org,
        }

        try:
            if file_size <= chunked_threshold:
                result = _publish_simple(base, headers, params, p)
            else:
                result = _publish_chunked(base, headers, params, p, file_size)

            if result == "published":
                ok += 1
            # result == "skipped" → already counted
        except click.ClickException as exc:
            click.echo(f"  ERROR: {exc.format_message()}", err=True)
            failed.append(label)

    click.echo(f"cvcpkg: published {ok}/{len(archives)} archive(s).")
    if failed:
        click.echo(f"cvcpkg: {len(failed)} archive(s) failed:", err=True)
        for f in failed:
            click.echo(f"  - {f}", err=True)
        raise click.ClickException(f"publish completed with {len(failed)} error(s)")


def _extract_manifest(archive_path: Path) -> dict:
    """Extract manifest.yaml from a cvcpkg archive."""
    import tarfile
    import zipfile

    manifest = None
    try:
        if archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                for entry in zf.namelist():
                    if entry.endswith("manifest.yaml"):
                        manifest = yaml.safe_load(zf.read(entry))
                        break
        else:
            with tarfile.open(archive_path, mode="r:*") as tf:
                for member in tf.getmembers():
                    if member.name.endswith("manifest.yaml"):
                        f = tf.extractfile(member)
                        if f:
                            manifest = yaml.safe_load(f.read())
                        break
    except (tarfile.TarError, zipfile.BadZipFile) as exc:
        raise click.ClickException(f"{archive_path.name}: cannot read archive: {exc}") from exc

    if not manifest:
        raise click.ClickException(
            f"{archive_path.name}: no manifest.yaml found — is this a cvcpkg archive?"
        )
    return manifest


def _variant_exists(
    base: str,
    headers: dict,
    name: str,
    version: str,
    platform: str,
    arch: str,
    build_type: str,
    link: str,
) -> bool:
    """Check if this exact package variant already exists on the server."""
    import httpx

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{base}/v1/packages/{name}",
                params={"platform": platform, "limit": 200},
                headers=headers,
            )
        if resp.status_code != 200:
            return False
        for pkg in resp.json().get("packages", []):
            if (
                pkg.get("version") == version
                and pkg.get("platform") == platform
                and pkg.get("arch") == arch
                and pkg.get("build_type") == build_type
                and pkg.get("link") == link
            ):
                return True
    except Exception:
        pass
    return False


def _publish_simple(base: str, headers: dict, params: dict, archive_path: Path) -> str:
    """Upload a small archive in a single POST request.  Returns 'published' or 'skipped'."""
    import httpx

    with httpx.Client(timeout=300) as client:
        with open(archive_path, "rb") as f:
            resp = client.post(
                f"{base}/v1/publish",
                params=params,
                files={"file": (archive_path.name, f, "application/octet-stream")},
                headers=headers,
            )

    if resp.status_code == 200:
        data = resp.json()
        click.echo(f"  published: sha256={data['sha256']}")
        return "published"
    elif resp.status_code == 409:
        click.echo(f"  skipped (already published): {resp.json().get('detail', '')}")
        return "skipped"
    else:
        raise click.ClickException(f"publish failed ({resp.status_code}): {resp.text}")


def _publish_chunked(
    base: str,
    headers: dict,
    params: dict,
    archive_path: Path,
    file_size: int,
    max_retries: int = 3,
) -> str:
    """Upload a large archive using chunked upload with resume.

    Returns 'published' or 'skipped'.
    """
    import hashlib

    import httpx

    # 1. Init upload session
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{base}/v1/upload/init",
            params={**params, "total_size": file_size},
            headers=headers,
        )

    if resp.status_code == 409:
        click.echo(f"  skipped (already published): {resp.json().get('detail', '')}")
        return "skipped"
    if resp.status_code != 201:
        raise click.ClickException(f"upload init failed ({resp.status_code}): {resp.text}")

    init_data = resp.json()
    upload_id = init_data["upload_id"]
    chunk_size = init_data.get("chunk_size", 8 * 1024 * 1024)

    # 2. Upload chunks with retry + resume
    offset = 0
    sha256 = hashlib.sha256()

    with open(archive_path, "rb") as f:
        while offset < file_size:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            sha256.update(chunk)
            end = offset + len(chunk) - 1

            for attempt in range(1, max_retries + 1):
                try:
                    with httpx.Client(timeout=120) as client:
                        resp = client.patch(
                            f"{base}/v1/upload/{upload_id}",
                            content=chunk,
                            headers={
                                **headers,
                                "Content-Type": "application/octet-stream",
                                "Content-Range": f"bytes {offset}-{end}/{file_size}",
                            },
                        )

                    if resp.status_code == 409:
                        # Offset mismatch — check server status and resume
                        with httpx.Client(timeout=30) as client:
                            status_resp = client.get(
                                f"{base}/v1/upload/{upload_id}",
                                headers=headers,
                            )
                        if status_resp.status_code == 200:
                            server_offset = status_resp.json()["bytes_received"]
                            if server_offset > offset:
                                # Server already has this chunk, skip forward
                                offset = server_offset
                                f.seek(offset)
                                # Recompute hash from start (needed for verification)
                                sha256 = hashlib.sha256()
                                f.seek(0)
                                remaining = offset
                                while remaining > 0:
                                    rehash_chunk = f.read(min(chunk_size, remaining))
                                    sha256.update(rehash_chunk)
                                    remaining -= len(rehash_chunk)
                                break
                        raise click.ClickException(f"chunk upload offset mismatch: {resp.text}")

                    if resp.status_code != 200:
                        raise click.ClickException(
                            f"chunk upload failed ({resp.status_code}): {resp.text}"
                        )

                    received = resp.json()["bytes_received"]
                    pct = received * 100 // file_size
                    click.echo(
                        f"  chunk {offset}-{end}: "
                        f"{received / 1024 / 1024:.1f}/{file_size / 1024 / 1024:.1f} MB ({pct}%)"
                    )
                    offset = received
                    break  # success

                except httpx.TransportError as exc:
                    if attempt < max_retries:
                        import time

                        wait = 2**attempt
                        click.echo(
                            f"  chunk upload error (attempt {attempt}/{max_retries}): "
                            f"{exc} — retrying in {wait}s"
                        )
                        time.sleep(wait)
                    else:
                        raise click.ClickException(
                            f"chunk upload failed after {max_retries} retries: {exc}"
                        ) from exc

    # 3. Finalise
    expected_sha256 = sha256.hexdigest()
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{base}/v1/upload/{upload_id}/complete",
            params={"expected_sha256": expected_sha256},
            headers=headers,
        )

    if resp.status_code == 200:
        data = resp.json()
        click.echo(f"  published (chunked): sha256={data['sha256']}")
        return "published"
    else:
        raise click.ClickException(f"upload complete failed ({resp.status_code}): {resp.text}")


# ── add ─────────────────────────────────────────────────────────


@cli.command()
@click.argument("components", nargs=-1, required=True)
@click.option(
    "--from",
    "from_file",
    type=click.Path(exists=True),
    required=True,
    help="Path to cvc-requirements.yaml to add components to.",
)
def add(components: tuple[str, ...], from_file: str) -> None:
    """Add component(s) to a requirements file.

    Appends each COMPONENT to the components list in the
    given cvc-requirements.yaml file if not already present.

    \b
    Examples:
      cvcpkg add zlib boost --from cvc-requirements.yaml
      cvcpkg add 'hdf5==1.14.5+cvc.1' --from cvc-requirements.yaml
    """
    path = Path(from_file)
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    comp_list = data.get("components", [])
    existing_names = set()
    for c in comp_list:
        if isinstance(c, str):
            existing_names.add(c.split("==")[0].split(">=")[0].split("<=")[0])
        elif isinstance(c, dict):
            existing_names.add(c.get("name", ""))

    added = []
    for comp in components:
        name = comp.split("==")[0].split(">=")[0].split("<=")[0]
        if name in existing_names:
            click.echo(f"cvcpkg: {name} already in {from_file}, skipping.")
            continue
        if "==" in comp:
            n, v = comp.split("==", 1)
            comp_list.append({"name": n, "version": f"=={v}"})
        else:
            comp_list.append(comp)
        added.append(name)

    if not added:
        click.echo("cvcpkg: nothing to add.")
        return

    data["components"] = comp_list
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    click.echo(f"cvcpkg: added {', '.join(added)} to {from_file}")


# ── remove ──────────────────────────────────────────────────────


@cli.command()
@click.argument("components", nargs=-1, required=True)
@click.option(
    "--from",
    "from_file",
    type=click.Path(exists=True),
    required=True,
    help="Path to cvc-requirements.yaml to remove components from.",
)
def remove(components: tuple[str, ...], from_file: str) -> None:
    """Remove component(s) from a requirements file.

    Removes each COMPONENT from the components list in the
    given cvc-requirements.yaml file.

    \b
    Examples:
      cvcpkg remove boost --from cvc-requirements.yaml
    """
    path = Path(from_file)
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    comp_list = data.get("components", [])
    remove_set = set(components)
    new_list = []
    removed = []
    for c in comp_list:
        if isinstance(c, str):
            name = c.split("==")[0].split(">=")[0].split("<=")[0]
        elif isinstance(c, dict):
            name = c.get("name", "")
        else:
            new_list.append(c)
            continue
        if name in remove_set:
            removed.append(name)
        else:
            new_list.append(c)

    if not removed:
        click.echo(f"cvcpkg: none of {', '.join(components)} found in {from_file}.")
        return

    data["components"] = new_list
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    click.echo(f"cvcpkg: removed {', '.join(removed)} from {from_file}")


# ── world ───────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--from",
    "from_file",
    type=click.Path(exists=True),
    required=True,
    help="Path to cvc-requirements.yaml listing components to build.",
)
@_platform_opt
@_config_opt
@_link_opt
@_prefix_opt
@_keep_build_opt
@_recipes_dir_opt
def world(
    from_file: str,
    platform: str,
    config: str,
    link: str,
    prefix: str,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
) -> None:
    """Build all recipes needed by a requirements file.

    Reads a cvc-requirements.yaml, resolves dependencies between
    recipes, and builds them all in topological order into the prefix.

    \b
    Examples:
      cvcpkg world --from cvc-requirements.yaml --prefix ./prefix
      cvcpkg world --from cvc-requirements.yaml --config debug
    """
    from cvcpkg.builder import (
        BuildContext,
        Recipe,
        build_recipe,
        find_recipes_dir,
        load_all_recipes,
        resolve_build_order,
    )
    from cvcpkg.manifest import Requirements

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve()

    reqs = Requirements.from_yaml(from_file)
    requested = {c.name for c in reqs.components if not c.exclude}

    # Load all recipes.
    if recipes_dirs:
        rdirs = [Path(d) for d in recipes_dirs]
        all_recipes = load_all_recipes(rdirs)
    else:
        all_recipes = [
            Recipe.load(d) for d in find_recipes_dir().iterdir() if (d / "recipe.yaml").is_file()
        ]

    by_name = {r.name: r for r in all_recipes}

    # Gather the transitive closure of requested components.
    needed: set[str] = set()

    def _gather(name: str) -> None:
        if name in needed:
            return
        needed.add(name)
        r = by_name.get(name)
        if r is None:
            return
        for dep in r.raw.get("depends", {}).get("build", []):
            dep_name = dep if isinstance(dep, str) else dep.get("name", "")
            if dep_name:
                _gather(dep_name)

    for name in requested:
        _gather(name)

    if not needed:
        click.echo("cvcpkg: no recipes match the requirements.")
        return

    # Filter to recipes we actually have.
    to_build = [r for r in all_recipes if r.name in needed]
    if not to_build:
        click.echo("cvcpkg: no matching recipes found.")
        return

    order = resolve_build_order(to_build, platform=plat)
    click.echo(f"cvcpkg: building {len(order)} recipe(s) in dependency order:")
    for r in order:
        click.echo(f"  {r.name} {r.full_version}")

    for r in order:
        click.echo(f"cvcpkg: building {r.name} ...")
        ctx = BuildContext(recipe=r, platform=plat, config=config, link=link)
        ctx.install_dir = prefix_path
        ctx.keep_build_dir = keep_build_dir
        build_recipe(ctx)
        click.echo(f"  {r.name} done.")

    click.echo(f"cvcpkg: world build complete — {len(order)} recipe(s) built to {prefix_path}")


# ── Helper: resolve recipe dir ──────────────────────────────────


def _resolve_recipe_dir(name: str, recipes_dirs: tuple[str, ...] = ()) -> Path:
    """Resolve a recipe name or path to its directory.

    Accepts either a path to a directory containing recipe.yaml, or
    a bare recipe name (e.g. "grpc") which is looked up in the
    provided *recipes_dirs* (later dirs take precedence) or the
    auto-detected recipes/ directory.
    """
    p = Path(name)
    if p.is_dir() and (p / "recipe.yaml").is_file():
        return p.resolve()

    # Search provided dirs in reverse order (later = higher priority).
    for rdir in reversed(recipes_dirs):
        candidate = Path(rdir) / name
        if candidate.is_dir() and (candidate / "recipe.yaml").is_file():
            return candidate.resolve()

    # Fallback to auto-detected dir.
    from cvcpkg.builder import find_recipes_dir

    recipes_dir = find_recipes_dir()
    candidate = recipes_dir / name
    if candidate.is_dir() and (candidate / "recipe.yaml").is_file():
        return candidate.resolve()
    raise click.ClickException(f"Recipe not found: {name}")


def _auto_platform(platform: str) -> str:
    """Resolve 'auto' to the detected platform, pass others through."""
    if platform == "auto":
        from cvcpkg.platform import detect_platform

        return detect_platform()
    return platform


# ── build ───────────────────────────────────────────────────────


@cli.command()
@click.argument("recipe", nargs=-1, required=True)
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Install prefix.")
@_keep_build_opt
@_recipes_dir_opt
@click.option(
    "--with-deps/--no-deps",
    default=True,
    help="Also build dependencies in order (default: --with-deps).",
)
@click.option(
    "--host-platform",
    default="",
    help="Host platform for cross-compilation (e.g. linux, macos, windows).",
)
def build(
    recipe: tuple[str, ...],
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
    with_deps: bool,
    host_platform: str,
) -> None:
    """Build one or more recipes from source.

    Downloads the upstream source (or uses vendored sources), applies
    patches, and runs the recipe's platform-specific build script.
    Results are installed into --prefix.

    Dependencies are automatically resolved and built first unless
    --no-deps is specified.

    \b
    Examples:
      cvcpkg build zlib --prefix ./prefix
      cvcpkg build grpc protobuf --config debug --link static
      cvcpkg build mypkg --recipes-dir ./my-recipes --recipes-dir recipes
      cvcpkg build vtk --no-deps --prefix ./prefix
    """
    from cvcpkg.builder import build_recipe, find_recipes_dir, resolve_build_order

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve() if prefix else None

    if with_deps:
        # Resolve all deps and build in topological order
        rdirs = [Path(d) for d in recipes_dirs] if recipes_dirs else [find_recipes_dir()]
        from cvcpkg.builder import list_recipes, load_all_recipes

        if len(rdirs) > 1:
            all_recipes = load_all_recipes(rdirs)
        else:
            all_recipes = list_recipes(rdirs[0])
        by_name = {r.name: r for r in all_recipes}

        # Collect requested + their transitive deps
        needed: set[str] = set()

        def _collect(name: str) -> None:
            if name in needed:
                return
            needed.add(name)
            if name not in by_name:
                return
            r = by_name[name]
            deps = r.raw.get("depends", {}).get("build", [])
            for d in deps:
                dep_name = d if isinstance(d, str) else d.get("name", "")
                plats = d.get("platforms") if isinstance(d, dict) else None
                if plats and plat not in plats:
                    continue
                if dep_name:
                    _collect(dep_name)

        for name in recipe:
            _collect(name)

        # Filter to what's available
        available = [by_name[n] for n in needed if n in by_name]

        # Split into target-platform recipes and host-tool recipes.
        # Host tools are deps that have no matrix entry for the target
        # platform but do have one for the native host (e.g. emsdk when
        # cross-compiling to wasm).
        from cvcpkg.platform import detect_platform

        host_plat = detect_platform()
        target_recipes: list = []
        host_tool_recipes: list = []
        for r in available:
            if any(m.platform == plat for m in r.build_matrix):
                target_recipes.append(r)
            elif plat != host_plat and any(m.platform == host_plat for m in r.build_matrix):
                host_tool_recipes.append(r)

        # Build host tools first (e.g. emsdk), then target recipes
        if host_tool_recipes:
            host_ordered = resolve_build_order(host_tool_recipes, host_plat)
            for r in host_ordered:
                print(f"\ncvcpkg: ══ {r.name} ({r.full_version}) [host tool] ══")
                build_recipe(
                    r.recipe_dir,
                    platform=host_plat,
                    config=config,
                    link=link,
                    prefix=prefix_path,
                    keep_build_dir=keep_build_dir,
                )

        ordered = resolve_build_order(target_recipes, plat)
        for r in ordered:
            print(f"\ncvcpkg: ══ {r.name} ({r.full_version}) ══")
            build_recipe(
                r.recipe_dir,
                platform=plat,
                config=config,
                link=link,
                prefix=prefix_path,
                keep_build_dir=keep_build_dir,
                host_platform=host_platform,
            )
    else:
        for name in recipe:
            recipe_dir = _resolve_recipe_dir(name, recipes_dirs)
            build_recipe(
                recipe_dir,
                platform=plat,
                config=config,
                link=link,
                prefix=prefix_path,
                keep_build_dir=keep_build_dir,
                host_platform=host_platform,
            )


# ── pack ────────────────────────────────────────────────────────


@cli.command()
@click.argument("recipe", nargs=-1, required=True)
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Install prefix.")
@click.option("--output-dir", type=click.Path(), default="./dist", help="Output directory.")
@_keep_build_opt
@_recipes_dir_opt
@_maintainer_opt
@click.option(
    "--signing-key",
    type=click.Path(exists=True),
    default=None,
    help="Path to Ed25519 private key to sign archives.",
)
def pack(
    recipe: tuple[str, ...],
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    output_dir: str,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
    maintainer: str,
    signing_key: str | None,
) -> None:
    """Build and archive one or more recipes.

    Like 'build', but also creates a distributable .tar.gz archive
    for each recipe containing the installed files, manifest.yaml,
    and SHA-256 checksum.  Archives are written to --output-dir.

    \b
    Example:
      cvcpkg pack zlib boost --output-dir ./dist
    """
    from cvcpkg.builder import pack_recipe

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve() if prefix else None
    output = Path(output_dir).resolve()

    for name in recipe:
        recipe_dir = _resolve_recipe_dir(name, recipes_dirs)
        archive, sha, size = pack_recipe(
            recipe_dir,
            platform=plat,
            config=config,
            link=link,
            prefix=prefix_path,
            output_dir=output,
            keep_build_dir=keep_build_dir,
            maintainer=maintainer,
        )
        click.echo(f"  {archive} ({size:,} bytes, sha256={sha})")
        if signing_key:
            from cvcpkg.signing import sign_file, write_signature

            sig = sign_file(archive, Path(signing_key))
            sig_path = archive.with_suffix(archive.suffix + ".sig")
            write_signature(sig, sig_path)
            click.echo(f"  Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}…)")


# ── build-all ───────────────────────────────────────────────────


@cli.command("build-all")
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Shared install prefix.")
@_keep_build_opt
@_recipes_dir_opt
@click.option(
    "--work-dir",
    type=click.Path(),
    default=None,
    envvar="CVCPKG_WORK_DIR",
    help="Parent directory for intermediate build trees.  "
    "Defaults to the system temp directory ($TMPDIR / /tmp).  "
    "Set this to a fast or large scratch volume when the "
    "default temp partition is too small or too slow.",
)
@click.option(
    "--host-platform",
    default="",
    help="Host platform for cross-compilation (e.g. linux, macos, windows).",
)
@click.option(
    "--keep-going",
    is_flag=True,
    default=False,
    help="Continue building after a recipe fails.  "
    "Recipes whose dependencies failed are skipped.  "
    "A summary of failures is printed at the end.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Disable the local build cache entirely (no lookups, no stores).",
)
@click.option(
    "--force-clean",
    is_flag=True,
    default=False,
    help="Skip cache lookups (rebuild from source) but still store results.",
)
@click.option(
    "--server-cache",
    default="",
    envvar="CVCPKG_SERVER_CACHE",
    help="Server cache URL (e.g. http://cache.example.com). "
    "Enables server-side cache lookups and optional pushes.",
)
@click.option(
    "--server-cache-token",
    default="",
    envvar="CVCPKG_SERVER_CACHE_TOKEN",
    help="Bearer token for authenticated server cache access.",
)
@click.option(
    "--server-cache-push",
    is_flag=True,
    default=False,
    envvar="CVCPKG_SERVER_CACHE_PUSH",
    help="Push successful builds to the server cache.",
)
@click.option(
    "--no-server-cache",
    is_flag=True,
    default=False,
    help="Disable server cache entirely (both pull and push).",
)
@click.option(
    "--server-cache-org",
    default="",
    envvar="CVCPKG_SERVER_CACHE_ORG",
    help="Organization slug for server cache queries.",
)
def build_all_cmd(
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
    work_dir: str | None,
    host_platform: str,
    keep_going: bool,
    no_cache: bool,
    force_clean: bool,
    server_cache: str,
    server_cache_token: str,
    server_cache_push: bool,
    no_server_cache: bool,
    server_cache_org: str,
) -> None:
    """Build all recipes in dependency order.

    Performs a topological sort of the recipe dependency graph and
    builds each recipe in order into a shared --prefix.  Each recipe
    can find previously-built dependencies via CMAKE_PREFIX_PATH.

    This is the primary command used by CI to produce the full
    dependency bundle.

    \b
    Example:
      cvcpkg build-all --platform linux --config release --link shared \\
          --prefix ./prefix --recipes-dir recipes
    """
    from cvcpkg.builder import build_all, find_recipes_dir

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve() if prefix else None
    work_dir_root = Path(work_dir).resolve() if work_dir else None
    rdirs = [Path(d) for d in recipes_dirs] if recipes_dirs else [find_recipes_dir()]

    contexts = build_all(
        rdirs if len(rdirs) > 1 else rdirs[0],
        platform=plat,
        config=config,
        link=link,
        prefix=prefix_path,
        keep_build_dir=keep_build_dir,
        host_platform=host_platform,
        keep_going=keep_going,
        no_cache=no_cache,
        force_clean=force_clean,
        server_cache_url=server_cache,
        server_cache_token=server_cache_token,
        server_cache_push=server_cache_push,
        no_server_cache=no_server_cache,
        server_cache_org=server_cache_org,
        work_dir_root=work_dir_root,
    )
    failures = getattr(contexts, "failures", [])
    if failures:
        raise SystemExit(1)


# ── pack-all ────────────────────────────────────────────────────


@cli.command("pack-all")
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Shared install prefix.")
@click.option("--output-dir", type=click.Path(), default="./dist", help="Output directory.")
@_keep_build_opt
@_recipes_dir_opt
@_maintainer_opt
@click.option(
    "--signing-key",
    type=click.Path(exists=True),
    default=None,
    help="Path to Ed25519 private key to sign archives.",
)
@click.option(
    "--work-dir",
    type=click.Path(),
    default=None,
    envvar="CVCPKG_WORK_DIR",
    help="Parent directory for intermediate build trees.  "
    "Defaults to the system temp directory ($TMPDIR / /tmp).  "
    "Set this to a fast or large scratch volume when the "
    "default temp partition is too small or too slow.",
)
@click.option(
    "--host-platform",
    default="",
    help="Host platform for cross-compilation (e.g. linux, macos, windows). "
    "Selects the matching build script when multiple host_platform entries exist.",
)
@click.option(
    "--shard",
    default="",
    help="Recipe shard in INDEX/TOTAL format (e.g. 0/3). "
    "Only recipes assigned to this shard are packaged; "
    "their dependencies are still built.",
)
@click.option(
    "--org",
    default="",
    callback=_validate_org_slug,
    expose_value=True,
    is_eager=False,
    help="Organization slug to embed in manifests.",
)
@click.option(
    "--keep-going",
    is_flag=True,
    default=False,
    help="Continue building after a recipe fails.  "
    "Recipes whose dependencies failed are skipped.  "
    "A summary of failures is printed at the end.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Disable the local build cache entirely (no lookups, no stores).",
)
@click.option(
    "--force-clean",
    is_flag=True,
    default=False,
    help="Skip cache lookups (rebuild from source) but still store results.",
)
@click.option(
    "--server-cache",
    default="",
    envvar="CVCPKG_SERVER_CACHE",
    help="Server cache URL (e.g. http://cache.example.com). "
    "Enables server-side cache lookups and optional pushes.",
)
@click.option(
    "--server-cache-token",
    default="",
    envvar="CVCPKG_SERVER_CACHE_TOKEN",
    help="Bearer token for authenticated server cache access.",
)
@click.option(
    "--server-cache-push",
    is_flag=True,
    default=False,
    envvar="CVCPKG_SERVER_CACHE_PUSH",
    help="Push successful builds to the server cache.",
)
@click.option(
    "--no-server-cache",
    is_flag=True,
    default=False,
    help="Disable server cache entirely (both pull and push).",
)
@click.option(
    "--server-cache-org",
    default="",
    envvar="CVCPKG_SERVER_CACHE_ORG",
    help="Organization slug for server cache queries.",
)
def pack_all_cmd(
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    output_dir: str,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
    maintainer: str,
    signing_key: str | None,
    work_dir: str | None,
    host_platform: str,
    shard: str,
    org: str,
    keep_going: bool,
    no_cache: bool,
    force_clean: bool,
    server_cache: str,
    server_cache_token: str,
    server_cache_push: bool,
    no_server_cache: bool,
    server_cache_org: str,
) -> None:
    """Build and archive all recipes.

    Each recipe is built into its own isolated install directory while
    using a shared prefix for dependency lookup.  After each build the
    recipe's files are merged into the prefix so later recipes can find
    them, and a per-component archive is created in --output-dir.

    \b
    Example:
      cvcpkg pack-all --platform linux --config release --link shared \\
          --output-dir ./dist --recipes-dir recipes
    \b
      # Shard across 3 hosts for parallel wasm builds:
      cvcpkg pack-all --platform wasm --shard 0/3 --host-platform linux ...
      cvcpkg pack-all --platform wasm --shard 1/3 --host-platform macos ...
      cvcpkg pack-all --platform wasm --shard 2/3 --host-platform windows ...
    """
    from cvcpkg.builder import (
        build_all,
        create_archive,
        find_recipes_dir,
        generate_manifest,
        list_recipes,
        stage_bundle,
    )
    from cvcpkg.platform import detect_arch

    plat = _auto_platform(platform)
    arch = detect_arch()
    prefix_path = Path(prefix).resolve() if prefix else None
    work_dir_root = Path(work_dir).resolve() if work_dir else None
    output = Path(output_dir).resolve()
    rdirs = [Path(d) for d in recipes_dirs] if recipes_dirs else [find_recipes_dir()]

    # Load all recipes for chain_hash computation
    if len(rdirs) > 1:
        from cvcpkg.builder import load_all_recipes

        all_recipe_list = load_all_recipes(rdirs)
    else:
        all_recipe_list = list_recipes(rdirs[0])
    all_recipes = {r.name: r for r in all_recipe_list}

    # Parse shard spec
    shard_tuple: tuple[int, int] | None = None
    if shard:
        try:
            idx_s, total_s = shard.split("/")
            shard_tuple = (int(idx_s), int(total_s))
            if shard_tuple[0] < 0 or shard_tuple[0] >= shard_tuple[1]:
                raise ValueError
        except (ValueError, TypeError):
            raise click.BadParameter(
                f"Invalid shard format '{shard}'. Expected INDEX/TOTAL (e.g. 0/3)."
            )

    contexts = build_all(
        rdirs if len(rdirs) > 1 else rdirs[0],
        platform=plat,
        config=config,
        link=link,
        prefix=prefix_path,
        keep_build_dir=keep_build_dir,
        per_component=True,
        host_platform=host_platform,
        shard=shard_tuple,
        keep_going=keep_going,
        no_cache=no_cache,
        force_clean=force_clean,
        server_cache_url=server_cache,
        server_cache_token=server_cache_token,
        server_cache_push=server_cache_push,
        no_server_cache=no_server_cache,
        server_cache_org=server_cache_org,
        work_dir_root=work_dir_root,
    )

    output.mkdir(parents=True, exist_ok=True)
    for ctx in contexts:
        # Cross-compiled recipes (e.g. wasm built on linux) use their
        # actual target platform and arch, not the host's.
        ctx_plat = ctx.platform
        ctx_arch = "wasm32" if ctx_plat == "wasm" else arch
        manifest = generate_manifest(
            ctx.recipe,
            ctx.install_dir,
            ctx_plat,
            ctx_arch,
            config,
            link,
            maintainer=maintainer,
            all_recipes=all_recipes,
            org_slug=org,
        )
        staging = ctx.work_dir / "staging"
        staging.mkdir(exist_ok=True)
        stage_bundle(ctx.install_dir, manifest, staging, recipe_dir=ctx.recipe.recipe_dir)
        archive_path, sha256, size = create_archive(
            staging,
            output,
            ctx.recipe.name,
            ctx.recipe.full_version,
            ctx_plat,
            ctx_arch,
            config,
            link,
        )
        click.echo(f"  {archive_path.name} ({size:,} bytes, sha256={sha256})")
        if signing_key:
            from cvcpkg.signing import sign_file, write_signature

            sig = sign_file(archive_path, Path(signing_key))
            sig_path = archive_path.with_suffix(archive_path.suffix + ".sig")
            write_signature(sig, sig_path)
            click.echo(f"  Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}…)")

    failures = getattr(contexts, "failures", [])
    if failures:
        raise SystemExit(1)


# ── recipes ─────────────────────────────────────────────────────


@cli.command()
@click.option("--list", "mode", flag_value="list", default=True, help="List all recipes.")
@click.option("--show", "show_name", metavar="NAME", help="Show details of a recipe.")
@click.option("--validate", "mode", flag_value="validate", help="Validate all recipes.")
@click.option(
    "--tag",
    metavar="TAG",
    help="Filter recipe list to those with this tag (e.g. math, graphics).",
)
@_recipes_dir_opt
def recipes(
    mode: str, show_name: str | None, tag: str | None, recipes_dirs: tuple[str, ...]
) -> None:
    """List or inspect recipes.

    \b
    Examples:
      cvcpkg recipes               # list all recipes
      cvcpkg recipes --show grpc   # show details of a recipe
      cvcpkg recipes --validate    # validate all recipe.yaml files
      cvcpkg recipes --tag math    # list only math recipes
      cvcpkg recipes --recipes-dir ./my-recipes  # use custom recipe dir
    """
    from cvcpkg.builder import Recipe, list_recipes, load_all_recipes

    if show_name:
        recipe_dir = _resolve_recipe_dir(show_name, recipes_dirs)
        recipe = Recipe.load(recipe_dir)
        click.echo(f"Name:     {recipe.name}")
        click.echo(f"Version:  {recipe.full_version}")
        click.echo(f"Source:   {recipe.source.type}")
        if recipe.source.url:
            click.echo(f"URL:      {recipe.source.url}")
        platforms = [m.platform for m in recipe.build_matrix]
        click.echo(f"Platforms: {', '.join(platforms)}")
        if recipe.tags:
            click.echo(f"Tags:     {', '.join(recipe.tags)}")
        deps = recipe.raw.get("depends", {}).get("build", [])
        if deps:
            dep_names = []
            for d in deps:
                if isinstance(d, str):
                    dep_names.append(d)
                else:
                    dep_org = d.get("org", "")
                    label = f"{dep_org}/{d.get('name', '?')}" if dep_org else d.get("name", "?")
                    plats = d.get("platforms")
                    if plats:
                        label += f" [{','.join(plats)}]"
                    dep_names.append(label)
            click.echo(f"Depends:  {', '.join(dep_names)}")
        return

    if mode == "validate":
        ctx = click.get_current_context()
        ctx.invoke(validate, target="all")
        return

    # Default: list
    if recipes_dirs:
        rdirs = [Path(d) for d in recipes_dirs]
        all_recipes = load_all_recipes(rdirs)
    else:
        all_recipes = list_recipes()

    # Apply tag filter if requested.
    if tag:
        all_recipes = [r for r in all_recipes if tag in r.tags]

    if not all_recipes:
        msg = f"no recipes found with tag '{tag}'." if tag else "no recipes found."
        raise click.ClickException(msg)
    click.echo(f"{'Name':<20} {'Version':<18} {'Tags':<20} {'Platforms'}")
    click.echo("-" * 78)
    for r in all_recipes:
        platforms = ", ".join(m.platform for m in r.build_matrix)
        tags_str = ", ".join(r.tags) if r.tags else ""
        click.echo(f"{r.name:<20} {r.full_version:<18} {tags_str:<20} {platforms}")


# ── key ─────────────────────────────────────────────────────────


@cli.group()
def key() -> None:
    """Manage Ed25519 signing keys.

    Generate keypairs, import public keys from trusted publishers,
    and list the local keyring.

    \b
    Examples:
      cvcpkg key generate --label release
      cvcpkg key list
      cvcpkg key import publisher.pub --label upstream
      cvcpkg key export --label release
    """


@key.command("generate")
@click.option("--label", required=True, help="Human-readable label for this key.")
@click.option("--password", default=None, help="Password-protect the private key.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_generate(label: str, password: str | None, keys_dir: str | None) -> None:
    """Generate a new Ed25519 signing keypair."""
    from cvcpkg.signing import generate_keypair

    kd = Path(keys_dir) if keys_dir else None
    info = generate_keypair(label, keys_dir=kd, password=password)
    click.echo(f"Generated key '{info.label}'")
    click.echo(f"  Fingerprint: {info.fingerprint}")
    click.echo(f"  Private key: {info.path}")
    click.echo(f"  Public key:  {info.path.with_suffix('.pub') if info.path else 'N/A'}")


@key.command("list")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_list(keys_dir: str | None) -> None:
    """List all keys in the keyring."""
    from cvcpkg.signing import list_keys

    kd = Path(keys_dir) if keys_dir else None
    keys = list_keys(kd)
    if not keys:
        click.echo("No keys found.")
        return
    for ki in keys:
        kind = "private+public" if ki.has_private else "public only"
        click.echo(f"  {ki.label:<20} {ki.fingerprint[:16]}…  ({kind})")


@key.command("import")
@click.argument("pub_file", type=click.Path(exists=True))
@click.option("--label", required=True, help="Label for the imported key.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_import(pub_file: str, label: str, keys_dir: str | None) -> None:
    """Import a public key into the trusted keyring."""
    from cvcpkg.signing import import_public_key

    kd = Path(keys_dir) if keys_dir else None
    pub_pem = Path(pub_file).read_text()
    info = import_public_key(pub_pem, label, keys_dir=kd)
    click.echo(f"Imported '{info.label}' ({info.fingerprint[:16]}…)")


@key.command("export")
@click.option("--label", required=True, help="Label of the key to export.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_export(label: str, keys_dir: str | None) -> None:
    """Export a public key (PEM) to stdout."""
    from cvcpkg.signing import list_keys

    kd = Path(keys_dir) if keys_dir else None
    keys = list_keys(kd)
    for ki in keys:
        if ki.label == label:
            click.echo(ki.public_pem, nl=False)
            return
    raise click.ClickException(f"Key '{label}' not found")


# ── sign ────────────────────────────────────────────────────────


@cli.command()
@click.argument("archive", type=click.Path(exists=True))
@click.option(
    "--signing-key",
    required=True,
    type=click.Path(exists=True),
    help="Path to Ed25519 private key (.key).",
)
@click.option("--password", default=None, help="Password for the signing key.")
def sign(archive: str, signing_key: str, password: str | None) -> None:
    """Sign an archive file.

    Creates a detached ``<archive>.sig`` signature file alongside
    the archive.

    \b
    Example:
      cvcpkg sign dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz \\
          --signing-key ~/.config/cvcpkg/keys/release.key
    """
    from cvcpkg.signing import sign_file, write_signature

    archive_path = Path(archive)
    sig = sign_file(archive_path, Path(signing_key), password)
    sig_path = archive_path.with_suffix(archive_path.suffix + ".sig")
    write_signature(sig, sig_path)
    click.echo(f"Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}…)")


# ── verify-sig ──────────────────────────────────────────────────


@cli.command("verify-sig")
@click.argument("archive", type=click.Path(exists=True))
@click.option(
    "--sig-file",
    type=click.Path(exists=True),
    default=None,
    help="Signature file (defaults to <archive>.sig).",
)
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def verify_sig(archive: str, sig_file: str | None, keys_dir: str | None) -> None:
    """Verify the signature of an archive against trusted keys.

    \b
    Example:
      cvcpkg verify-sig dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz
    """
    from cvcpkg.signing import read_signature, verify_file

    archive_path = Path(archive)
    if sig_file:
        sp = Path(sig_file)
    else:
        sp = archive_path.with_suffix(archive_path.suffix + ".sig")
    if not sp.is_file():
        raise click.ClickException(f"Signature file not found: {sp}")

    kd = Path(keys_dir) if keys_dir else None
    sig = read_signature(sp)
    ki = verify_file(archive_path, sig, kd)
    click.echo(f"Verified: signed by '{ki.label}' ({ki.fingerprint[:16]}…)")


# ── rev-bump ────────────────────────────────────────────────────


@cli.command("rev-bump")
@click.argument("recipe_name")
@_recipes_dir_opt
@_platform_opt
@click.option(
    "--no-cascade",
    is_flag=True,
    default=False,
    help="Only bump the named recipe; do not bump downstream dependents.",
)
def rev_bump_cmd(
    recipe_name: str,
    recipes_dirs: tuple[str, ...],
    platform: str,
    no_cascade: bool,
) -> None:
    """Bump the cvc_revision for a recipe and its dependents.

    Increments the cvc_revision field in recipe.yaml for RECIPE_NAME
    and, by default, for every recipe that transitively depends on it.
    This ensures that a patched dependency triggers rebuilds of all
    downstream packages.

    \b
    Examples:
      cvcpkg rev-bump openssl
      cvcpkg rev-bump zlib --no-cascade
      cvcpkg rev-bump openssl --platform linux
    """
    from cvcpkg.builder import find_recipes_dir, rev_bump

    rdirs = [Path(d) for d in recipes_dirs] if recipes_dirs else [find_recipes_dir()]
    recipes_dir = rdirs[0]

    bumped = rev_bump(
        recipe_name,
        recipes_dir,
        platform=platform or "",
        cascade=not no_cascade,
    )

    for name, old_rev, new_rev in bumped:
        click.echo(f"  {name}: cvc_revision {old_rev} → {new_rev}")
    click.echo(f"\n{len(bumped)} recipe(s) bumped.")


# ── cache subcommand group ──────────────────────────────────────


@cli.group("cache")
def cache_group() -> None:
    """Manage the local build cache."""


@cache_group.command("list")
@click.option(
    "--server", envvar="CVCPKG_SERVER_CACHE", default="", help="List entries from remote server."
)
@click.option(
    "--token", envvar="CVCPKG_SERVER_CACHE_TOKEN", default="", help="Bearer token for server."
)
@click.option("--name", default="", help="Filter by component name.")
@click.option("--platform-filter", "plat_filter", default="", help="Filter by platform.")
def cache_list_cmd(server: str, token: str, name: str, plat_filter: str) -> None:
    """List build cache entries (local or remote)."""
    if server:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        params: dict[str, str] = {"limit": "1000"}
        if name:
            params["name"] = name
        if plat_filter:
            params["platform"] = plat_filter
        url = f"{server.rstrip('/')}/v1/cache?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            click.echo(f"Server error: {e.code} {e.reason}", err=True)
            raise SystemExit(1)
        except (urllib.error.URLError, OSError) as e:
            click.echo(f"Connection error: {e}", err=True)
            raise SystemExit(1)

        pkgs = data.get("packages", [])
        if not pkgs:
            click.echo("Server cache is empty.")
            return
        total = data.get("total", len(pkgs))
        total_bytes = sum(p.get("size_bytes", 0) for p in pkgs)
        click.echo(f"{total} cached builds ({_human_size(total_bytes)}):\n")
        for p in pkgs:
            qname = f"{p.get('org', '')}/{p['name']}" if p.get("org") else p["name"]
            click.echo(
                f"  {qname} {p['version']}  "
                f"{p.get('platform', '')}/{p.get('arch', '')}/"
                f"{p.get('build_type', '')}/{p.get('link', '')}  "
                f"{_human_size(p.get('size_bytes', 0))}  "
                f"recipe={p.get('recipe_version', '')[:12]}…"
            )
        return

    from cvcpkg.build_cache import BuildCache

    bc = BuildCache()
    entries = bc.list_entries()
    if not entries:
        click.echo("Build cache is empty.")
        return
    total_size = bc.total_size_bytes()
    click.echo(f"{len(entries)} cached builds ({_human_size(total_size)}):\n")
    for e in entries:
        qname = f"{e.org}/{e.name}" if e.org else e.name
        click.echo(
            f"  {qname} {e.version}  "
            f"{e.platform}/{e.arch}/{e.config}/{e.link}  "
            f"{_human_size(e.archive_size_bytes)}  "
            f"{e.chain_hash[:12]}…  "
            f"stored={e.stored_at[:10]}"
        )


@cache_group.command("info")
@click.argument("chain_hash_val")
@_platform_opt
@_config_opt
@_link_opt
def cache_info_cmd(chain_hash_val: str, platform: str, config: str, link: str) -> None:
    """Show details for a specific cache entry by chain hash prefix."""
    from cvcpkg.build_cache import BuildCache

    plat = _auto_platform(platform)
    arch = _auto_arch(plat)
    bc = BuildCache()

    # Support prefix matching.
    entry = bc.info(chain_hash_val, plat, arch, config, link)
    if entry is None:
        # Try prefix match against all entries.
        for e in bc.list_entries():
            if e.chain_hash.startswith(chain_hash_val):
                entry = e
                break
    if entry is None:
        click.echo(f"No cache entry matching '{chain_hash_val}'.", err=True)
        raise SystemExit(1)
    click.echo(f"Name:        {entry.name}")
    click.echo(f"Version:     {entry.version}")
    click.echo(f"Chain hash:  {entry.chain_hash}")
    click.echo(f"Platform:    {entry.platform}")
    click.echo(f"Arch:        {entry.arch}")
    click.echo(f"Config:      {entry.config}")
    click.echo(f"Link:        {entry.link}")
    click.echo(f"Size:        {_human_size(entry.archive_size_bytes)}")
    click.echo(f"SHA-256:     {entry.archive_sha256}")
    click.echo(f"Stored:      {entry.stored_at}")
    click.echo(f"Last used:   {entry.last_used_at}")
    if entry.org:
        click.echo(f"Org:         {entry.org}")


@cache_group.command("remove")
@click.argument("chain_hash_val")
@_platform_opt
@_config_opt
@_link_opt
def cache_remove_cmd(chain_hash_val: str, platform: str, config: str, link: str) -> None:
    """Remove a specific cache entry by chain hash."""
    from cvcpkg.build_cache import BuildCache

    plat = _auto_platform(platform)
    arch = _auto_arch(plat)
    bc = BuildCache()
    if bc.evict(chain_hash_val, plat, arch, config, link):
        click.echo("Removed.")
    else:
        click.echo("Entry not found.", err=True)
        raise SystemExit(1)


@cache_group.command("purge")
@click.option(
    "--max-size",
    default=None,
    help="Maximum cache size (e.g. 10G, 500M).  Evicts oldest entries first.",
)
@click.option(
    "--max-age-days",
    default=None,
    type=int,
    help="Remove entries not used within this many days.",
)
@click.option("--all", "purge_all", is_flag=True, help="Remove all cache entries.")
@click.option(
    "--stale",
    is_flag=True,
    help="Remove entries whose chain_hash no longer matches any current recipe.",
)
@click.option(
    "--server", envvar="CVCPKG_SERVER_CACHE", default="", help="Purge from remote server (admin)."
)
@click.option(
    "--token", envvar="CVCPKG_SERVER_CACHE_TOKEN", default="", help="Bearer token for server."
)
def cache_purge_cmd(
    max_size: str | None,
    max_age_days: int | None,
    purge_all: bool,
    stale: bool,
    server: str,
    token: str,
) -> None:
    """Evict build cache entries by size, age, or staleness (local or remote)."""
    if server:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        if purge_all:
            # DELETE /v1/cache with no filters removes everything
            url = f"{server.rstrip('/')}/v1/cache"
            req = urllib.request.Request(url, method="DELETE")
        elif stale:
            hashes = _compute_current_chain_hashes()
            body = json.dumps({"valid_chain_hashes": sorted(hashes)}).encode()
            url = f"{server.rstrip('/')}/v1/cache/gc"
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
        elif max_age_days is not None:
            params = {"older_than": f"{max_age_days}d"}
            url = f"{server.rstrip('/')}/v1/cache?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, method="DELETE")
        elif max_size is not None:
            # Use the GC endpoint for size-based eviction
            body = json.dumps({"max_storage_bytes": _parse_size(max_size)}).encode()
            url = f"{server.rstrip('/')}/v1/cache/gc"
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
        else:
            click.echo("Specify --max-size, --max-age-days, --stale, or --all.", err=True)
            raise SystemExit(1)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            click.echo(f"Server error: {e.code} {e.reason}", err=True)
            raise SystemExit(1)
        except (urllib.error.URLError, OSError) as e:
            click.echo(f"Connection error: {e}", err=True)
            raise SystemExit(1)
        count = data.get("deleted_count", 0)
        click.echo(f"Removed {count} server cache entries.")
        for d in data.get("deleted", []):
            click.echo(f"  {d['name']}=={d['version']} ({d['size_bytes']} bytes)")
        return

    from cvcpkg.build_cache import BuildCache

    bc = BuildCache()
    if purge_all:
        removed = bc.purge(max_size_bytes=0)
        click.echo(f"Removed {removed} entries.")
        return
    if stale:
        hashes = _compute_current_chain_hashes()
        removed = bc.purge_stale(hashes)
        click.echo(f"Removed {removed} stale entries.")
        return
    if max_size is None and max_age_days is None:
        click.echo("Specify --max-size, --max-age-days, --stale, or --all.", err=True)
        raise SystemExit(1)

    size_bytes = _parse_size(max_size) if max_size else None
    age_secs = max_age_days * 86400.0 if max_age_days else None
    removed = bc.purge(max_size_bytes=size_bytes, max_age_seconds=age_secs)
    click.echo(f"Removed {removed} entries.")


@cache_group.command("server-stats")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_CACHE",
    required=True,
    help="Server URL.",
)
@click.option(
    "--token",
    envvar="CVCPKG_SERVER_CACHE_TOKEN",
    default="",
    help="Bearer token.",
)
def cache_server_stats_cmd(server: str, token: str) -> None:
    """Show storage statistics from the remote cache server."""
    import json
    import urllib.error
    import urllib.request

    url = f"{server.rstrip('/')}/v1/cache/stats"
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        click.echo(f"Server error: {e.code} {e.reason}", err=True)
        raise SystemExit(1)
    except (urllib.error.URLError, OSError) as e:
        click.echo(f"Connection error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"Total packages: {data['total_packages']}")
    click.echo(f"Total size:     {_human_size(data['total_size_bytes'])}")
    orgs = data.get("orgs", {})
    if orgs:
        click.echo("\nPer-organization:")
        for slug, info in sorted(orgs.items()):
            label = slug or "(no org)"
            click.echo(f"  {label}: {info['count']} packages, {_human_size(info['size_bytes'])}")


@cache_group.command("server-gc")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_CACHE",
    required=True,
    help="Server URL.",
)
@click.option(
    "--token",
    envvar="CVCPKG_SERVER_CACHE_TOKEN",
    default="",
    help="Bearer token (must be admin).",
)
@click.option(
    "--max-age-days",
    default=None,
    type=int,
    help="Delete non-release packages older than this many days.",
)
@click.option(
    "--max-size",
    default=None,
    help="Maximum total storage (e.g. 10G). Evicts oldest non-release packages.",
)
def cache_server_gc_cmd(
    server: str,
    token: str,
    max_age_days: int | None,
    max_size: str | None,
) -> None:
    """Run garbage collection on the remote cache server (admin-only)."""
    import json
    import urllib.error
    import urllib.request

    if max_age_days is None and max_size is None:
        click.echo("Specify --max-age-days and/or --max-size.", err=True)
        raise SystemExit(1)

    body: dict = {}
    if max_age_days is not None:
        body["max_age_seconds"] = max_age_days * 86400.0
    if max_size is not None:
        body["max_storage_bytes"] = _parse_size(max_size)

    url = f"{server.rstrip('/')}/v1/cache/gc"
    payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        click.echo(f"Server error: {e.code} {e.reason}", err=True)
        raise SystemExit(1)
    except (urllib.error.URLError, OSError) as e:
        click.echo(f"Connection error: {e}", err=True)
        raise SystemExit(1)

    count = data.get("deleted_count", 0)
    click.echo(f"GC removed {count} package(s).")
    for d in data.get("deleted", []):
        click.echo(f"  {d['name']}=={d['version']} ({d['size_bytes']} bytes)")


def _auto_arch(platform: str) -> str:
    """Resolve architecture for a given platform."""
    if platform == "wasm":
        return "wasm32"
    from cvcpkg.platform import detect_arch

    return detect_arch()


def _human_size(n: int) -> str:
    """Format bytes as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _parse_size(s: str) -> int:
    """Parse a human-readable size string (e.g. '10G') to bytes."""
    s = s.strip().upper()
    multipliers = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * mult)
    return int(s)


def _compute_current_chain_hashes() -> set[str]:
    """Compute chain_hash for every current recipe on each platform.

    Returns the set of all valid chain hashes so that entries whose
    chain_hash is *not* in this set can be considered stale.
    """
    from cvcpkg.builder import chain_hash, find_recipes_dir, list_recipes

    recipes_dir = find_recipes_dir()
    recipes = list_recipes(recipes_dir)
    by_name = {r.name: r for r in recipes}

    # Compute for all platforms referenced by the recipes' build matrices.
    platforms: set[str] = set()
    for r in recipes:
        for me in r.build_matrix:
            platforms.add(me.platform)
    if not platforms:
        platforms = {"linux", "darwin", "windows", "freebsd", "wasm"}

    hashes: set[str] = set()
    for plat in platforms:
        for r in recipes:
            h = chain_hash(r, by_name, plat)
            if h:
                hashes.add(h)
    return hashes


# ── main() wrapper for backward compat with tests ──────────────


def main(argv: list[str] | None = None) -> int:
    """Entry point for programmatic invocation and tests.

    Wraps the Click CLI group so callers get a clean integer return
    code instead of SystemExit.  All CvcpkgError and ClickException
    errors are caught, printed, and mapped to exit code 1.

    Args:
        argv: Command-line arguments, e.g. ``["install", "--from", "req.yaml"]``.
              Defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        0 on success, non-zero on error.
    """
    try:
        cli(args=argv, standalone_mode=False)
        return 0
    except click.exceptions.Exit as e:
        return e.code
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except click.ClickException as e:
        e.show()
        return 1
    except CvcpkgError as e:
        click.echo(f"cvcpkg: ERROR: {e}", err=True)
        return 1


if __name__ == "__main__":
    cli()
