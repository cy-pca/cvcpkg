"""``cvcpkg search`` — full-text search against a cvcpkg server."""

from __future__ import annotations

import os as _os

import click

from cvcpkg.catalog import trust_mirror_default as _trust_mirror_default
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
    help="Bearer token (only required if the server enforces read auth).  [env: CVCPKG_TOKEN]",
)
@click.option("--platform", default="", help="Filter by platform.")
@click.option("--arch", default="", help="Filter by architecture.")
@click.option("--link", default="", help="Filter by link mode (shared/static).")
@click.option("--build-type", default="", help="Filter by build type (release/debug).")
@click.option("--release", default="", help="Filter by release tag ('live' for unreleased).")
@click.option("--org", default="", help="Filter by organization slug.")
@click.option("--tag", default="", help="Filter by a single tag name.")
@click.option("--include-yanked", is_flag=True, help="Include yanked packages in the results.")
@click.option(
    "--yanked-only",
    is_flag=True,
    help="Show ONLY yanked packages (implies --include-yanked). "
    "Useful for finding a bundle to unyank.",
)
@click.option("--limit", type=int, default=50, show_default=True, help="Page size (max 200).")
@click.option("--offset", type=int, default=0, show_default=True, help="Result offset.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print the raw JSON response instead of a formatted table.",
)
@click.option(
    "--trust-mirror/--no-trust-mirror",
    default=None,
    help=(
        "Accept a mirror's ruling over its upstream's. By default upstream "
        "is authoritative, so a bundle the upstream retired is skipped even "
        "if this mirror still serves it. --no-trust-mirror restores that "
        "default when CVCPKG_TRUST_MIRROR is set in the environment."
    ),
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
    yanked_only: bool,
    limit: int,
    offset: int,
    as_json: bool,
    trust_mirror: bool | None,
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
    if trust_mirror is not None:
        # Consulted by cvcpkg.catalog.trust_mirror_default(); set here so
        # every downstream resolution path sees it without threading a
        # parameter through each one.  Writing "0" on --no-trust-mirror is what
        # lets the flag override an inherited CVCPKG_TRUST_MIRROR=1.
        _os.environ["CVCPKG_TRUST_MIRROR"] = "1" if trust_mirror else "0"
    # search talks to /v1/search directly rather than going through
    # catalog_entries(), so the upstream-authoritative default has to be applied
    # here too -- otherwise search is the one command that still shows a
    # mirror's dissent as though upstream had never retired the bundle.
    _trust = _trust_mirror_default() if trust_mirror is None else trust_mirror
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
    # --yanked-only is a client-side filter over the results, so it needs the
    # yanked rows in the response.
    if include_yanked or yanked_only:
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
    if yanked_only:
        packages = [p for p in packages if p.get("yanked")]
    elif not _trust and not include_yanked:
        # Upstream retired these; the mirror serving them anyway is exactly the
        # dissent --trust-mirror exists to opt into.  --include-yanked is a
        # deliberate request to see retired builds, so it is left alone.
        packages = [p for p in packages if not p.get("upstream_yanked")]
    if not packages:
        click.echo("No yanked packages." if yanked_only else "No matching packages.")
        return

    # Whether to show the State column at all: only meaningful once yanked rows
    # can appear.  Without it, --include-yanked printed yanked and active bundles
    # as identical rows -- the whole reason a user could not tell which to unyank.
    show_state = include_yanked or yanked_only or any(p.get("yanked") for p in packages)

    if yanked_only:
        click.echo(f"{len(packages)} yanked build(s):")
    else:
        click.echo(
            f"{pkg_count} package(s), {total} build(s) match "
            f"(showing {len(packages)} starting at offset {offset}):"
        )
    click.echo()
    header = (
        f"{'Name':<24} {'Version':<20} {'Platform':<10} {'Arch':<8} "
        f"{'Link':<7} {'Build':<8} {'Size':>10}"
    )
    if show_state:
        header += f"  {'State':<8}"
    click.echo(header)
    click.echo("-" * (90 + (10 if show_state else 0)))
    any_yanked = False
    for p in packages:
        size = _human_size(int(p.get("size_bytes") or 0))
        row = (
            f"{(p.get('name') or ''):<24} "
            f"{(p.get('version') or ''):<20} "
            f"{(p.get('platform') or ''):<10} "
            f"{(p.get('arch') or ''):<8} "
            f"{(p.get('link') or ''):<7} "
            f"{(p.get('build_type') or ''):<8} "
            f"{size:>10}"
        )
        if show_state:
            yanked = bool(p.get("yanked"))
            any_yanked = any_yanked or yanked
            state = "yanked" if yanked else "active"
            yat = p.get("yanked_at")
            if yanked and yat:
                state = f"yanked {str(yat)[:10]}"  # date only
            row += f"  {state}"
        click.echo(row)

    if any_yanked and not as_json:
        # The stated purpose of revealing yanked bundles: give the operator the
        # command to restore one.  Emit a concrete example off the first yanked
        # row so it can be copy-pasted and narrowed.
        y = next(p for p in packages if p.get("yanked"))
        click.echo()
        click.echo("To restore a yanked bundle (admin token required):")
        click.echo(
            f"  cvcpkg unyank {y.get('name')} {y.get('version')} "
            f"--platform {y.get('platform')} --arch {y.get('arch')} "
            f"--config {y.get('build_type')} --link {y.get('link')}"
        )

    remaining = total - (offset + len(packages))
    if remaining > 0 and not yanked_only:
        click.echo()
        click.echo(f"{remaining} more result(s) available — rerun with --offset {offset + limit}")
