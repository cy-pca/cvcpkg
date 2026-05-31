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

import json
import os
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

_VALID_PLATFORMS = [
    "auto",
    "any",
    "linux",
    "macos",
    "windows",
    "wasm",
    "freebsd",
    "openbsd",
    "netbsd",
]
_VALID_ARCHES = [
    "auto",
    "x86_64",
    "arm64",
    "riscv64",
    "ppc64le",
    "ppc64",
    "s390x",
    "noarch",
    "wasm32",
]

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
    help="Link mode -- shared (.so/.dylib/.dll) or static (.a/.lib).",
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
        catalog_entries,
        fetch_catalog,
        load_catalog_from_file,
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
        # Catalog was unreachable -- all requested components must be
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

    # Fetch mirror list for failover downloads.
    server_url = os.environ.get("CVCPKG_SERVER_URL", "")
    mirror_urls: list[str] = []
    if server_url:
        mirror_urls = _fetch_mirror_urls(server_url, os.environ.get("CVCPKG_TOKEN"))

    for name in sorted(picked):
        entry = picked[name]
        # Inject mirror download URLs as fallbacks.
        if mirror_urls and entry.archive_url:
            fname = entry.archive_url.rsplit("/", 1)[-1]
            for murl in mirror_urls:
                fallback = f"{murl.rstrip('/')}/v1/mirror/download/{fname}"
                if fallback not in entry.mirror_urls:
                    entry.mirror_urls.append(fallback)
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
    click.echo(f"cvcpkg: done -- {len(picked)} component(s) installed to {prefix_path}")


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
            raise click.ClickException("no lockfile found -- prefix may not be managed by cvcpkg.")
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
            "cannot find packaging/validate.py -- run from the libcvc-deps repo root."
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
            click.echo(f"  MISSING  {entry.name} -- no manifest.yaml")
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

    # Fetch mirror list for failover downloads.
    server_url = os.environ.get("CVCPKG_SERVER_URL", "")
    mirror_urls: list[str] = []
    if server_url:
        mirror_urls = _fetch_mirror_urls(server_url, os.environ.get("CVCPKG_TOKEN"))

    for entry in lock.bundles:
        manifest_path = prefix_path / "share" / "libcvc-deps" / entry.name / "manifest.yaml"
        if manifest_path.exists():
            continue
        if not entry.archive_url:
            raise click.ClickException(f"cannot sync {entry.name} -- no archive_url in lockfile.")
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
        # Inject mirror download URLs as fallbacks.
        if mirror_urls and cat_entry.archive_url:
            fname = cat_entry.archive_url.rsplit("/", 1)[-1]
            for murl in mirror_urls:
                fallback = f"{murl.rstrip('/')}/v1/mirror/download/{fname}"
                if fallback not in cat_entry.mirror_urls:
                    cat_entry.mirror_urls.append(fallback)
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
        click.echo(f"cvcpkg: catalog refreshed -- revision {rev}, {n} bundle(s).")
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


@cli.command("catalog-generate")
@click.option(
    "--indexes-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory containing per-platform *-index.yaml files.",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write catalog files to.",
)
@click.option(
    "--release-tag",
    required=True,
    help="Release tag (e.g. v1.2.0).",
)
@click.option(
    "--server-url",
    default="https://pkg.tx.wtf",
    show_default=True,
    help="cvcpkg server URL for archive download URLs.",
)
@click.option(
    "--base-revision",
    default=0,
    type=int,
    help="Previous catalog revision number to increment from.",
)
def catalog_generate(
    indexes_dir: Path,
    output_dir: Path,
    release_tag: str,
    server_url: str,
    base_revision: int,
) -> None:
    """Generate a unified catalog from per-platform index files.

    Merges all *-index.yaml files in INDEXES_DIR into a catalog with
    download URLs pointing to SERVER_URL.  Writes latest.yaml,
    <revision>.yaml, index.yaml, and <tag>-index.yaml to OUTPUT_DIR.

    \b
    Examples:
      cvcpkg catalog-generate \\
        --indexes-dir ./indexes \\
        --output-dir ./catalog-output \\
        --release-tag v1.2.0
    """
    from cvcpkg.catalog import generate_catalog

    cat = generate_catalog(
        indexes_dir,
        output_dir,
        release_tag=release_tag,
        server_url=server_url,
        base_revision=base_revision,
    )
    rev = cat.get("revision", "?")
    n = len(cat.get("bundles", []))
    click.echo(f"cvcpkg: catalog revision {rev} generated -- {n} bundle(s).")
    click.echo(f"cvcpkg: output written to {output_dir}/")


# ── gc ──────────────────────────────────────────────────────────


@cli.command()
def gc() -> None:
    """Prune the local download cache.

    Removes downloaded archives from ~/.cache/cvcpkg/ that are no
    longer referenced by any installed prefix.  Safe to run at
    any time -- bundles will be re-downloaded if needed.
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.cache import gc as run_gc

    cache_dir = default_cache_dir()
    if not cache_dir.is_dir():
        click.echo("cvcpkg: cache is empty.")
        return
    removed = run_gc(cache_dir, set())
    click.echo(f"cvcpkg: pruned {removed} cached archive(s).")


# ── clean ───────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--work-dir",
    type=click.Path(exists=True),
    default=None,
    envvar="CVCPKG_WORK_DIR",
    help="Directory to scan for stale work directories.  "
    "Defaults to the system temp directory ($TMPDIR / /tmp).  "
    "Pass the same value used with 'build-all --work-dir'.",
)
@click.option(
    "--older-than",
    default=120,
    type=int,
    show_default=True,
    help="Only remove directories older than this many minutes.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List stale directories without removing them.",
)
@click.option(
    "--all",
    "remove_all",
    is_flag=True,
    help="Remove all cvcpkg work directories regardless of age.",
)
def clean(
    work_dir: str | None,
    older_than: int,
    dry_run: bool,
    remove_all: bool,
) -> None:
    """Remove leftover cvcpkg build work directories.

    Scans the temp directory (or --work-dir) for orphaned cvcpkg-*
    directories left behind by interrupted or crashed builds and
    removes them.

    By default only directories older than 120 minutes are removed.
    Use --all to remove everything, or --older-than to adjust the
    age threshold.

    \b
    Examples:
      cvcpkg clean                          # clean /tmp, dirs >2h old
      cvcpkg clean --dry-run                # preview what would be removed
      cvcpkg clean --work-dir /scratch      # clean a custom work dir
      cvcpkg clean --all                    # remove all cvcpkg work dirs
      cvcpkg clean --older-than 30          # remove dirs older than 30 min
    """
    import shutil
    import tempfile
    import time

    scan_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir())

    if not scan_dir.is_dir():
        click.echo(f"cvcpkg: directory does not exist: {scan_dir}")
        return

    now = time.time()
    cutoff = now + 1 if remove_all else now - older_than * 60
    removed = 0
    total_bytes = 0

    candidates = sorted(scan_dir.iterdir())
    for entry in candidates:
        if not entry.is_dir() or not entry.name.startswith("cvcpkg-"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        age_min = int((now - mtime) / 60)
        try:
            dir_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        except OSError:
            dir_size = 0
        total_bytes += dir_size
        if dry_run:
            click.echo(f"  [dry-run] {entry.name}  ({_human_size(dir_size)}, {age_min}m old)")
        else:
            shutil.rmtree(entry, ignore_errors=True)
            click.echo(f"  removed {entry.name}  ({_human_size(dir_size)}, {age_min}m old)")
        removed += 1

    if removed == 0:
        click.echo(f"cvcpkg: no stale work directories in {scan_dir}")
    else:
        verb = "would remove" if dry_run else "removed"
        click.echo(
            f"\ncvcpkg: {verb} {removed} director{'y' if removed == 1 else 'ies'}"
            f" ({_human_size(total_bytes)})"
        )


# ── download ────────────────────────────────────────────────────


@cli.command()
@click.argument("components", nargs=-1, required=True)
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
    "--output-dir",
    "-o",
    type=click.Path(),
    default=".",
    help="Directory to save downloaded archives to.",
)
@click.option(
    "--catalog",
    default=None,
    metavar="URL_OR_FILE",
    help="Catalog URL or local YAML file.",
)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default=None,
    metavar="URL",
    help="cvcpkg-server URL.  If set, mirrors are fetched and used as fallbacks.",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    default=None,
    help="Bearer token for the server.  [env: CVCPKG_TOKEN]",
)
def download(
    components: tuple[str, ...],
    platform: str,
    arch: str,
    config: str,
    link: str,
    output_dir: str,
    catalog: str | None,
    server: str | None,
    token: str | None,
) -> None:
    """Download component archives without extracting them.

    Fetches prebuilt bundle archives from the catalog and saves them
    into OUTPUT_DIR.  Unlike 'install', archives are not extracted
    into a prefix -- they are kept as-is for redistribution, caching,
    or manual inspection.

    When --server is provided, the client also queries the server's
    mirror list and uses healthy mirrors as fallback download sources.

    \b
    Examples:
      cvcpkg download zlib boost --output-dir ./archives
      cvcpkg download zlib==1.3.1+cvc.1 -o ./dist --config debug
      cvcpkg download zlib --server https://pkg.tx.wtf -o ./dist
    """
    import shutil

    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import catalog_entries, fetch_catalog, load_catalog_from_file
    from cvcpkg.installer import download_bundle
    from cvcpkg.manifest import CatalogEntry, ComponentReq
    from cvcpkg.platform import detect_arch, detect_platform

    plat = platform if platform != "auto" else detect_platform()
    arc = arch if arch != "auto" else detect_arch()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    click.echo(f"cvcpkg: resolving for {plat}/{arc}/{config}/{link}")

    # Parse component specs (name or name==version).
    reqs: list[ComponentReq] = []
    for c in components:
        if "==" in c:
            name, ver = c.split("==", 1)
            reqs.append(ComponentReq(name=name, version=f"=={ver}"))
        else:
            reqs.append(ComponentReq(name=c))

    # Fetch catalog.
    catalog_url = catalog or ""
    try:
        if catalog_url and Path(catalog_url).is_file():
            cat = load_catalog_from_file(catalog_url)
        else:
            cat = fetch_catalog(catalog_url, cache_dir=default_cache_dir())
    except Exception as exc:
        raise click.ClickException(f"failed to fetch catalog: {exc}") from exc

    entries = catalog_entries(cat, platform=plat, arch=arc, build_type=config, link=link)
    candidates: dict[str, list[CatalogEntry]] = {}
    for e in entries:
        candidates.setdefault(e.name, []).append(e)

    if not entries:
        raise click.ClickException("no bundles found in catalog for this platform tuple.")

    from cvcpkg.resolver import resolve

    result = resolve(reqs, candidates)
    picked = result.picked

    if not picked:
        raise click.ClickException("resolver found no matching bundles.")

    # Fetch mirror list from server for failover URLs.
    mirror_urls: list[str] = []
    if server:
        mirror_urls = _fetch_mirror_urls(server, token)

    # Download each resolved bundle.
    cache_dir = default_cache_dir()
    for name in sorted(picked):
        entry = picked[name]
        # Inject mirror download URLs as fallbacks.
        if mirror_urls and entry.archive_url:
            filename = entry.archive_url.rsplit("/", 1)[-1]
            for murl in mirror_urls:
                fallback = f"{murl.rstrip('/')}/v1/mirror/download/{filename}"
                if fallback not in entry.mirror_urls:
                    entry.mirror_urls.append(fallback)

        click.echo(f"cvcpkg: downloading {name} {entry.version} ...")
        archive = download_bundle(entry, cache_dir)
        dest = out / archive.name
        shutil.copy2(archive, dest)
        click.echo(f"  -> {dest} ({dest.stat().st_size:,} bytes)")

    click.echo(f"cvcpkg: downloaded {len(picked)} archive(s) to {out}")


def _fetch_mirror_urls(server: str, token: str | None) -> list[str]:
    """Fetch the list of healthy mirror URLs from a cvcpkg-server."""
    import logging
    import urllib.request

    log = logging.getLogger("cvcpkg")
    base = server.rstrip("/")
    url = f"{base}/v1/mirrors"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        import json

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        mirrors = data.get("mirrors", [])
        urls = [m["url"] for m in mirrors if m.get("healthy")]
        if urls:
            log.debug("fetched %d healthy mirror(s) from %s", len(urls), base)
        return urls
    except Exception as exc:
        log.debug("failed to fetch mirrors from %s: %s", base, exc)
        return []


# ── publish ─────────────────────────────────────────────────────


@cli.command()
@click.argument("packages", nargs=-1, required=False)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default="",
    metavar="URL",
    help="cvcpkg-server URL (e.g. https://pkg.tx.wtf).  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    default="",
    help="Bearer token with publisher or admin role.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--dest",
    default="",
    metavar="URI",
    help="Storage backend URI (e.g. s3://bucket/prefix, sftp://host/path, file:///local).",
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
@click.option(
    "--output-dir",
    type=click.Path(),
    default="./dist",
    help="Directory containing built archives (used when publishing by recipe name).",
)
@_platform_opt
@_config_opt
@_link_opt
@click.option(
    "--all",
    "publish_all",
    is_flag=True,
    default=False,
    help="Publish all archives in --output-dir matching the platform tuple.",
)
def publish(
    packages: tuple[str, ...],
    server: str,
    token: str,
    dest: str,
    release_tag: str,
    chunked_threshold: int,
    org: str,
    output_dir: str,
    platform: str,
    config: str,
    link: str,
    publish_all: bool,
) -> None:
    """Publish bundle archive(s) to a cvcpkg-server or storage backend.

    PACKAGES can be recipe names (e.g. ``zlib``, ``grpc``) or paths to
    archive files.  When a recipe name is given, cvcpkg looks for the
    matching archive in --output-dir using the current --platform,
    --config, and --link settings.

    Use --all to publish every archive in --output-dir that matches
    the platform tuple, without listing recipe names individually.

    Passing archive file paths directly still works but is deprecated
    and will be removed in a future release.

    **Server mode** (``--server``): reads the embedded manifest.yaml
    from each archive to extract component metadata, then uploads to
    the cvcpkg-server REST API.  Small archives (< 10 MB) upload in
    a single request; larger archives use chunked upload with resume.

    **Storage-backend mode** (``--dest``): uploads archive files to a
    storage backend (S3, SFTP, local directory, etc.) using the
    pluggable storage layer.

    Exactly one of ``--server`` or ``--dest`` must be provided.

    Archives are produced by ``cvcpkg pack`` or ``cvcpkg pack-all``.

    \b
    Examples:
      # Publish to cvcpkg-server by recipe name (recommended):
      cvcpkg publish zlib grpc --server https://pkg.tx.wtf --token cvctok_...
      # Publish all recipes found in dist/ to the server:
      cvcpkg publish --all --server https://pkg.tx.wtf --token cvctok_...
      # Publish to an S3 bucket:
      cvcpkg publish --all --dest s3://my-bucket/cvcpkg/
      # Publish to a local directory:
      cvcpkg publish dist/*.tar.gz --dest file:///shared/repo/
    """
    if not server and not dest:
        raise click.UsageError("provide --server (or set CVCPKG_SERVER_URL) or --dest.")
    if server and dest:
        raise click.UsageError("--server and --dest are mutually exclusive.")
    if server and not token:
        raise click.UsageError(
            "--token is required when publishing to a server " "(or set CVCPKG_TOKEN)."
        )
    if not packages and not publish_all:
        raise click.UsageError("provide recipe names, archive paths, or use --all.")

    from cvcpkg.platform import detect_arch

    plat = _auto_platform(platform)
    arc = detect_arch()

    # Resolve each package argument to archive file path(s).
    if publish_all:
        archive_paths = _resolve_all_archives(output_dir, plat, arc, config, link)
        if not archive_paths:
            raise click.ClickException(
                f"no archives found in {Path(output_dir).resolve()} "
                f"for {plat}/{arc}/{config}/{link}"
            )
    else:
        archive_paths = _resolve_publish_archives(packages, output_dir, plat, arc, config, link)

    if dest:
        _publish_to_backend(dest, archive_paths)
    else:
        _publish_to_server(
            server,
            token,
            archive_paths,
            release_tag,
            chunked_threshold,
            org,
        )


def _publish_to_backend(dest: str, archive_paths: list[Path]) -> None:
    """Upload archives to a storage backend (S3, SFTP, file, etc.)."""
    from cvcpkg.storage import get_backend

    backend = get_backend(dest)
    for p in archive_paths:
        if not p.is_file():
            raise click.ClickException(f"file not found: {p}")
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
    click.echo(f"cvcpkg: published {len(archive_paths)} archive(s) to {dest}.")


def _publish_to_server(
    server: str,
    token: str,
    archive_paths: list[Path],
    release_tag: str,
    chunked_threshold: int,
    org: str,
) -> None:
    """Upload archives to a cvcpkg-server via its REST API."""
    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    ok = 0
    failed: list[str] = []

    for p in archive_paths:
        manifest = _extract_manifest(p)
        bundle = manifest.get("bundle", {})
        name = bundle.get("name", "")
        version = bundle.get("version", "")
        plat = bundle.get("platform", "")
        arch = bundle.get("arch", "")
        build_type = bundle.get("build_type", bundle.get("config", "release"))
        link = bundle.get("link", "shared")
        recipe_version = manifest.get("meta", {}).get("recipe_sha256", "")
        meta = manifest.get("meta", {})
        manifest_org = bundle.get("org", "")

        # Extract runtime deps from manifest.
        deps_block = manifest.get("dependencies", {})
        if isinstance(deps_block, dict):
            required_deps = deps_block.get("required", [])
        else:
            required_deps = []
        # Fallback: legacy flat "depends" list.
        if not required_deps:
            legacy = manifest.get("depends", [])
            if isinstance(legacy, list):
                required_deps = legacy

        if not name or not version:
            raise click.ClickException(f"{p.name}: manifest missing name or version")

        file_size = p.stat().st_size
        display_name = f"{org or manifest_org}/{name}" if (org or manifest_org) else name
        label = f"{display_name}=={version} ({plat}/{arch}/{build_type}/{link})"

        if _variant_exists(base, headers, name, version, plat, arch, build_type, link):
            click.echo(f"cvcpkg: skipping {label} (already on server)")
            continue

        click.echo(f"cvcpkg: publishing {label} [{file_size / 1024 / 1024:.1f} MB] -> {base}")

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
            "required_deps": json.dumps(required_deps),
        }

        try:
            if file_size <= chunked_threshold:
                result = _publish_simple(base, headers, params, p)
            else:
                result = _publish_chunked(base, headers, params, p, file_size)

            if result == "published":
                ok += 1
        except click.ClickException as exc:
            click.echo(f"  ERROR: {exc.format_message()}", err=True)
            failed.append(label)

    click.echo(f"cvcpkg: published {ok}/{len(archive_paths)} archive(s).")
    if failed:
        click.echo(f"cvcpkg: {len(failed)} archive(s) failed:", err=True)
        for f in failed:
            click.echo(f"  - {f}", err=True)
        raise click.ClickException(f"publish completed with {len(failed)} error(s)")


def _resolve_publish_archives(
    packages: tuple[str, ...],
    output_dir: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
) -> list[Path]:
    """Resolve package arguments to archive file paths.

    Each argument is either:
    - A file path to an existing archive (deprecated, emits a warning)
    - A recipe name, resolved by globbing the output directory for
      ``{name}-*-{platform}-{arch}-{config}-{link}.*``
    """
    import warnings

    dist = Path(output_dir).resolve()
    result: list[Path] = []

    for pkg in packages:
        p = Path(pkg)
        if p.is_file():
            warnings.warn(
                f"Passing archive file paths to 'cvcpkg publish' is deprecated. "
                f"Use recipe names instead (e.g. 'cvcpkg publish {p.stem.split('-')[0]}').",
                DeprecationWarning,
                stacklevel=2,
            )
            result.append(p.resolve())
            continue

        # Treat as a recipe name — search output_dir for matching archives.
        if not dist.is_dir():
            raise click.ClickException(
                f"output directory does not exist: {dist}\n"
                f"  Run 'cvcpkg pack-all' first, or pass --output-dir."
            )

        pattern = f"{pkg}-*-{platform}-{arch}-{config}-{link}.*"
        matches = sorted(dist.glob(pattern))
        # Filter out signature files.
        matches = [m for m in matches if not m.name.endswith(".sig")]
        if not matches:
            raise click.ClickException(
                f"no archive found for recipe '{pkg}' in {dist}\n"
                f"  Expected pattern: {pattern}\n"
                f"  Check --output-dir, --platform, --config, --link."
            )
        if len(matches) > 1:
            # Multiple versions — take the latest (last alphabetically).
            click.echo(f"cvcpkg: multiple archives for '{pkg}', using {matches[-1].name}", err=True)
        result.append(matches[-1])

    return result


def _resolve_all_archives(
    output_dir: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
) -> list[Path]:
    """Find all archives in *output_dir* matching the platform tuple."""
    dist = Path(output_dir).resolve()
    if not dist.is_dir():
        return []
    pattern = f"*-{platform}-{arch}-{config}-{link}.*"
    matches = sorted(dist.glob(pattern))
    return [m for m in matches if m.is_file() and not m.name.endswith(".sig")]


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
            f"{archive_path.name}: no manifest.yaml found -- is this a cvcpkg archive?"
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
                        # Offset mismatch -- check server status and resume
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
                            f"{exc} -- retrying in {wait}s"
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

    click.echo(f"cvcpkg: world build complete -- {len(order)} recipe(s) built to {prefix_path}")


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
        from cvcpkg.builder import (
            _collect_host_tools,
            list_recipes,
            load_all_recipes,
        )

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
        from cvcpkg.platform import detect_platform

        host_plat = host_platform or detect_platform()
        target_recipes = [
            r
            for r in available
            if any(m.platform == plat or m.platform == "any" for m in r.build_matrix)
        ]
        host_tool_recipes = _collect_host_tools(target_recipes, all_recipes, plat, host_plat)

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

        # Collect cross-toolchain env from host-tool recipes.
        merged_toolchain_env: dict[str, str] = {}
        for r in host_tool_recipes:
            merged_toolchain_env.update(r.cross_toolchain_env)

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
                host_platform=host_plat,
                cross_toolchain_env=merged_toolchain_env,
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
            click.echo(f"  Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}...)")


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

    import shutil

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
        except (ValueError, TypeError) as exc:
            raise click.BadParameter(
                f"Invalid shard format '{shard}'. Expected INDEX/TOTAL (e.g. 0/3)."
            ) from exc

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
        cleanup_work_dirs=False,
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
            click.echo(f"  Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}...)")

        # Clean up per-component work directory now that the archive
        # has been created (build_all was called with
        # cleanup_work_dirs=False so the caller manages lifetime).
        if not keep_build_dir and ctx.work_dir != ctx.prefix and ctx.work_dir.is_dir():
            shutil.rmtree(ctx.work_dir, ignore_errors=True)

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
        click.echo(f"  {ki.label:<20} {ki.fingerprint[:16]}...  ({kind})")


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
    click.echo(f"Imported '{info.label}' ({info.fingerprint[:16]}...)")


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
    click.echo(f"Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}...)")


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
    click.echo(f"Verified: signed by '{ki.label}' ({ki.fingerprint[:16]}...)")


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
            raise SystemExit(1) from e
        except (urllib.error.URLError, OSError) as e:
            click.echo(f"Connection error: {e}", err=True)
            raise SystemExit(1) from e

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
                f"recipe={p.get('recipe_version', '')[:12]}..."
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
            f"{e.chain_hash[:12]}...  "
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
            raise SystemExit(1) from e
        except (urllib.error.URLError, OSError) as e:
            click.echo(f"Connection error: {e}", err=True)
            raise SystemExit(1) from e
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
        raise SystemExit(1) from e
    except (urllib.error.URLError, OSError) as e:
        click.echo(f"Connection error: {e}", err=True)
        raise SystemExit(1) from e

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
        raise SystemExit(1) from e
    except (urllib.error.URLError, OSError) as e:
        click.echo(f"Connection error: {e}", err=True)
        raise SystemExit(1) from e

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


# ── remote token management (client → server API) ──────────────


def _api_request(method: str, url: str, token: str, **kwargs):
    """Make an authenticated HTTP request; return parsed JSON or raise."""
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = getattr(client, method)(url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    return resp.json()


@cli.group("token")
def token_group() -> None:
    """Manage server API tokens (requires admin token).

    These commands talk to the running cvcpkg-server via its REST API,
    so mutations go through the same code path as normal requests —
    no direct database access, no race conditions.
    """


@token_group.command("create")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Name for the new token.")
@click.option(
    "--role",
    required=True,
    type=click.Choice(["reader", "publisher", "admin"]),
    help="Role to assign.",
)
@click.option("--expires-in-days", type=int, default=None, help="Optional expiry in days.")
def token_create(server: str, token: str, name: str, role: str, expires_in_days: int | None):
    """Create a new API token on the server."""
    body: dict = {"name": name, "role": role}
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    data = _api_request("post", f"{server.rstrip('/')}/v1/tokens", token, json=body)
    click.echo(f"Created token '{data['name']}' (role: {data['role']})")
    click.echo(f"  Token: {data['token']}")
    click.echo("  ⚠ Store this token securely — it will not be shown again.")
    if data.get("expires_at"):
        click.echo(f"  Expires: {data['expires_at']}")


@token_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
def token_list(server: str, token: str):
    """List all API tokens on the server."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/tokens", token)
    tokens = data.get("tokens", [])
    if not tokens:
        click.echo("No tokens found.")
        return
    for t in tokens:
        status = " [REVOKED]" if t.get("revoked") else ""
        expires = f"  expires={t['expires_at']}" if t.get("expires_at") else ""
        click.echo(f"  {t['name']:<24} role={t['role']:<12}{expires}{status}")


@token_group.command("revoke")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Name of the token to revoke.")
def token_revoke(server: str, token: str, name: str):
    """Revoke an API token on the server."""
    _api_request("delete", f"{server.rstrip('/')}/v1/tokens/{name}", token)
    click.echo(f"Revoked token '{name}'.")


@token_group.command("set-email")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to update.")
@click.option("--email", required=True, help="New email address.")
def token_set_email(server: str, token: str, name: str, email: str):
    """Set the email address on a token.

    Admins can update any token's email.  Non-admin users can only
    update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/email",
        token,
        json={"email": email},
    )
    click.echo(f"Email for '{name}' updated to '{email}'.")


@token_group.command("requests")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "denied"]),
    default=None,
    help="Filter by status (default: all).",
)
def token_requests(server: str, token: str, status: str | None):
    """List token registration requests."""
    url = f"{server.rstrip('/')}/v1/token-requests"
    params = {}
    if status:
        params["status"] = status
    data = _api_request("get", url, token, params=params)
    requests = data.get("requests", [])
    if not requests:
        click.echo("No token requests found.")
        return
    click.echo(f"{'ID':<6} {'Name':<20} {'Email':<30} {'Role':<12} {'Status':<10}")
    click.echo("-" * 78)
    for r in requests:
        click.echo(
            f"{r['id']:<6} {r['name']:<20} {r['email']:<30} {r['role']:<12} {r['status']:<10}"
        )


@token_group.command("approve")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.argument("request_id", type=int)
def token_approve(server: str, token: str, request_id: int):
    """Approve a pending token registration request."""
    url = f"{server.rstrip('/')}/v1/token-requests/{request_id}/approve"
    data = _api_request("post", url, token)
    click.echo(data.get("message", "approved"))
    raw = data.get("token")
    if raw:
        click.echo(f"  Token: {raw}")
        click.echo("  ⚠ Send this token to the requester — it will not be shown again.")


@token_group.command("deny")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.argument("request_id", type=int)
def token_deny(server: str, token: str, request_id: int):
    """Deny a pending token registration request."""
    url = f"{server.rstrip('/')}/v1/token-requests/{request_id}/deny"
    data = _api_request("post", url, token)
    click.echo(data.get("message", "denied"))


@token_group.command("set-description")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to update.")
@click.option("--description", required=True, help="New description.")
def token_set_description(server: str, token: str, name: str, description: str):
    """Set the description on a token.

    Admins can update any token's description.  Non-admin users can
    only update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/profile",
        token,
        json={"description": description},
    )
    click.echo(f"Description for '{name}' updated.")


@token_group.command("set-metadata")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to update.")
@click.option("--metadata", required=True, help="New metadata (JSON or arbitrary text).")
def token_set_metadata(server: str, token: str, name: str, metadata: str):
    """Set the metadata on a token.

    Admins can update any token's metadata.  Non-admin users can
    only update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/profile",
        token,
        json={"metadata": metadata},
    )
    click.echo(f"Metadata for '{name}' updated.")


# ── user profile lookup (client → server API) ──────────────────


@cli.group("user")
def user_group() -> None:
    """Look up user profiles on the server."""


@user_group.command("info")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.argument("name")
def user_info(server: str, name: str):
    """Look up a user's public profile by name."""
    import httpx

    url = f"{server.rstrip('/')}/v1/users/{name}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        raise click.ClickException(f"user '{name}' not found")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"  Name:        {data['name']}")
    click.echo(f"  Role:        {data['role']}")
    click.echo(f"  Email:       {data.get('email', '')}")
    click.echo(f"  Description: {data.get('description', '')}")
    if data.get("metadata"):
        click.echo(f"  Metadata:    {data['metadata']}")
    click.echo(f"  Packages:    {data.get('packages_published', 0)}")
    click.echo(f"  Created:     {data.get('created_at', '')}")


@user_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option("--name", default="", help="Filter by username (substring match).")
@click.option("--email", default="", help="Filter by email (substring match).")
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher", "admin"]),
    default=None,
    help="Filter by role.",
)
@click.option("--org", default="", help="Filter by organization membership.")
@click.option(
    "--has-published",
    is_flag=True,
    default=False,
    help="Only show users who have published packages.",
)
@click.option(
    "--sort",
    type=click.Choice(["name", "email", "packages_published"]),
    default="name",
    help="Sort field.  [default: name]",
)
@click.option(
    "--order",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    help="Sort order.  [default: asc]",
)
@click.option("--limit", type=int, default=100, help="Results per page.  [default: 100]")
@click.option("--offset", type=int, default=0, help="Pagination offset.  [default: 0]")
def user_list(
    server: str,
    name: str,
    email: str,
    role: str | None,
    org: str,
    has_published: bool,
    sort: str,
    order: str,
    limit: int,
    offset: int,
):
    """List user identities with pagination and filtering."""
    import httpx

    params: dict = {"limit": limit, "offset": offset, "sort": sort, "order": order}
    if name:
        params["name"] = name
    if email:
        params["email"] = email
    if role:
        params["role"] = role
    if org:
        params["org"] = org
    if has_published:
        params["has_published"] = "true"

    url = f"{server.rstrip('/')}/v1/users"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    users = data.get("users", [])
    total = data.get("total", 0)
    if not users:
        click.echo("No users found.")
        return
    click.echo(f"Showing {len(users)} of {total} users:")
    click.echo(f"  {'Name':<24} {'Role':<12} {'Email':<30} {'Pkgs':<6}")
    click.echo("  " + "-" * 72)
    for u in users:
        click.echo(
            f"  {u['name']:<24} {u['role']:<12} {u.get('email', ''):<30} "
            f"{u.get('packages_published', 0):<6}"
        )


@user_group.command("by-email")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.argument("email")
def user_by_email(server: str, email: str):
    """Look up a user's profile by email address."""
    import httpx

    url = f"{server.rstrip('/')}/v1/users/by-email/{email}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        raise click.ClickException(f"no user with email '{email}' found")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"  Name:        {data['name']}")
    click.echo(f"  Role:        {data['role']}")
    click.echo(f"  Email:       {data.get('email', '')}")
    click.echo(f"  Description: {data.get('description', '')}")
    if data.get("metadata"):
        click.echo(f"  Metadata:    {data['metadata']}")
    click.echo(f"  Packages:    {data.get('packages_published', 0)}")
    click.echo(f"  Created:     {data.get('created_at', '')}")


# ── self-service registration (client → server API) ────────────


@cli.command("register")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option("--name", required=True, help="Desired token name / identity.")
@click.option("--email", required=True, help="Email address.")
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher"]),
    default="reader",
    help="Requested role.  [default: reader]",
)
@click.option("--description", default="", help="User description (unicode).")
@click.option("--metadata", default="", help="Arbitrary JSON or text metadata.")
def register_cmd(server: str, name: str, email: str, role: str, description: str, metadata: str):
    """Register for an API token on a cvcpkg-server.

    Depending on the server's registration mode, you will either
    receive a token immediately (open mode) or your request will be
    queued for admin approval (admin-gated mode).
    """
    import httpx

    url = f"{server.rstrip('/')}/v1/register"
    body: dict = {"name": name, "email": email, "role": role}
    if description:
        body["description"] = description
    if metadata:
        body["metadata"] = metadata
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(data.get("message", "done"))
    token_value = data.get("token")
    if token_value:
        click.echo(f"  Token: {token_value}")
        click.echo("  ⚠ Save this token — it will not be shown again.")
        click.echo(f"  Configure your client: cvcpkg config set token {token_value}")
    request_id = data.get("request_id")
    if request_id:
        click.echo(f"  Request ID: {request_id}")
        click.echo("  You will be notified when an admin reviews your request.")


# ── remote server management (client → server API) ─────────────


@cli.group("server")
def server_group() -> None:
    """Remote server management commands (requires admin token)."""


@server_group.command("stop")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    metavar="TOKEN",
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.confirmation_option(prompt="Are you sure you want to shut down the server?")
def server_stop(server: str, token: str):
    """Gracefully shut down the remote cvcpkg-server (requires admin token)."""
    import httpx

    url = f"{server.rstrip('/')}/v1/admin/shutdown"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 403:
        raise click.ClickException("permission denied — admin token required")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    click.echo("Server shutdown initiated.")


@server_group.command("status")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
def server_status(server: str):
    """Check the status of the remote cvcpkg-server."""
    import httpx

    url = f"{server.rstrip('/')}/healthz"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
    except httpx.ConnectError as exc:
        raise click.ClickException(f"cannot connect to {server}") from exc
    if resp.status_code != 200:
        raise click.ClickException(f"server returned {resp.status_code}")
    data = resp.json()
    click.echo(f"  Status:     {data.get('status', 'ok')}")
    click.echo(f"  Version:    {data.get('version', '?')}")
    click.echo(f"  Packages:   {data.get('packages_count', '?')}")
    click.echo(f"  Uptime:     {data.get('uptime_seconds', '?')}s")
    click.echo(f"  Mirror:     {data.get('mirror_mode', False)}")


# ── remote org member management (client → server API) ─────────


@cli.group("org")
def org_group() -> None:
    """Manage organizations on the server.

    Organization owners can add/remove members to control who can
    publish to the organization's namespace — without affecting the
    member's access to anything else.
    """


@org_group.command("members")
@click.argument("slug")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
def org_members(slug: str, server: str, token: str):
    """List members of an organization."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/orgs/{slug}", token)
    members = data.get("members", [])
    if not members:
        click.echo(f"Organization '{slug}' has no members.")
        return
    click.echo(f"Members of '{slug}':")
    for m in members:
        click.echo(f"  {m['token_name']:<24} role={m['role']}")


@org_group.command("add-member")
@click.argument("slug")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to add as member.")
@click.option(
    "--role",
    type=click.Choice(["owner", "member"]),
    default="member",
    help="Org-level role (default: member).",
)
def org_add_member(slug: str, server: str, token: str, name: str, role: str):
    """Add a member to an organization."""
    url = f"{server.rstrip('/')}/v1/orgs/{slug}/members"
    _api_request("post", url, token, params={"token_name": name, "role": role})
    click.echo(f"Added '{name}' to '{slug}' as {role}.")


@org_group.command("remove-member")
@click.argument("slug")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to remove.")
def org_remove_member(slug: str, server: str, token: str, name: str):
    """Remove a member from an organization.

    This revokes access to the organization's packages without
    affecting the member's global token or access to other orgs.
    """
    url = f"{server.rstrip('/')}/v1/orgs/{slug}/members/{name}"
    _api_request("delete", url, token)
    click.echo(f"Removed '{name}' from '{slug}'.")


# ── Builder commands ────────────────────────────────────────────


@cli.group("builder")
def builder_group() -> None:
    """Manage remote build agents."""


@builder_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--platform", default=None, help="Filter by platform.")
@click.option("--arch", default=None, help="Filter by architecture.")
@click.option("--status", default=None, help="Filter by status (online/offline/busy).")
def builder_list(server: str, token: str, platform: str | None, arch: str | None, status: str | None):
    """List registered builders."""
    import httpx

    params: dict[str, str] = {}
    if platform:
        params["platform"] = platform
    if arch:
        params["arch"] = arch
    if status:
        params["status"] = status
    url = f"{server.rstrip('/')}/v1/builders"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    builders = data.get("builders", [])
    if not builders:
        click.echo("No builders registered.")
        return
    click.echo(f"{'ID':>5}  {'Name':<24} {'Platform':<10} {'Arch':<10} {'Status':<8} {'Jobs':>4}")
    click.echo("-" * 72)
    for b in builders:
        click.echo(
            f"{b['id']:>5}  {b['name']:<24} {b['platform']:<10} {b['arch']:<10} "
            f"{b['status']:<8} {b['current_jobs']}/{b['max_jobs']:>3}"
        )


@builder_group.command("status")
@click.argument("builder_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builder_status(builder_id: int, server: str, token: str):
    """Show details for a specific builder."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{data['id']}: {data['name']}")
    click.echo(f"  Org:         {data.get('org_slug') or '(global)'}")
    click.echo(f"  Platform:    {data['platform']}/{data['arch']}")
    click.echo(f"  Status:      {data['status']}")
    click.echo(f"  Jobs:        {data['current_jobs']}/{data['max_jobs']}")
    click.echo(f"  Labels:      {', '.join(data.get('labels', [])) or '(none)'}")
    click.echo(f"  Affinity:    {'yes' if data.get('prefer_affinity') else 'no'}")
    click.echo(f"  Last HB:     {data.get('last_heartbeat') or 'never'}")
    click.echo(f"  Registered:  {data.get('created_at', 'unknown')}")


@builder_group.command("run")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Builder name (unique per org).")
@click.option("--platform", default=None, help="Platform (default: auto-detect).")
@click.option("--arch", default=None, help="Architecture (default: auto-detect).")
@click.option("--org", "org_slug", default="", help="Organization scope.")
@click.option("--max-jobs", type=int, default=1, help="Max concurrent jobs.")
@click.option("--label", "labels", multiple=True, help="Labels (repeatable).")
def builder_run(
    server: str,
    token: str,
    name: str,
    platform: str | None,
    arch: str | None,
    org_slug: str,
    max_jobs: int,
    labels: tuple[str, ...],
):
    """Register as a builder and run the heartbeat loop.

    Registers this machine as a remote builder, then sends periodic
    heartbeats to the server.  Press Ctrl-C to unregister and exit.
    """
    import signal
    import time

    import httpx

    if platform is None:
        import sysconfig

        platform = sysconfig.get_platform().split("-")[0]
    if arch is None:
        import sysconfig

        platform_full = sysconfig.get_platform()
        parts = platform_full.split("-")
        arch = parts[-1] if len(parts) > 1 else "unknown"

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "name": name,
        "platform": platform,
        "arch": arch,
        "org_slug": org_slug,
        "max_jobs": max_jobs,
        "labels": list(labels),
        "capabilities": {},
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{base}/v1/builders/register", headers=headers, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"registration failed ({resp.status_code}): {detail}")
    info = resp.json()
    builder_id = info["id"]
    click.echo(f"Registered builder #{builder_id} ({name}) — {platform}/{arch}")

    shutdown = False

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not shutdown:
            time.sleep(60)
            if shutdown:
                break
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(
                        f"{base}/v1/builders/{builder_id}/heartbeat",
                        headers=headers,
                        json={"status": "online", "current_jobs": 0},
                    )
                if resp.status_code >= 400:
                    click.echo(f"heartbeat failed: {resp.status_code}", err=True)
            except Exception as exc:
                click.echo(f"heartbeat error: {exc}", err=True)
    finally:
        click.echo("Shutting down — unregistering builder…")
        try:
            with httpx.Client(timeout=10) as client:
                client.delete(f"{base}/v1/builders/{builder_id}", headers=headers)
            click.echo("Builder unregistered.")
        except Exception:
            click.echo("Warning: failed to unregister builder.", err=True)


@builder_group.command("stop")
@click.argument("builder_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token (admin).  [env: CVCPKG_TOKEN]",
)
def builder_stop(builder_id: int, server: str, token: str):
    """Unregister a builder by ID (admin-only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{builder_id} unregistered.")


# ── Build job commands ──────────────────────────────────────────


@cli.group("builds")
def builds_group() -> None:
    """Manage remote build jobs."""


@builds_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--status", default=None, help="Filter by status.")
@click.option("--platform", default=None, help="Filter by platform.")
@click.option("--dag-id", default=None, help="Filter by DAG ID.")
@click.option("--recipe", "recipe_name", default=None, help="Filter by recipe name.")
@click.option("--limit", type=int, default=50, help="Max results.")
def builds_list(
    server: str,
    token: str,
    status: str | None,
    platform: str | None,
    dag_id: str | None,
    recipe_name: str | None,
    limit: int,
):
    """List build jobs."""
    import httpx

    params: dict[str, str | int] = {"limit": limit}
    if status:
        params["status"] = status
    if platform:
        params["platform"] = platform
    if dag_id:
        params["dag_id"] = dag_id
    if recipe_name:
        params["recipe_name"] = recipe_name
    url = f"{server.rstrip('/')}/v1/builds"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    jobs = data.get("jobs", [])
    if not jobs:
        click.echo("No build jobs found.")
        return
    click.echo(
        f"{'ID':>5}  {'Recipe':<20} {'Platform':<10} {'Config':<8} "
        f"{'Link':<7} {'Status':<10} {'DAG':>8}"
    )
    click.echo("-" * 78)
    for j in jobs:
        click.echo(
            f"{j['id']:>5}  {j['recipe_name']:<20} {j['platform']:<10} "
            f"{j['config']:<8} {j['link']:<7} {j['status']:<10} "
            f"{(j.get('dag_id') or '-'):>8}"
        )


@builds_group.command("info")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_info(job_id: int, server: str, token: str):
    """Show details for a build job."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/builds/{job_id}", token)
    click.echo(f"Build #{data['id']}: {data['recipe_name']}")
    click.echo(f"  Version:     {data.get('recipe_version') or '-'}")
    click.echo(f"  Platform:    {data['platform']}/{data['arch']}")
    click.echo(f"  Config:      {data['config']}")
    click.echo(f"  Link:        {data['link']}")
    click.echo(f"  Status:      {data['status']}")
    click.echo(f"  DAG:         {data.get('dag_id') or '-'}")
    click.echo(f"  Builder:     {data.get('builder_id') or 'unassigned'}")
    click.echo(f"  Priority:    {data.get('priority', 0)}")
    click.echo(f"  Submitted:   {data.get('submitted_at', 'unknown')}")
    click.echo(f"  Started:     {data.get('started_at') or '-'}")
    click.echo(f"  Finished:    {data.get('finished_at') or '-'}")
    if data.get("error_message"):
        click.echo(f"  Error:       {data['error_message']}")
    if data.get("result_archive_url"):
        click.echo(f"  Archive:     {data['result_archive_url']}")
    deps = data.get("depends_on", [])
    if deps:
        click.echo(f"  Depends on:  {', '.join(str(d) for d in deps)}")


@builds_group.command("cancel")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_cancel(job_id: int, server: str, token: str):
    """Cancel a build job."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/{job_id}/cancel", token)
    click.echo(f"Build #{job_id}: {data.get('status', 'cancelled')}")


@builds_group.command("cancel-dag")
@click.argument("dag_id")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_cancel_dag(dag_id: str, server: str, token: str):
    """Cancel all pending/dispatched jobs in a DAG."""
    data = _api_request(
        "post", f"{server.rstrip('/')}/v1/builds/dag/{dag_id}/cancel", token
    )
    click.echo(f"DAG {dag_id}: {data.get('cancelled', 0)} jobs cancelled")


@builds_group.command("log")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--follow", "-f", is_flag=True, help="Follow log output (SSE stream).")
def builds_log(job_id: int, server: str, token: str, follow: bool):
    """View or follow the build log for a job."""
    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    if follow:
        url = f"{base}/v1/builds/{job_id}/log/stream"
        with httpx.Client(timeout=None) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise click.ClickException(
                        f"server returned {resp.status_code}"
                    )
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        click.echo(line[6:])
                    elif line.startswith("event: done"):
                        break
    else:
        url = f"{base}/v1/builds/{job_id}/log"
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code == 404:
            raise click.ClickException(f"no log available for build job {job_id}")
        if resp.status_code >= 400:
            raise click.ClickException(
                f"server returned {resp.status_code}: {resp.text}"
            )
        click.echo(resp.text, nl=False)


@builds_group.command("log-delete")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_log_delete(job_id: int, server: str, token: str):
    """Delete the log for a build job (admin only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builds/{job_id}/log", token)
    click.echo(f"Log for build #{job_id} deleted.")


@builds_group.command("purge")
@click.option(
    "--older-than", "older_than", required=True,
    help="Age threshold, e.g. '30d' (days).",
)
@click.option("--status", default=None, help="Only purge jobs with this status (e.g. 'failed').")
@click.option("--delete-logs/--keep-logs", default=True, help="Also delete log files (default: yes).")
@click.option("--delete-jobs/--logs-only", "delete_jobs", default=False,
              help="Delete entire job rows, not just logs (default: logs only).")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_purge(
    older_than: str, status: str | None, delete_logs: bool,
    delete_jobs: bool, server: str, token: str,
):
    """Purge old build logs/jobs (admin only).

    Example: cvcpkg builds purge --older-than 30d --status failed
    """
    import re
    import httpx

    m = re.match(r"^(\d+)d$", older_than)
    if not m:
        raise click.ClickException("--older-than must be in the form '<N>d', e.g. '30d'")
    days = int(m.group(1))

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str | int | bool] = {
        "older_than_days": days,
        "delete_logs": delete_logs,
    }
    if status:
        params["status"] = status

    if delete_jobs:
        endpoint = f"{base}/v1/admin/purge/builds"
    else:
        endpoint = f"{base}/v1/admin/gc/logs"

    with httpx.Client(timeout=120) as client:
        resp = client.post(endpoint, headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    what = "jobs" if delete_jobs else "logs"
    click.echo(f"Purged {data.get('purged', 0)} {what} older than {days}d.")


# ── Recipe distribution commands ────────────────────────────────


@cli.group("recipe")
def recipe_group() -> None:
    """Manage server-side recipe bundles."""


@recipe_group.command("push")
@click.argument("name")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--recipes-dir", type=click.Path(exists=True), default=None,
              help="Recipe source directory.")
@click.option("--org", "org_slug", default="", help="Organization scope.")
def recipe_push(name: str, server: str, token: str, recipes_dir: str | None, org_slug: str):
    """Bundle and push a recipe to the server."""
    import io
    import tarfile

    import httpx

    from cvcpkg.builder import RecipeError, find_recipes_dir

    if recipes_dir:
        rdir = Path(recipes_dir)
    else:
        try:
            rdir = find_recipes_dir()
        except RecipeError:
            raise click.ClickException("could not find recipes directory")

    recipe_path = rdir / name
    if not recipe_path.is_dir():
        raise click.ClickException(f"recipe directory not found: {recipe_path}")

    # Create tar.gz bundle
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(recipe_path.rglob("*")):
            if f.is_file():
                arcname = str(f.relative_to(recipe_path))
                tar.add(f, arcname=arcname)
    buf.seek(0)

    # Read recipe.yaml for version info
    recipe_yaml = recipe_path / "recipe.yaml"
    version = ""
    if recipe_yaml.is_file():
        import yaml
        with open(recipe_yaml) as f:
            data = yaml.safe_load(f)
        recipe_info = data.get("recipe", {})
        version = recipe_info.get("upstream_version", "")

    url = f"{server.rstrip('/')}/v1/recipes/{name}"
    params = {"org_slug": org_slug, "version": version}
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            url, headers=headers, params=params,
            files={"file": (f"{name}.tar.gz", buf, "application/gzip")},
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(
        f"Recipe '{data['name']}' uploaded "
        f"(version={data.get('version', '')}, "
        f"size={data.get('bundle_size', 0)} bytes)"
    )


@recipe_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default=None, help="Filter by organization.")
def recipe_list(server: str, token: str, org_slug: str | None):
    """List recipes available on the server."""
    import httpx

    params: dict[str, str] = {}
    if org_slug is not None:
        params["org_slug"] = org_slug
    url = f"{server.rstrip('/')}/v1/recipes"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    recipes = data.get("recipes", [])
    if not recipes:
        click.echo("No recipes found.")
        return
    click.echo(f"{'Name':<25} {'Version':<15} {'Size':>10}  {'Uploaded':>20}")
    click.echo("-" * 75)
    for r in recipes:
        size_str = f"{r.get('bundle_size', 0):,}"
        click.echo(
            f"{r['name']:<25} {r.get('version', ''):<15} "
            f"{size_str:>10}  {r.get('updated_at', 'unknown'):>20}"
        )


@recipe_group.command("delete")
@click.argument("name")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
def recipe_delete(name: str, server: str, token: str, org_slug: str):
    """Delete a recipe from the server (admin only)."""
    import httpx

    url = f"{server.rstrip('/')}/v1/recipes/{name}"
    params = {"org_slug": org_slug}
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = client.delete(url, headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    click.echo(f"Recipe '{name}' deleted.")


# ── Webhook CLI commands ────────────────────────────────────────


@cli.group("webhook")
def webhook_group() -> None:
    """Manage server webhooks."""


@webhook_group.command("register")
@click.argument("url")
@click.option(
    "--event", "-e", "events", multiple=True, required=True,
    help="Event(s) to subscribe to (can be repeated).",
)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
def webhook_register(url: str, events: tuple[str, ...], server: str, token: str, org_slug: str):
    """Register a new webhook."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"url": url, "events": list(events), "org_slug": org_slug}
    with httpx.Client(timeout=30) as client:
        resp = client.post(api, headers=headers, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"Webhook {data['id']} registered for {url}")


@webhook_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default=None, help="Filter by organization.")
def webhook_list(server: str, token: str, org_slug: str | None):
    """List registered webhooks."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks"
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str] = {}
    if org_slug is not None:
        params["org_slug"] = org_slug
    with httpx.Client(timeout=30) as client:
        resp = client.get(api, headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    for wh in data.get("webhooks", []):
        status = "active" if wh.get("active") else "inactive"
        click.echo(f"  [{wh['id']}] {wh['url']}  events={wh['events']}  ({status})")
    click.echo(f"Total: {data.get('total', 0)}")


@webhook_group.command("info")
@click.argument("webhook_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def webhook_info(webhook_id: int, server: str, token: str):
    """Get details for a webhook."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks/{webhook_id}"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(api, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    for k, v in data.items():
        click.echo(f"  {k}: {v}")


@webhook_group.command("update")
@click.argument("webhook_id", type=int)
@click.option("--url", default=None, help="New delivery URL.")
@click.option("--event", "-e", "events", multiple=True, help="Replace events list.")
@click.option("--active/--inactive", default=None, help="Enable or disable.")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def webhook_update(
    webhook_id: int, url: str | None, events: tuple[str, ...],
    active: bool | None, server: str, token: str,
):
    """Update a webhook."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks/{webhook_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body: dict = {}
    if url is not None:
        body["url"] = url
    if events:
        body["events"] = list(events)
    if active is not None:
        body["active"] = active
    if not body:
        raise click.ClickException("nothing to update — supply at least one option")
    with httpx.Client(timeout=30) as client:
        resp = client.patch(api, headers=headers, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    click.echo(f"Webhook {webhook_id} updated.")


@webhook_group.command("delete")
@click.argument("webhook_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def webhook_delete(webhook_id: int, server: str, token: str):
    """Delete a webhook (admin only)."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks/{webhook_id}"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = client.delete(api, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    click.echo(f"Webhook {webhook_id} deleted.")


@webhook_group.command("test")
@click.argument("webhook_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def webhook_test(webhook_id: int, server: str, token: str):
    """Send a test payload to a webhook."""
    import httpx

    api = f"{server.rstrip('/')}/v1/webhooks/{webhook_id}/test"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = client.post(api, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"Test delivery: status_code={data.get('status_code', '?')}")


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
