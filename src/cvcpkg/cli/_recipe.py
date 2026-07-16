"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

from pathlib import Path

from cvcpkg._archive import safe_tar_extractall
import click
import yaml

from cvcpkg.cli import cli
from cvcpkg.cli._helpers import (
    _resolve_recipes_dirs,
)

# ── Recipe distribution commands ────────────────────────────────


def _bundle_vendored_source(tar, recipe_path: Path, rdir: Path) -> None:
    """If the recipe uses ``source.type: vendored``, bundle the vendored
    source tree into the tarball under ``_vendored/<path>/``."""
    recipe_yaml = recipe_path / "recipe.yaml"
    if not recipe_yaml.is_file():
        return
    data = yaml.safe_load(recipe_yaml.read_text())
    source = data.get("source", {})
    if source.get("type") != "vendored" or not source.get("path"):
        return
    # Resolve the vendored path relative to the repo root (parent of recipes/)
    repo_root = rdir.parent
    vendored = (repo_root / source["path"]).resolve()
    if not vendored.is_dir():
        return
    for f in sorted(vendored.rglob("*")):
        if f.is_file():
            arcname = f"_vendored/{source['path']}/{f.relative_to(vendored)}"
            tar.add(f, arcname=arcname)


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
@click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help="Extra recipes directory to overlay on the default.",
)
@click.option(
    "--no-default-recipes",
    is_flag=True,
    default=False,
    help="Ignore the auto-detected default recipes directory.",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
def recipe_push(
    name: str,
    server: str,
    token: str,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    org_slug: str,
):
    """Bundle and push a recipe to the server."""
    import io
    import tarfile

    import httpx

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

    # Search for the recipe in reverse order (later = higher priority)
    rdir = None
    for d in reversed(rdirs):
        if (d / name).is_dir() and (d / name / "recipe.yaml").is_file():
            rdir = d
            break
    if rdir is None:
        rdir = rdirs[0]

    recipe_path = rdir / name
    if not recipe_path.is_dir():
        raise click.ClickException(f"recipe directory not found: {recipe_path}")

    # Create tar.gz bundle
    #
    # Recipe files are stored under ``<name>/`` so that build scripts
    # can reference ``${SCRIPT_DIR}/../_common/env-linux.sh`` and resolve
    # correctly after extraction.  If a ``_common/`` sibling directory
    # exists in the recipes root, it is included alongside.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(recipe_path.rglob("*")):
            if f.is_file():
                arcname = f"{name}/{f.relative_to(recipe_path)}"
                tar.add(f, arcname=arcname)
        # Include _common/ sibling (shared build helpers)
        common_dir = rdir / "_common"
        if common_dir.is_dir():
            for f in sorted(common_dir.rglob("*")):
                if f.is_file():
                    arcname = f"_common/{f.relative_to(common_dir)}"
                    tar.add(f, arcname=arcname)
        # Include vendored source directory if source.type == "vendored"
        _bundle_vendored_source(tar, recipe_path, rdir)
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
            url,
            headers=headers,
            params=params,
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


@recipe_group.command("publish")
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
@click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help="Extra recipes directory to overlay on the default.",
)
@click.option(
    "--no-default-recipes",
    is_flag=True,
    default=False,
    help="Ignore the auto-detected default recipes directory.",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
def recipe_publish(
    name: str,
    server: str,
    token: str,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    org_slug: str,
):
    """Publish a recipe to the server (push recipe + register placeholder package).

    This pushes the recipe bundle to the server and registers a
    placeholder entry in the catalog so the recipe is discoverable.
    The placeholder has no build artifacts — it signals that the recipe
    is available for remote builds or local source builds.

    \b
    Examples:
      cvcpkg recipe publish zlib
      cvcpkg recipe publish my-library --org my-org
    """
    import io
    import tarfile

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

    # Search for the recipe in reverse order (later = higher priority)
    rdir = None
    for d in reversed(rdirs):
        if (d / name).is_dir() and (d / name / "recipe.yaml").is_file():
            rdir = d
            break
    if rdir is None:
        rdir = rdirs[0]

    recipe_path = rdir / name
    if not recipe_path.is_dir():
        raise click.ClickException(f"recipe directory not found: {recipe_path}")

    # Read recipe metadata
    recipe_yaml = recipe_path / "recipe.yaml"
    version = ""
    description = ""
    homepage = ""
    pkg_license = ""
    maintainer_field = ""
    if recipe_yaml.is_file():
        recipe_data = yaml.safe_load(recipe_yaml.read_text())
        recipe_info = recipe_data.get("recipe", {})
        version = recipe_info.get("upstream_version", "")
        cvc_rev = recipe_data.get("cvc_revision", recipe_info.get("cvc_revision", 1))
        full_version = f"{version}+cvc.{cvc_rev}" if version else ""
        description = recipe_info.get("description", "")
        homepage = recipe_info.get("homepage", "")
        pkg_license = recipe_info.get("license", "")
        maintainer_field = recipe_info.get("maintainer", "")
    else:
        full_version = ""

    # 1. Push the recipe bundle (reuse recipe push logic)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(recipe_path.rglob("*")):
            if f.is_file():
                arcname = f"{name}/{f.relative_to(recipe_path)}"
                tar.add(f, arcname=arcname)
        common_dir = rdir / "_common"
        if common_dir.is_dir():
            for f in sorted(common_dir.rglob("*")):
                if f.is_file():
                    arcname = f"_common/{f.relative_to(common_dir)}"
                    tar.add(f, arcname=arcname)
    buf.seek(0)

    url = f"{base}/v1/recipes/{name}"
    params = {"org_slug": org_slug, "version": version}
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            url,
            headers=headers,
            params=params,
            files={"file": (f"{name}.tar.gz", buf, "application/gzip")},
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"recipe push failed ({resp.status_code}): {detail}")
    click.echo(f"Recipe '{name}' pushed (version={version})")

    # 2. Register placeholder package entry via POST /v1/recipes/{name}/register
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{base}/v1/recipes/{name}/register",
            headers=headers,
            json={
                "version": full_version,
                "description": description,
                "homepage": homepage,
                "license": pkg_license,
                "maintainer": maintainer_field,
                "org_slug": org_slug,
            },
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        click.echo(f"cvcpkg: warning: placeholder registration failed: {detail}", err=True)
    else:
        click.echo(f"Recipe '{name}' registered in catalog (version={full_version})")


@recipe_group.command("pull")
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
    default="",
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
@click.option(
    "--output-dir",
    type=click.Path(),
    default="./recipes",
    help="Directory to extract the recipe into.",
)
def recipe_pull(name: str, server: str, token: str, org_slug: str, output_dir: str):
    """Download a recipe from the server.

    Downloads the recipe bundle and extracts it to --output-dir/<name>/.
    This lets you inspect or locally build a recipe from the server.

    \b
    Examples:
      cvcpkg recipe pull zlib
      cvcpkg recipe pull zlib --output-dir ./my-recipes
    """
    import tarfile

    import httpx

    base = server.rstrip("/")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, str] = {}
    if org_slug:
        params["org_slug"] = org_slug

    with httpx.Client(timeout=120) as client:
        resp = client.get(f"{base}/v1/recipes/{name}", headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"failed to download recipe '{name}': {detail}")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle_path = output / f"{name}.tar.gz"
    bundle_path.write_bytes(resp.content)

    with tarfile.open(bundle_path, "r:gz") as tar:
        safe_tar_extractall(tar, output)
    bundle_path.unlink()
    click.echo(f"Recipe '{name}' extracted to {output / name}")


@recipe_group.command("pull-all")
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
    default="",
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--org", "org_slug", default="", help="Organization scope (empty = base set).")
@click.option(
    "--output-dir",
    type=click.Path(),
    default="./recipes",
    help="Directory to extract recipes into.",
)
def recipe_pull_all(server: str, token: str, org_slug: str, output_dir: str):
    """Download the full recipe set from the server.

    Downloads all recipes as a single bundle and extracts them to
    --output-dir.  Use --org to download an organization's recipe set
    instead of the base set.

    \b
    Examples:
      cvcpkg recipe pull-all
      cvcpkg recipe pull-all --org my-org --output-dir ./org-recipes
    """
    import tarfile

    import httpx

    base = server.rstrip("/")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, str] = {}
    if org_slug:
        params["org_slug"] = org_slug

    click.echo(f"cvcpkg: downloading recipe set from {base} ...")
    with httpx.Client(timeout=300) as client:
        resp = client.get(f"{base}/v1/recipes/bundle", headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"failed to download recipe set: {detail}")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle_path = output / "recipes-bundle.tar.gz"
    bundle_path.write_bytes(resp.content)

    with tarfile.open(bundle_path, "r:gz") as tar:
        safe_tar_extractall(tar, output)
    bundle_path.unlink()

    # Count extracted recipes
    recipe_count = sum(1 for d in output.iterdir() if d.is_dir() and (d / "recipe.yaml").is_file())
    click.echo(f"cvcpkg: {recipe_count} recipes extracted to {output}")


@recipe_group.command("push-all")
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
@click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help="Extra recipes directory to overlay on the default.",
)
@click.option(
    "--no-default-recipes",
    is_flag=True,
    default=False,
    help="Ignore the auto-detected default recipes directory.",
)
@click.option("--org", "org_slug", default="", help="Organization scope.")
def recipe_push_all(
    server: str, token: str, recipes_dirs: tuple[str, ...], no_default_recipes: bool, org_slug: str
):
    """Push all recipes from the default + overlay directories to the server.

    Iterates every recipe in the bundled recipes directory (and any
    extra ``--recipes-dir`` overlays) and pushes each one to the
    server.  Later directories win on name conflicts.

    \b
    Examples:
      cvcpkg recipe push-all
      cvcpkg recipe push-all --recipes-dir ./my-extra-recipes
    """
    import io
    import tarfile

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

    # Merge recipes from all dirs (later dirs win on name conflicts).
    # For each recipe we track which rdir it came from so we can
    # bundle the correct _common/ sibling.
    recipe_map: dict[str, tuple[Path, Path]] = {}  # name -> (recipe_path, rdir)
    for rdir in rdirs:
        if not rdir.is_dir():
            continue
        for recipe_path in sorted(rdir.iterdir()):
            if not recipe_path.is_dir() or recipe_path.name.startswith(("_", ".")):
                continue
            if not (recipe_path / "recipe.yaml").is_file():
                continue
            recipe_map[recipe_path.name] = (recipe_path, rdir)

    pushed = 0
    failed = 0
    for name in sorted(recipe_map):
        recipe_path, rdir = recipe_map[name]
        recipe_yaml = recipe_path / "recipe.yaml"
        recipe_data = yaml.safe_load(recipe_yaml.read_text())
        recipe_info = recipe_data.get("recipe", {})
        version = recipe_info.get("upstream_version", "")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for f in sorted(recipe_path.rglob("*")):
                if f.is_file():
                    arcname = f"{name}/{f.relative_to(recipe_path)}"
                    tar.add(f, arcname=arcname)
            common_dir = rdir / "_common"
            if common_dir.is_dir():
                for f in sorted(common_dir.rglob("*")):
                    if f.is_file():
                        arcname = f"_common/{f.relative_to(common_dir)}"
                        tar.add(f, arcname=arcname)
            # Include vendored source directory if source.type == "vendored"
            _bundle_vendored_source(tar, recipe_path, rdir)
        buf.seek(0)

        url = f"{base}/v1/recipes/{name}"
        params = {"org_slug": org_slug, "version": version}
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    params=params,
                    files={"file": (f"{name}.tar.gz", buf, "application/gzip")},
                )
            if resp.status_code >= 400:
                click.echo(f"  {name}: failed ({resp.status_code})", err=True)
                failed += 1
            else:
                click.echo(f"  {name} (version={version})")
                pushed += 1
        except Exception as exc:
            click.echo(f"  {name}: error ({exc})", err=True)
            failed += 1

    click.echo(f"cvcpkg: pushed {pushed} recipes ({failed} failed)")
