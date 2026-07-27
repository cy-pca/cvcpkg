# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import time
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._helpers import (
    _VALID_ARCHES,
    _config_opt,
    _human_size,
    _link_opt,
    _platform_opt,
)

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
    default="https://cvcpkg.org",
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
      cvcpkg download zlib --server https://cvcpkg.org -o ./dist
    """
    import shutil

    from cvcpkg.cache import default_cache_dir
    from cvcpkg.catalog import catalog_entries, fetch_catalog, load_catalog_from_file
    from cvcpkg.installer import download_bundle
    from cvcpkg.manifest import CatalogEntry, ComponentReq, parse_component_spec
    from cvcpkg.platform import detect_arch, detect_platform

    plat = platform if platform != "auto" else detect_platform()
    arc = arch if arch != "auto" else detect_arch()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    click.echo(f"cvcpkg: resolving for {plat}/{arc}/{config}/{link}")

    # Parse component specs: name, name==version, or org/name[==version].
    reqs: list[ComponentReq] = [parse_component_spec(c) for c in components]
    requested_org: dict[str, str] = {c.name: c.org for c in reqs if c.org}

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
        wanted_org = requested_org.get(e.name)
        if wanted_org and e.org != wanted_org:
            continue
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
