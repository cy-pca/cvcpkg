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

_VALID_PLATFORMS = ["auto", "linux", "macos", "windows"]
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
    type=click.Path(exists=True),
    default=None,
    help="Path to recipes/ directory.  Auto-detected from repo root if omitted.",
)


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
    "--ignore-abi",
    is_flag=True,
    help="Skip ABI compatibility checks (C++ standard, runtime, etc.).",
)
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
    ignore_abi: bool,
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
    from cvcpkg.catalog import catalog_entries, fetch_catalog, load_catalog_from_file
    from cvcpkg.installer import install_entry
    from cvcpkg.lockfile import LockEntry, Lockfile
    from cvcpkg.manifest import ComponentReq, Requirements
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
    catalog_url = catalog or ""
    if catalog_url and Path(catalog_url).is_file():
        cat = load_catalog_from_file(catalog_url)
    else:
        cat = fetch_catalog(catalog_url, cache_dir=default_cache_dir())

    entries = catalog_entries(cat, platform=plat, arch=arc, build_type=reqs.config, link=reqs.link)
    if not entries:
        raise click.ClickException("no bundles found in catalog for this platform tuple.")

    # Group candidate entries by component name for the resolver.
    candidates: dict[str, list] = {}
    for e in entries:
        candidates.setdefault(e.name, []).append(e)

    from cvcpkg.resolver import resolve

    result = resolve(reqs.components, candidates)
    picked = result.picked

    click.echo(f"cvcpkg: resolved {len(picked)} component(s):")
    for name in sorted(picked):
        click.echo(f"  {name} == {picked[name].version}")

    # ── Download and extract each resolved bundle ──
    cache_dir = default_cache_dir()
    lock_entries: list[LockEntry] = []
    for name in sorted(picked):
        entry = picked[name]
        click.echo(f"cvcpkg: installing {name} {entry.version} ...")
        install_entry(entry, prefix_path, cache_dir)
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
                seen.setdefault(e.name, []).append(e.version)
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
    matches = [e for e in entries if e.name == component]
    if not matches:
        raise click.ClickException(f"component '{component}' not found in catalog.")

    from cvcpkg.semver import Version

    matches.sort(key=lambda e: Version.parse(e.version), reverse=True)
    latest = matches[0]

    click.echo(f"Name:             {latest.name}")
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


# ── Helper: resolve recipe dir ──────────────────────────────────


def _resolve_recipe_dir(name: str) -> Path:
    """Resolve a recipe name or path to its directory.

    Accepts either a path to a directory containing recipe.yaml, or
    a bare recipe name (e.g. "grpc") which is looked up relative to
    the auto-detected recipes/ directory.
    """
    p = Path(name)
    if p.is_dir() and (p / "recipe.yaml").is_file():
        return p.resolve()
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
def build(
    recipe: tuple[str, ...],
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    keep_build_dir: bool,
) -> None:
    """Build one or more recipes from source.

    Downloads the upstream source (or uses vendored sources), applies
    patches, and runs the recipe's platform-specific build script.
    Results are installed into --prefix.

    \b
    Examples:
      cvcpkg build zlib --prefix ./prefix
      cvcpkg build grpc protobuf --config debug --link static
    """
    from cvcpkg.builder import build_recipe

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve() if prefix else None

    for name in recipe:
        recipe_dir = _resolve_recipe_dir(name)
        build_recipe(
            recipe_dir,
            platform=plat,
            config=config,
            link=link,
            prefix=prefix_path,
            keep_build_dir=keep_build_dir,
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
def pack(
    recipe: tuple[str, ...],
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    output_dir: str,
    keep_build_dir: bool,
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
        recipe_dir = _resolve_recipe_dir(name)
        archive, sha, size = pack_recipe(
            recipe_dir,
            platform=plat,
            config=config,
            link=link,
            prefix=prefix_path,
            output_dir=output,
            keep_build_dir=keep_build_dir,
        )
        click.echo(f"  {archive} ({size:,} bytes, sha256={sha})")


# ── build-all ───────────────────────────────────────────────────


@cli.command("build-all")
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Shared install prefix.")
@_keep_build_opt
@_recipes_dir_opt
def build_all_cmd(
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    keep_build_dir: bool,
    recipes_dir: str | None,
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
    rdir = Path(recipes_dir) if recipes_dir else find_recipes_dir()

    build_all(
        rdir,
        platform=plat,
        config=config,
        link=link,
        prefix=prefix_path,
        keep_build_dir=keep_build_dir,
    )


# ── pack-all ────────────────────────────────────────────────────


@cli.command("pack-all")
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Shared install prefix.")
@click.option("--output-dir", type=click.Path(), default="./dist", help="Output directory.")
@_keep_build_opt
@_recipes_dir_opt
def pack_all_cmd(
    platform: str,
    config: str,
    link: str,
    prefix: str | None,
    output_dir: str,
    keep_build_dir: bool,
    recipes_dir: str | None,
) -> None:
    """Build and archive all recipes.

    Combines build-all with per-component packaging.  Each recipe is
    built into a shared prefix, then staged and archived individually
    into --output-dir as .tar.gz files ready for catalog publishing.

    \b
    Example:
      cvcpkg pack-all --platform linux --config release --link shared \\
          --output-dir ./dist --recipes-dir recipes
    """
    from cvcpkg.builder import (
        build_all,
        create_archive,
        find_recipes_dir,
        generate_manifest,
        stage_bundle,
    )
    from cvcpkg.platform import detect_arch

    plat = _auto_platform(platform)
    arch = detect_arch()
    prefix_path = Path(prefix).resolve() if prefix else None
    output = Path(output_dir).resolve()
    rdir = Path(recipes_dir) if recipes_dir else find_recipes_dir()

    contexts = build_all(
        rdir,
        platform=plat,
        config=config,
        link=link,
        prefix=prefix_path,
        keep_build_dir=keep_build_dir,
    )

    output.mkdir(parents=True, exist_ok=True)
    for ctx in contexts:
        manifest = generate_manifest(
            ctx.recipe,
            ctx.install_dir,
            plat,
            arch,
            config,
            link,
        )
        staging = ctx.work_dir / "staging"
        staging.mkdir(exist_ok=True)
        stage_bundle(ctx.install_dir, manifest, staging)
        archive_path, sha256, size = create_archive(
            staging,
            output,
            ctx.recipe.name,
            ctx.recipe.full_version,
            plat,
            arch,
            config,
            link,
        )
        click.echo(f"  {archive_path.name} ({size:,} bytes, sha256={sha256})")


# ── recipes ─────────────────────────────────────────────────────


@cli.command()
@click.option("--list", "mode", flag_value="list", default=True, help="List all recipes.")
@click.option("--show", "show_name", metavar="NAME", help="Show details of a recipe.")
@click.option("--validate", "mode", flag_value="validate", help="Validate all recipes.")
def recipes(mode: str, show_name: str | None) -> None:
    """List or inspect recipes.

    \b
    Examples:
      cvcpkg recipes               # list all recipes
      cvcpkg recipes --show grpc   # show details of a recipe
      cvcpkg recipes --validate    # validate all recipe.yaml files
    """
    from cvcpkg.builder import Recipe, find_recipes_dir, list_recipes

    if show_name:
        rd = find_recipes_dir()
        recipe_dir = rd / show_name
        if not (recipe_dir / "recipe.yaml").is_file():
            raise click.ClickException(f"recipe '{show_name}' not found.")
        recipe = Recipe.load(recipe_dir)
        click.echo(f"Name:     {recipe.name}")
        click.echo(f"Version:  {recipe.full_version}")
        click.echo(f"Source:   {recipe.source.type}")
        if recipe.source.url:
            click.echo(f"URL:      {recipe.source.url}")
        platforms = [m.platform for m in recipe.build_matrix]
        click.echo(f"Platforms: {', '.join(platforms)}")
        deps = recipe.raw.get("depends", {}).get("build", [])
        if deps:
            dep_names = []
            for d in deps:
                if isinstance(d, str):
                    dep_names.append(d)
                else:
                    label = d.get("name", "?")
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
    all_recipes = list_recipes()
    if not all_recipes:
        raise click.ClickException("no recipes found.")
    click.echo(f"{'Name':<20} {'Version':<18} {'Platforms'}")
    click.echo("-" * 60)
    for r in all_recipes:
        platforms = ", ".join(m.platform for m in r.build_matrix)
        click.echo(f"{r.name:<20} {r.full_version:<18} {platforms}")


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
