# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import json

import click

from cvcpkg.cli import cli
from cvcpkg.cli._build import _auto_platform
from cvcpkg.cli._helpers import (
    _config_opt,
    _human_size,
    _link_opt,
    _platform_opt,
)

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
    if platform in ("wasm", "wasm-mt"):
        return "wasm32"
    from cvcpkg.platform import detect_arch

    return detect_arch()


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
        platforms = {"linux", "darwin", "windows", "freebsd", "wasm", "wasm-mt"}

    hashes: set[str] = set()
    for plat in platforms:
        for r in recipes:
            h = chain_hash(r, by_name, plat)
            if h:
                hashes.add(h)
    return hashes
