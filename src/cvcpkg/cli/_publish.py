"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml

from cvcpkg.cli import cli
from cvcpkg.cli._build import _auto_platform
from cvcpkg.cli._helpers import (
    _config_opt,
    _link_opt,
    _platform_opt,
    _validate_org_slug,
)
from cvcpkg.cli._server import _api_request

# ── publish ─────────────────────────────────────────────────────


@cli.command()
@click.argument("packages", nargs=-1, required=False)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default="",
    metavar="URL",
    help="cvcpkg-server URL (e.g. https://cvcpkg.org).  [env: CVCPKG_SERVER_URL]",
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
      cvcpkg publish zlib grpc --server https://cvcpkg.org --token cvctok_...
      # Publish all recipes found in dist/ to the server:
      cvcpkg publish --all --server https://cvcpkg.org --token cvctok_...
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
            "--token is required when publishing to a server (or set CVCPKG_TOKEN)."
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

        if _variant_exists(base, headers, name, version, plat, arch, build_type, link, org):
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

    archive_like_exts = {
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".tgz",
        ".tbz2",
        ".txz",
    }

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

        # If the argument looks like a file path (contains separators, is
        # absolute, or has an archive-like extension), report a clear missing
        # file error instead of treating it as a recipe name glob.
        looks_like_path = (
            p.is_absolute() or p.parent != Path(".") or p.suffix.lower() in archive_like_exts
        )
        if looks_like_path:
            raise click.ClickException(f"archive file not found: {p}")

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
    org: str,
) -> bool:
    """Check if this exact package variant already exists on the server.

    Only a live (non-yanked) bundle in the same org counts: a yanked
    artifact, or an identical variant under a different org, must not
    suppress the upload.
    """
    import httpx

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{base}/v1/packages/{name}",
                params={"platform": platform, "limit": 200, "org": org},
                headers=headers,
            )
        if resp.status_code != 200:
            return False
        for pkg in resp.json().get("packages", []):
            # The server treats org="" as "no filter" and older servers may
            # return yanked bundles, so both must be re-checked client-side.
            if pkg.get("yanked", False):
                continue
            if (
                pkg.get("org", "") == org
                and pkg.get("version") == version
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


# ── yank / unyank ───────────────────────────────────────────────
#
# The server has had /v1/packages/{name}/{version}/{yank,unyank} for a while
# with nothing in the CLI driving them, so the only way to retire a bad bundle
# was a hand-rolled curl -- easy to get wrong in the one direction that matters
# (see _scope_opts).


# Scope options are deliberately NOT the shared _platform_opt/_config_opt/
# _link_opt: those default to auto/release/shared, whereas the server treats an
# omitted scope field as "every variant of this version".  Reusing them would
# silently narrow every yank to one variant -- the opposite of what the operator
# typed.  Tri-state (None = unscoped) is the whole contract here.
def _scope_opts(fn):
    fn = click.option(
        "--link",
        type=click.Choice(["shared", "static"], case_sensitive=False),
        default=None,
        help="Only this link mode.  Omit to match every link mode.",
    )(fn)
    fn = click.option(
        "--config",
        "build_type",
        type=click.Choice(["release", "debug"], case_sensitive=False),
        default=None,
        help="Only this build configuration.  Omit to match both.",
    )(fn)
    fn = click.option(
        "--arch", default=None, metavar="ARCH", help="Only this arch.  Omit to match every arch."
    )(fn)
    fn = click.option(
        "--platform",
        default=None,
        metavar="PLATFORM",
        help="Only this platform.  Omit to match every platform.",
    )(fn)
    return fn


def _server_opts(fn):
    fn = click.option(
        "--token",
        envvar="CVCPKG_TOKEN",
        required=True,
        help="Bearer token.  [env: CVCPKG_TOKEN]",
    )(fn)
    fn = click.option(
        "--server",
        envvar="CVCPKG_SERVER_URL",
        required=True,
        metavar="URL",
        help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
    )(fn)
    return fn


def _matching_bundles(
    bundles: list[dict],
    version: str,
    platform: str | None,
    arch: str | None,
    build_type: str | None,
    link: str | None,
) -> list[dict]:
    """The bundles the server would act on, for previewing before we POST.

    Mirrors the server's filter exactly (app.py yank/unyank and
    db_stores.yank): exact match on version, plus equality on each scope field
    that was supplied.  If this drifts from the server the preview lies about
    what is about to happen, which is worse than no preview.
    """
    out = []
    for b in bundles:
        if b.get("version") != version:
            continue
        if platform is not None and b.get("platform") != platform:
            continue
        if arch is not None and b.get("arch") != arch:
            continue
        if link is not None and b.get("link") != link:
            continue
        if build_type is not None and b.get("build_type") != build_type:
            continue
        out.append(b)
    return out


def _variant(b: dict) -> str:
    return (
        f"{b.get('platform', '?')}/{b.get('arch', '?')}/"
        f"{b.get('build_type', '?')}/{b.get('link', '?')}"
    )


def _orphaned_variants(matched: list[dict], all_bundles: list[dict]) -> list[str]:
    """Variants left with no installable bundle once *matched* is yanked.

    Yanking the only bundle for a platform/arch/config/link makes that variant
    unresolvable for consumers.  That is sometimes exactly the intent, so this
    warns rather than refuses.
    """
    orphaned = []
    for b in matched:
        if b.get("yanked"):
            continue  # already yanked; nothing changes for this variant
        survivors = [
            o
            for o in all_bundles
            if _variant(o) == _variant(b)
            and o.get("version") != b.get("version")
            and not o.get("yanked")
        ]
        if not survivors:
            orphaned.append(_variant(b))
    return sorted(set(orphaned))


def _yank_impl(
    action: str,
    name: str,
    version: str,
    server: str,
    token: str,
    platform: str | None,
    arch: str | None,
    build_type: str | None,
    link: str | None,
    yes: bool,
) -> None:
    base = server.rstrip("/")
    # include_yanked so unyank can see its targets, and so yank can report a
    # target that is already yanked instead of silently "succeeding".
    listing = _api_request(
        "get", f"{base}/v1/packages/{name}", token, params={"include_yanked": "true"}
    )
    all_bundles = listing.get("packages", [])
    if not all_bundles:
        raise click.ClickException(f"no package named '{name}' on {base}")

    matched = _matching_bundles(all_bundles, version, platform, arch, build_type, link)
    if not matched:
        known = sorted({b.get("version", "") for b in all_bundles})
        raise click.ClickException(
            f"no bundle matches {name}=={version} with that scope.\n"
            f"  published versions: {', '.join(known)}\n"
            "  (the server would report success having changed nothing)"
        )

    want_yanked = action == "yank"
    changing = [b for b in matched if bool(b.get("yanked")) != want_yanked]

    click.echo(f"cvcpkg: {action} {name}=={version} on {base}")
    for b in sorted(matched, key=_variant):
        state = "yanked" if b.get("yanked") else "active"
        note = "" if bool(b.get("yanked")) != want_yanked else "  (already there, no change)"
        click.echo(f"  {_variant(b):38s} {state}{note}")

    if not changing:
        click.echo(f"cvcpkg: nothing to do -- all {len(matched)} bundle(s) already {action}ed.")
        return

    if want_yanked:
        for v in _orphaned_variants(changing, all_bundles):
            click.echo(
                f"  warning: {v} has no other active bundle -- "
                "consumers resolving that variant will find nothing",
                err=True,
            )

    if not yes:
        click.confirm(f"{action} {len(changing)} bundle(s)?", abort=True)

    params = {
        k: v
        for k, v in (
            ("platform", platform),
            ("arch", arch),
            ("link", link),
            ("build_type", build_type),
        )
        if v is not None
    }
    resp = _api_request(
        "post", f"{base}/v1/packages/{name}/{version}/{action}", token, params=params
    )
    click.echo(f"cvcpkg: {resp.get('message', action)} ({resp.get('count', 0)} bundle(s))")


@cli.command()
@click.argument("name")
@click.argument("version")
@_server_opts
@_scope_opts
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def yank(
    name: str,
    version: str,
    server: str,
    token: str,
    platform: str | None,
    arch: str | None,
    build_type: str | None,
    link: str | None,
    yes: bool,
) -> None:
    """Yank a published version so resolution stops selecting it.

    Use it to retire a bundle that builds but is broken.  The archive is not
    deleted and yank is reversible ('cvcpkg unyank'), but note this is stronger
    than cargo's yank: the catalog OMITS yanked bundles rather than flagging
    them, so a pin to a yanked version stops resolving too -- that install now
    builds from source (--fallback-to-source / --local) or fails.

    Scope flags narrow which variants are affected.  With none of them, EVERY
    variant of the version is yanked.

    Requires a publisher token (which may only yank its own packages, or its
    org's) or an admin token.

    \b
    Examples:
      # retire one broken variant
      cvcpkg yank readline 8.3+cvc.1 --platform linux --arch x86_64 \\
          --config release --link shared
      # retire a whole version, no prompt
      cvcpkg yank mypkg 1.2.3+cvc.1 --yes
    """
    _yank_impl("yank", name, version, server, token, platform, arch, build_type, link, yes)


@cli.command()
@click.argument("name")
@click.argument("version")
@_server_opts
@_scope_opts
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def unyank(
    name: str,
    version: str,
    server: str,
    token: str,
    platform: str | None,
    arch: str | None,
    build_type: str | None,
    link: str | None,
    yes: bool,
) -> None:
    """Restore a yanked version to the catalog.

    The inverse of 'cvcpkg yank'.  Unlike yank, this requires an ADMIN token --
    a publisher cannot un-retire even its own package.

    \b
    Example:
      cvcpkg unyank readline 8.3+cvc.1 --platform linux --arch x86_64
    """
    _yank_impl("unyank", name, version, server, token, platform, arch, build_type, link, yes)
