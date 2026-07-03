"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import os
from pathlib import Path

import click

from cvcpkg.cli._helpers import (
    _platform_opt,
    _config_opt,
    _link_opt,
    _prefix_opt,
    _keep_build_opt,
    _recipes_dir_opt,
    _no_default_recipes_opt,
    _local_opt,
    _maintainer_opt,
    _resolve_recipes_dirs,
    _validate_org_slug,
)
from cvcpkg.cli import cli

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
@_no_default_recipes_opt
def world(
    from_file: str,
    platform: str,
    config: str,
    link: str,
    prefix: str,
    keep_build_dir: bool,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
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
        load_all_recipes,
        resolve_build_order,
    )
    from cvcpkg.manifest import Requirements

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve()

    reqs = Requirements.from_yaml(from_file)
    requested = {c.name for c in reqs.components if not c.exclude}

    # Load all recipes.
    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)
    all_recipes = (
        load_all_recipes(rdirs)
        if len(rdirs) > 1
        else [Recipe.load(d) for d in rdirs[0].iterdir() if (d / "recipe.yaml").is_file()]
    )

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


def _resolve_recipe_dir(
    name: str,
    recipes_dirs: tuple[str, ...] = (),
    *,
    no_default: bool = False,
) -> Path:
    """Resolve a recipe name or path to its directory.

    Accepts either a path to a directory containing recipe.yaml, a
    direct path to a recipe.yaml file, or a bare recipe name (e.g.
    "grpc") which is looked up in the canonical recipe directories
    (bundled + any extra overlays). Later directories take precedence.
    """
    p = Path(name)
    if p.is_file() and p.name == "recipe.yaml":
        return p.parent.resolve()
    if p.is_dir() and (p / "recipe.yaml").is_file():
        return p.resolve()

    # Search resolved dirs in reverse order (later = higher priority).
    for rdir in reversed(_resolve_recipes_dirs(recipes_dirs, no_default=no_default)):
        candidate = rdir / name
        if candidate.is_dir() and (candidate / "recipe.yaml").is_file():
            return candidate.resolve()

    raise click.ClickException(f"Recipe not found: {name}")


def _auto_platform(platform: str) -> str:
    """Resolve 'auto' to the detected platform, pass others through."""
    if platform == "auto":
        from cvcpkg.platform import detect_platform

        return detect_platform()
    return platform


def _try_pull_server_recipes() -> tuple[str, ...]:
    """Try to download the recipe set from the server.

    Returns a 1-tuple of the local directory path if successful,
    or an empty tuple (so the caller falls through to local recipes).
    """
    from cvcpkg.config import default_server_url

    server = default_server_url()
    token = os.environ.get("CVCPKG_TOKEN", "")
    try:
        import httpx

        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(timeout=60) as client:
            resp = client.get(
                f"{server.rstrip('/')}/v1/recipes/bundle",
                headers=headers,
            )
        if resp.status_code != 200:
            click.echo(
                f"cvcpkg: could not fetch recipe set from {server} "
                f"(HTTP {resp.status_code}), falling back to local recipes.",
                err=True,
            )
            return ()
    except Exception as exc:
        click.echo(
            f"cvcpkg: could not reach {server} ({exc}), " "falling back to local recipes.",
            err=True,
        )
        return ()

    # Extract to a cache dir
    import tarfile
    import tempfile

    cache_base = Path(tempfile.gettempdir()) / "cvcpkg-server-recipes"
    cache_base.mkdir(parents=True, exist_ok=True)
    # Write bundle
    bundle_path = cache_base / "server-recipes.tar.gz"
    bundle_path.write_bytes(resp.content)
    extract_dir = cache_base / "recipes"
    if extract_dir.is_dir():
        import shutil

        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with tarfile.open(bundle_path, "r:gz") as tar:
        tar.extractall(path=extract_dir)  # noqa: S202
    click.echo(f"cvcpkg: using recipes from {server}")
    return (str(extract_dir),)


# ── build ───────────────────────────────────────────────────────


@cli.command()
@click.argument("recipe", nargs=-1, required=True)
@_platform_opt
@_config_opt
@_link_opt
@click.option("--prefix", type=click.Path(), default=None, help="Install prefix.")
@_keep_build_opt
@_recipes_dir_opt
@_no_default_recipes_opt
@_local_opt
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
    no_default_recipes: bool,
    local_mode: bool,
    with_deps: bool,
    host_platform: str,
) -> None:
    """Build one or more recipes from source.

    Downloads the upstream source (or uses vendored sources), applies
    patches, and runs the recipe's platform-specific build script.
    Results are installed into --prefix.

    By default, recipes are fetched from the package server (set via
    CVCPKG_SERVER_URL, default: cvcpkg.org).  Use --local to build
    from bundled/local recipes only.

    Dependencies are automatically resolved and built first unless
    --no-deps is specified.

    \b
    Examples:
      cvcpkg build zlib --prefix ./prefix
      cvcpkg build zlib --local --prefix ./prefix
      cvcpkg build grpc protobuf --config debug --link static
      cvcpkg build mypkg --recipes-dir ./my-recipes --recipes-dir recipes
      cvcpkg build vtk --no-deps --prefix ./prefix
    """
    from cvcpkg.builder import build_recipe, resolve_build_order

    plat = _auto_platform(platform)

    # If --local is not set and --recipes-dir is not specified,
    # try to pull recipes from the server.
    if not local_mode and not recipes_dirs:
        recipes_dirs = _try_pull_server_recipes()
    prefix_path = Path(prefix).resolve() if prefix else None

    if with_deps:
        # Resolve all deps and build in topological order
        rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)
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
            deps_block = r.raw.get("depends", {})
            # Collect both build and runtime deps so that libraries
            # needed at link time (listed under runtime) are available.
            all_deps: list = []
            for key in ("build", "runtime"):
                all_deps.extend(deps_block.get(key, []) or [])
            for d in all_deps:
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
            recipe_dir = _resolve_recipe_dir(name, recipes_dirs, no_default=no_default_recipes)
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
@_no_default_recipes_opt
@_local_opt
@_maintainer_opt
@click.option(
    "--signing-key",
    type=click.Path(exists=True),
    default=None,
    help="Path to Ed25519 private key to sign archives.",
)
@click.option(
    "--from-prefix",
    "from_prefix",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help=(
        "Skip the build and package an already-installed prefix directory. "
        "Downstream projects that build with their own toolchain can stage "
        "their install tree, then use the recipe purely for metadata "
        "(deps, cmake_packages, tags). Requires exactly one RECIPE arg."
    ),
)
@click.option(
    "--version-override",
    "version_override",
    default="",
    help=(
        "Replace the recipe's upstream_version in the manifest and archive "
        "filename. The '+cvc.<rev>' cvc_revision suffix is preserved. "
        "Useful with --from-prefix when downstream computes its version "
        "from git or CMake at build time."
    ),
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
    no_default_recipes: bool,
    local_mode: bool,
    maintainer: str,
    signing_key: str | None,
    from_prefix: str | None,
    version_override: str,
) -> None:
    """Build and archive one or more recipes.

    Like 'build', but also creates a distributable .tar.gz archive
    for each recipe containing the installed files, manifest.yaml,
    and SHA-256 checksum.  Archives are written to --output-dir.

    With --from-prefix DIR the build step is skipped and DIR is packaged
    directly. The recipe is used only for metadata (name, deps,
    cmake_packages, tags). Exactly one RECIPE must be supplied in this
    mode; the RECIPE arg may also be a filesystem path to a directory
    containing recipe.yaml.

    \b
    Example:
      cvcpkg pack zlib boost --output-dir ./dist
      cvcpkg pack zlib --local --output-dir ./dist
      cvcpkg pack ./cvcpkg/recipe.yaml --from-prefix ./stage \\
          --version-override 2.0.0 --output-dir ./dist
    """
    from cvcpkg.builder import pack_from_prefix, pack_recipe

    plat = _auto_platform(platform)

    if from_prefix:
        if len(recipe) != 1:
            raise click.UsageError("--from-prefix requires exactly one RECIPE argument.")
        if prefix:
            raise click.UsageError(
                "--prefix and --from-prefix are mutually exclusive. "
                "--from-prefix is the already-installed tree to package."
            )

    if not local_mode and not recipes_dirs:
        recipes_dirs = _try_pull_server_recipes()

    prefix_path = Path(prefix).resolve() if prefix else None
    output = Path(output_dir).resolve()

    for name in recipe:
        recipe_dir = _resolve_recipe_dir(name, recipes_dirs, no_default=no_default_recipes)
        if from_prefix:
            archive, sha, size = pack_from_prefix(
                recipe_dir,
                Path(from_prefix).resolve(),
                platform=plat,
                config=config,
                link=link,
                version_override=version_override,
                output_dir=output,
                maintainer=maintainer,
            )
        else:
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
@_no_default_recipes_opt
@_local_opt
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
    no_default_recipes: bool,
    local_mode: bool,
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
    from cvcpkg.builder import build_all

    plat = _auto_platform(platform)
    prefix_path = Path(prefix).resolve() if prefix else None
    work_dir_root = Path(work_dir).resolve() if work_dir else None

    if not local_mode and not recipes_dirs:
        recipes_dirs = _try_pull_server_recipes()

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

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
@_no_default_recipes_opt
@_local_opt
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
    no_default_recipes: bool,
    local_mode: bool,
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
    import shutil

    from cvcpkg.builder import (
        build_all,
        create_archive,
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

    if not local_mode and not recipes_dirs:
        recipes_dirs = _try_pull_server_recipes()

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

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
@_no_default_recipes_opt
def recipes(
    mode: str,
    show_name: str | None,
    tag: str | None,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
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
        recipe_dir = _resolve_recipe_dir(show_name, recipes_dirs, no_default=no_default_recipes)
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
    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)
    all_recipes = load_all_recipes(rdirs) if len(rdirs) > 1 else list_recipes(rdirs[0])

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


# ── rev-bump ──────────────────────────────────────────────────────────


@cli.command("rev-bump")
@click.argument("recipe_name")
@_recipes_dir_opt
@_no_default_recipes_opt
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
    no_default_recipes: bool,
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
    from cvcpkg.builder import rev_bump

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)
    # Use last dir containing the recipe (overlay wins).
    recipes_dir = rdirs[0]
    for rd in rdirs:
        if (rd / recipe_name).is_dir():
            recipes_dir = rd

    bumped = rev_bump(
        recipe_name,
        recipes_dir,
        platform=platform or "",
        cascade=not no_cascade,
    )

    for name, old_rev, new_rev in bumped:
        click.echo(f"  {name}: cvc_revision {old_rev} \u2192 {new_rev}")
    click.echo(f"\n{len(bumped)} recipe(s) bumped.")
