# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import click

from cvcpkg.cli import cli

# ── Webhook CLI commands ────────────────────────────────────────


@cli.group("webhook")
def webhook_group() -> None:
    """Manage server webhooks."""


@webhook_group.command("register")
@click.argument("url")
@click.option(
    "--event",
    "-e",
    "events",
    multiple=True,
    required=True,
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
    webhook_id: int,
    url: str | None,
    events: tuple[str, ...],
    active: bool | None,
    server: str,
    token: str,
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
