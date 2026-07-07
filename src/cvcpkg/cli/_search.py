"""``cvcpkg search`` — full-text search against a cvcpkg server."""

from __future__ import annotations

import click

from cvcpkg.cli import cli
from cvcpkg.cli._helpers import _human_size


@cli.command("search")
@click.argument("query", required=False, default="")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default="https://cvcpkg.org",
    show_default=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    default=None,
    help="Bearer token (only required if the server enforces read auth).  " "[env: CVCPKG_TOKEN]",
)
@click.option("--platform", default="", help="Filter by platform.")
@click.option("--arch", default="", help="Filter by architecture.")
@click.option("--link", default="", help="Filter by link mode (shared/static).")
@click.option("--build-type", default="", help="Filter by build type (release/debug).")
@click.option("--release", default="", help="Filter by release tag ('live' for unreleased).")
@click.option("--org", default="", help="Filter by organization slug.")
@click.option("--tag", default="", help="Filter by a single tag name.")
@click.option("--include-yanked", is_flag=True, help="Include yanked packages.")
@click.option("--limit", type=int, default=50, show_default=True, help="Page size (max 200).")
@click.option("--offset", type=int, default=0, show_default=True, help="Result offset.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print the raw JSON response instead of a formatted table.",
)
def search(
    query: str,
    server: str,
    token: str | None,
    platform: str,
    arch: str,
    link: str,
    build_type: str,
    release: str,
    org: str,
    tag: str,
    include_yanked: bool,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    """Search the cvcpkg catalog by name, tag, description, and more.

    QUERY is a substring matched case-insensitively across the package
    name, version, platform, architecture, description, tags, maintainer,
    license, and release tag.  All filter flags are ANDed with the query.

    \b
    Examples:
      cvcpkg search boost
      cvcpkg search --platform linux --tag scientific
      cvcpkg search fft --link static --release v1.3.0
    """
    import json as _json

    import httpx

    params: dict[str, str | int | bool] = {
        "limit": limit,
        "offset": offset,
        "facets": True,
    }
    if query:
        params["q"] = query
    if platform:
        params["platform"] = platform
    if arch:
        params["arch"] = arch
    if link:
        params["link"] = link
    if build_type:
        params["build_type"] = build_type
    if release:
        params["release"] = release
    if org:
        params["org"] = org
    if tag:
        params["tag"] = tag
    if include_yanked:
        params["include_yanked"] = True

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{server.rstrip('/')}/v1/search"
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

    if as_json:
        click.echo(_json.dumps(data, indent=2, sort_keys=True))
        return

    total = int(data.get("total") or 0)
    pkg_count = int(data.get("package_count") or 0)
    packages = data.get("packages") or []
    if not packages:
        click.echo("No matching packages.")
        return

    click.echo(
        f"{pkg_count} package(s), {total} build(s) match "
        f"(showing {len(packages)} starting at offset {offset}):"
    )
    click.echo()
    click.echo(
        f"{'Name':<24} {'Version':<20} {'Platform':<10} {'Arch':<8} "
        f"{'Link':<7} {'Build':<8} {'Size':>10}"
    )
    click.echo("-" * 90)
    for p in packages:
        size = _human_size(int(p.get("size_bytes") or 0))
        click.echo(
            f"{(p.get('name') or ''):<24} "
            f"{(p.get('version') or ''):<20} "
            f"{(p.get('platform') or ''):<10} "
            f"{(p.get('arch') or ''):<8} "
            f"{(p.get('link') or ''):<7} "
            f"{(p.get('build_type') or ''):<8} "
            f"{size:>10}"
        )

    remaining = total - (offset + len(packages))
    if remaining > 0:
        click.echo()
        click.echo(
            f"{remaining} more result(s) available " f"— rerun with --offset {offset + limit}"
        )
