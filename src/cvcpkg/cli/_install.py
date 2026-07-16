"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._catalog import _fetch_mirror_urls
from cvcpkg.cli._helpers import (
    _VALID_ARCHES,
    _config_opt,
    _link_opt,
    _local_opt,
    _no_default_recipes_opt,
    _platform_opt,
    _prefix_opt,
    _recipes_dir_opt,
    _resolve_recipes_dirs,
)

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
        "Catalog source strategy.  'server' uses cvcpkg.org only; "
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
    help="Verify Ed25519 signatures on downloaded archives when present.",
)
@click.option(
    "--require-signatures",
    is_flag=True,
    default=False,
    help="Require a valid Ed25519 signature on every archive; fail on any "
    "unsigned or invalidly-signed package.  Implies --verify-signatures.",
)
@click.option(
    "--fallback-to-source/--no-fallback-to-source",
    default=False,
    help="Build from source recipe when no prebuilt binary is available.",
)
@_recipes_dir_opt
@_no_default_recipes_opt
@_local_opt
@click.option(
    "--keep-host-tools/--strip-host-tools",
    default=False,
    help=(
        "Keep any build-time host-tools prefix recorded for this deliverable "
        "instead of stripping it.  When a build separated host tools into their "
        "own prefix, install strips it by default (it is a build-time "
        "byproduct); pass --keep-host-tools to retain it."
    ),
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
    source: str,
    ignore_abi: bool,
    verify_signatures: bool,
    require_signatures: bool,
    fallback_to_source: bool,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    local_mode: bool,
    keep_host_tools: bool,
) -> None:
    """Install component bundles into a prefix.

    Downloads and extracts prebuilt component archives from the
    libcvc-deps release catalog.  Components can be specified as
    positional arguments or loaded from a cvc-requirements.yaml
    file via --from.

    Use --local to skip the catalog and build everything from local
    recipes (implies --fallback-to-source).

    \b
    Examples:
      # Install from a requirements file
      cvcpkg install --from cvc-requirements.yaml --prefix ./deps

      # Override config for a debug build (file says release)
      cvcpkg install --from cvc-requirements.yaml --config debug

      # Install individual components by name
      cvcpkg install zlib boost --prefix ./deps

      # Build from local recipes only (no server)
      cvcpkg install --local zlib boost --prefix ./deps

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

    # --local implies --fallback-to-source and skips the catalog entirely
    if local_mode:
        fallback_to_source = True
        skip_catalog = True
    else:
        skip_catalog = False

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
    #   auto   → primary (cvcpkg.org) with GitHub Pages fallback
    #   server → cvcpkg.org only, no fallback
    #   github → GitHub Pages only, no fallback
    catalog_url = catalog or ""
    catalog_failed = False
    if skip_catalog:
        catalog_failed = True
        cat = {"bundles": []}
    else:
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

    # ── Conflict check ──
    #
    # Before touching the filesystem, verify that no package we are
    # about to install declares a conflict with:
    #   (a) another package in the current install set, or
    #   (b) a package already present in the prefix (via lockfile).
    #
    # Conflict data comes from local recipe files.  When recipe dirs
    # are not available the check is skipped gracefully.
    rdirs = (
        _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes) if recipes_dirs else None
    )
    _check_conflicts(
        list(picked.keys()) + list(source_only),
        prefix_path,
        rdirs,
    )

    # ── Download and extract each resolved bundle ──
    cache_dir = default_cache_dir()
    lock_entries: list[LockEntry] = []
    rdirs = (
        _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes) if recipes_dirs else None
    )

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
            install_entry(
                entry,
                prefix_path,
                cache_dir,
                verify_signatures=verify_signatures or require_signatures,
                require_signatures=require_signatures,
            )
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

    # ── Write the CMake package config into the prefix ──
    #
    # So downstream projects can `find_package(cvcpkg CONFIG REQUIRED)`
    # (or the libcvc-deps compat name) with no manual CMAKE_PREFIX_PATH.
    try:
        from cvcpkg import __version__
        from cvcpkg.cmake_config import write_cmake_config

        write_cmake_config(prefix_path, __version__)
        click.echo(f"cvcpkg: CMake config written to {prefix_path / 'lib' / 'cmake' / 'cvcpkg'}")
    except OSError as exc:
        click.echo(f"cvcpkg: warning — could not write CMake config: {exc}", err=True)

    # ── Write activation scripts ──
    #
    # Users can `source <prefix>/bin/activate` (POSIX) or
    # `. <prefix>\Scripts\Activate.ps1` (Windows) to put the prefix on
    # PATH / CMAKE_PREFIX_PATH / PKG_CONFIG_PATH / *LIBRARY_PATH.
    try:
        from cvcpkg.activate import write_activate_scripts

        written = write_activate_scripts(prefix_path, platform=plat)
        if written:
            click.echo(f"cvcpkg: activation scripts written ({len(written)} files)")
            hint = _activation_hint(plat, prefix_path)
            if hint:
                click.echo(f"cvcpkg: activate with: {hint}")
    except OSError as exc:
        click.echo(f"cvcpkg: warning — could not write activate scripts: {exc}", err=True)

    # ── Strip the build-time host-tools prefix ──
    #
    # If a host-tools prefix was recorded for this deliverable (written by
    # 'cvcpkg build' into share/libcvc-deps/host-tools.yaml), strip it now: it
    # is a build-time byproduct, not part of the deliverable.  --keep-host-tools
    # retains it.  No-op when there is no record (a pure prebuilt install).
    try:
        from cvcpkg.host_tools import strip_host_tools

        stripped = strip_host_tools(prefix_path, keep=keep_host_tools)
        if stripped is not None:
            click.echo(f"cvcpkg: stripped host-tools prefix {stripped}")
    except OSError as exc:
        click.echo(f"cvcpkg: warning — could not strip host tools: {exc}", err=True)

    click.echo(f"cvcpkg: done -- {len(picked)} component(s) installed to {prefix_path}")

    # Opt-in telemetry (Phase 2): fire-and-forget, only when the user set
    # CVCPKG_TELEMETRY=1.  Never raises, never slows the install by more
    # than a few seconds, sends only anonymous environment facts.
    from cvcpkg.cli._telemetry import maybe_send_telemetry

    maybe_send_telemetry(os.environ.get("CVCPKG_SERVER_URL", ""))


def _activation_hint(plat: str, prefix: Path) -> str:
    """Return a copy-pasteable activate command for the user."""
    if plat == "windows":
        return f". {prefix}\\Scripts\\Activate.ps1"
    return f"source {prefix}/bin/activate"


def _check_conflicts(
    installing: list[str],
    prefix_path: Path | None,
    recipe_dirs: list[Path] | None,
) -> None:
    """Raise ConflictError when any package in *installing* conflicts with
    another package being installed or with an already-installed package.

    Skips the check silently when *recipe_dirs* is not provided.
    """
    if not recipe_dirs or not installing:
        return

    from cvcpkg.builder import collect_recipe_conflicts
    from cvcpkg.lockfile import Lockfile

    conflict_map = collect_recipe_conflicts(installing, recipe_dirs)
    if not conflict_map:
        return

    installing_set = set(installing)

    # Also check what is already installed in the prefix lockfile.
    installed_names: set[str] = set()
    if prefix_path is not None:
        lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
        if lock_path.is_file():
            try:
                existing_lock = Lockfile.read(lock_path)
                installed_names = {b.name for b in existing_lock.bundles}
            except Exception:
                pass

    for pkg, pkg_conflicts in conflict_map.items():
        for conflict in pkg_conflicts:
            if conflict in installing_set:
                raise click.ClickException(
                    f"{pkg!r} conflicts with co-requested package {conflict!r}.\n"
                    f"You cannot install both at the same time.\n"
                    f"Remove {conflict!r} from the install request and retry."
                )
            if conflict in installed_names:
                prefix_str = str(prefix_path) if prefix_path else "<prefix>"
                raise click.ClickException(
                    f"{pkg!r} conflicts with installed package {conflict!r}.\n"
                    f"To install {pkg!r}, first uninstall the conflicting package:\n"
                    f"  cvcpkg uninstall {conflict} --prefix {prefix_str}\n"
                    f"Then retry:\n"
                    f"  cvcpkg install {pkg} --prefix {prefix_str}"
                )


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


# ── upgrade ─────────────────────────────────────────────────────


def _version_is_newer(new_ver: str, old_ver: str) -> bool:
    """Return True if *new_ver* is a newer cvcpkg version than *old_ver*.

    Compares on (SemVer, cvc_revision) so a newer ``+cvc.N`` rebuild of the
    same upstream version counts as newer (Version.__lt__ ignores build
    metadata on its own).
    """
    from cvcpkg.semver import Version

    try:
        nv = Version.parse(new_ver)
        ov = Version.parse(old_ver)
    except ValueError:
        return new_ver != old_ver
    return (nv, nv.cvc_revision) > (ov, ov.cvc_revision)


@cli.command("upgrade")
@click.argument("components", nargs=-1)
@_prefix_opt
@click.option(
    "--catalog",
    metavar="URL",
    help="Override catalog URL or path to a local catalog YAML file.",
)
@click.option(
    "--source",
    type=click.Choice(["auto", "server", "github"], case_sensitive=False),
    default="auto",
    help="Catalog source strategy (see `cvcpkg install`).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be upgraded without downloading or changing anything.",
)
@click.option(
    "--verify-signatures/--no-verify-signatures",
    default=False,
    help="Verify Ed25519 signatures on downloaded archives when present.",
)
@click.option(
    "--require-signatures",
    is_flag=True,
    default=False,
    help="Require a valid signature on every upgraded archive.  Implies --verify-signatures.",
)
def upgrade(
    components: tuple[str, ...],
    prefix: str,
    catalog: str | None,
    source: str,
    dry_run: bool,
    verify_signatures: bool,
    require_signatures: bool,
) -> None:
    """Upgrade installed components to newer catalog versions, in place.

    Reads the prefix lockfile, checks the catalog for newer versions of the
    installed components (matching the prefix's platform/arch/config/link),
    and re-installs the ones with a newer version available.  Restrict to
    specific COMPONENTS by naming them; otherwise every installed component
    is considered.

    \b
    Examples:
      cvcpkg upgrade --prefix ./deps                 # upgrade everything
      cvcpkg upgrade zlib boost --prefix ./deps      # only these
      cvcpkg upgrade --prefix ./deps --dry-run       # preview
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import catalog_entries, fetch_catalog, load_catalog_from_file
    from cvcpkg.installer import install_entry
    from cvcpkg.lockfile import LockEntry, Lockfile
    from cvcpkg.resolver import _sort_candidates

    prefix_path = Path(prefix).resolve()
    lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
    if not lock_path.exists():
        raise click.ClickException(f"no lockfile at {lock_path} — nothing installed to upgrade")
    lock = Lockfile.read(lock_path)
    if not lock.bundles:
        click.echo("cvcpkg: lockfile is empty, nothing to upgrade.")
        return

    catalog_url = catalog or ""
    if catalog_url and Path(catalog_url).is_file():
        cat = load_catalog_from_file(catalog_url)
    else:
        cat = fetch_catalog(catalog_url, cache_dir=default_cache_dir())

    entries = catalog_entries(
        cat,
        platform=lock.platform,
        arch=lock.arch,
        build_type=lock.config,
        link=lock.link,
    )
    by_name: dict[str, list] = {}
    for e in entries:
        by_name.setdefault(e.name, []).append(e)

    wanted = {c.split("==")[0] for c in components} if components else None
    if wanted:
        unknown = wanted - {b.name for b in lock.bundles}
        if unknown:
            raise click.ClickException(
                f"not installed in this prefix: {', '.join(sorted(unknown))}"
            )

    # Build the upgrade plan: (LockEntry, newest CatalogEntry).
    plan: list[tuple[LockEntry, object]] = []
    for b in lock.bundles:
        if wanted and b.name not in wanted:
            continue
        cands = by_name.get(b.name, [])
        if not cands:
            continue
        newest = _sort_candidates(cands, "")[0]
        if _version_is_newer(newest.version, b.version):
            plan.append((b, newest))

    if not plan:
        click.echo("cvcpkg: everything is up to date.")
        return

    click.echo(f"cvcpkg: {len(plan)} upgrade(s) available for {prefix_path}:")
    for b, new in plan:
        click.echo(f"  {b.name}: {b.version} -> {new.version}")

    if dry_run:
        click.echo("cvcpkg: dry run — no changes made.")
        return

    cache_dir = default_cache_dir()
    server_url = os.environ.get("CVCPKG_SERVER_URL", "")
    mirror_urls: list[str] = []
    if server_url:
        mirror_urls = _fetch_mirror_urls(server_url, os.environ.get("CVCPKG_TOKEN"))

    by_lock_name = {b.name: b for b in lock.bundles}
    upgraded = 0
    for b, new in plan:
        if mirror_urls and new.archive_url:
            fname = new.archive_url.rsplit("/", 1)[-1]
            for murl in mirror_urls:
                fallback = f"{murl.rstrip('/')}/v1/mirror/download/{fname}"
                if fallback not in new.mirror_urls:
                    new.mirror_urls.append(fallback)
        click.echo(f"cvcpkg: upgrading {b.name} {b.version} -> {new.version} ...")
        install_entry(
            new,
            prefix_path,
            cache_dir,
            verify_signatures=verify_signatures or require_signatures,
            require_signatures=require_signatures,
        )
        by_lock_name[b.name] = LockEntry(
            name=new.name,
            version=new.version,
            upstream_version=new.upstream_version,
            source_release=new.source_release,
            sha256=new.sha256,
            size_bytes=new.size_bytes,
            archive_url=new.archive_url,
        )
        upgraded += 1

    lock.bundles = list(by_lock_name.values())
    lock.catalog_revision = cat.get("revision", lock.catalog_revision)
    lock.write(lock_path)
    click.echo(f"cvcpkg: upgraded {upgraded} component(s); lockfile updated.")
