# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg login`` and friends — interactive SSO sessions for humans.

These obtain and manage an opaque ``cvcses_`` session (see ``oauth_native.py``
and ``credentials.py``) so interactive users stop hand-cutting API tokens.
Machines keep using ``cvctok_`` tokens via ``--token`` / ``CVCPKG_TOKEN``, which
always win over a stored session.

The whole path is stdlib + click only: HTTP here is ``urllib.request`` so the
auth surface loads on Haiku/OpenBSD and in the single-binary build.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import urllib.error
import urllib.request

import click

from cvcpkg.cli import cli


def _server_default() -> str:
    from cvcpkg.config import default_server_url

    return default_server_url()


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: dict | None = None,
    timeout: float = 30.0,
):
    """A minimal stdlib HTTP call; returns ``(status, parsed_json_or_None)``."""
    data = json.dumps(json_body).encode() if json_body is not None else None
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except (ValueError, OSError):
            return exc.code, None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise click.ClickException(f"could not reach {url}: {exc}") from exc


_server_opt = click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default="",
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)


@cli.command("login")
@_server_opt
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher", "admin"]),
    default="",
    help="Requested role for the session (narrows to your entitlement; default: lowest).",
)
@click.option("--device", default="", help="A label for this device (default: hostname).")
@click.option(
    "--browser/--no-browser",
    default=True,
    help="Open a browser for the loopback flow (default). --no-browser forces pairing.",
)
@click.option("--code", is_flag=True, help="Force the headless device-pairing flow.")
@click.option("--port", type=int, default=0, help="Pin the loopback callback port (for ssh -L).")
@click.option("--timeout", type=float, default=300.0, help="Seconds to wait for approval.")
@click.option("--json", "as_json", is_flag=True, help="Print the resulting session as JSON.")
def login(server, role, device, browser, code, port, timeout, as_json):
    """Sign in to a cvcpkg server with your SSO identity.

    Desktop: opens your browser (loopback). Headless/SSH: prints a short code to
    approve in any browser (pairing). Use --code to force pairing, or --port with
    ``ssh -L`` to bring the loopback back to a remote box.

    CI should not use this — export CVCPKG_TOKEN with a machine token instead.
    """
    from cvcpkg import credentials, oauth_native

    server = (server or _server_default()).rstrip("/")
    use_loopback = port > 0 or (browser and not code and oauth_native.can_open_browser())
    try:
        if use_loopback:
            cred = oauth_native.loopback_login(
                server,
                role=role,
                device=device,
                port=port,
                open_browser=browser,
                timeout=timeout,
                printer=click.echo,
            )
        else:
            cred = oauth_native.pairing_login(
                server, role=role, device=device, timeout=max(timeout, 600.0), printer=click.echo
            )
    except oauth_native.LoginError as exc:
        raise click.ClickException(str(exc)) from exc

    host = credentials.host_of(server)
    credentials.save(host, cred)
    if as_json:
        click.echo(json.dumps(dataclasses.asdict(cred), indent=2))
    else:
        click.echo(f"\nSigned in to {host} as {cred.principal} ({cred.role}).")
        if cred.device:
            click.echo(f"  device:  {cred.device}")
        if cred.expires_at:
            click.echo(f"  expires: {cred.expires_at}")


@cli.command("logout")
@_server_opt
@click.option(
    "--all", "all_sessions", is_flag=True, help="Revoke every session for your principal."
)
@click.option(
    "--local-only", is_flag=True, help="Forget the local credential without calling the server."
)
def logout(server, all_sessions, local_only):
    """Sign out: revoke the session server-side and forget it locally."""
    from cvcpkg import credentials

    server = (server or _server_default()).rstrip("/")
    host = credentials.host_of(server)
    cred = credentials.get(host)
    if cred is None:
        click.echo(f"Not signed in to {host}.")
        return
    if not local_only and cred.access:
        target = (cred.server_url or server).rstrip("/")
        status, _ = _request(
            "POST",
            f"{target}/v1/auth/revoke",
            token=cred.access,
            json_body={"all": bool(all_sessions)},
        )
        if status not in (200, 204):
            click.echo(
                f"cvcpkg: server-side revoke returned {status}; clearing local credential anyway.",
                err=True,
            )
    credentials.logout_local(host)
    click.echo(f"Signed out of {host}.")


@cli.command("whoami")
@_server_opt
@click.option("--json", "as_json", is_flag=True, help="Print the identity as JSON.")
def whoami(server, as_json):
    """Show who you are signed in as, and your org memberships."""
    from cvcpkg.cli._helpers import resolve_token

    server = (server or _server_default()).rstrip("/")
    token = resolve_token("", server)
    if not token:
        raise click.ClickException("not signed in — run 'cvcpkg login'")
    status, data = _request("GET", f"{server}/v1/auth/whoami", token=token)
    if status == 401:
        raise click.ClickException("session expired — run 'cvcpkg login'")
    if status != 200 or data is None:
        raise click.ClickException(f"whoami failed ({status})")
    if as_json:
        click.echo(json.dumps(data, indent=2))
        return
    click.echo(f"{data.get('name', '?')}  ({data.get('role', '?')}, {data.get('kind', '?')})")
    if data.get("email"):
        click.echo(f"  email: {data['email']}")
    orgs = data.get("orgs") or []
    if orgs:
        click.echo("  orgs:")
        for o in orgs:
            click.echo(f"    {o.get('slug', '?')} ({o.get('role', '?')})")


@cli.group("auth")
def auth_group() -> None:
    """Manage your login sessions (devices, revocation, status)."""


@auth_group.command("devices")
@_server_opt
@click.option("--json", "as_json", is_flag=True)
def auth_devices(server, as_json):
    """List the active sessions (devices) for your principal."""
    from cvcpkg.cli._helpers import resolve_token

    server = (server or _server_default()).rstrip("/")
    token = resolve_token("", server)
    if not token:
        raise click.ClickException("not signed in — run 'cvcpkg login'")
    status, data = _request("GET", f"{server}/v1/auth/devices", token=token)
    if status == 401:
        raise click.ClickException("session expired — run 'cvcpkg login'")
    if status != 200 or data is None:
        raise click.ClickException(f"listing devices failed ({status})")
    devices = data.get("devices") or []
    if as_json:
        click.echo(json.dumps(devices, indent=2))
        return
    if not devices:
        click.echo("No active sessions.")
        return
    for d in devices:
        here = " (this session)" if d.get("current") else ""
        click.echo(
            f"  #{d.get('session_id')}  {d.get('device_label') or '(unnamed)':<24} "
            f"{d.get('ip_at_issue', ''):<15} expires={d.get('expires_at', '')}{here}"
        )


@auth_group.command("revoke")
@click.argument("session_id", type=int)
@_server_opt
def auth_revoke(session_id, server):
    """Revoke one session by id (from 'cvcpkg auth devices')."""
    from cvcpkg.cli._helpers import resolve_token

    server = (server or _server_default()).rstrip("/")
    token = resolve_token("", server)
    if not token:
        raise click.ClickException("not signed in — run 'cvcpkg login'")
    status, _ = _request("DELETE", f"{server}/v1/auth/devices/{session_id}", token=token)
    if status not in (200, 204):
        raise click.ClickException(f"revoke failed ({status})")
    click.echo(f"Revoked session #{session_id}.")


@auth_group.command("status")
@_server_opt
def auth_status(server):
    """Exit 0 iff a live (unexpired) credential exists for the server."""
    from cvcpkg import credentials

    server = (server or _server_default()).rstrip("/")
    host = credentials.host_of(server)
    token = credentials.token_for(host)
    if not token:
        click.echo(f"Not signed in to {host}.")
        sys.exit(1)
    cred = credentials.get(host)
    click.echo(f"Signed in to {host} as {cred.principal} ({cred.role}).")
