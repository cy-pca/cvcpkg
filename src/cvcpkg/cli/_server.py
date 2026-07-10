"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import click

from cvcpkg.cli import cli

# ── remote token management (client → server API) ──────────────


def _api_request(method: str, url: str, token: str, **kwargs):
    """Make an authenticated HTTP request; return parsed JSON or raise."""
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        resp = getattr(client, method)(url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    return resp.json()


@cli.group("token")
def token_group() -> None:
    """Manage server API tokens (requires admin token).

    These commands talk to the running cvcpkg-server via its REST API,
    so mutations go through the same code path as normal requests —
    no direct database access, no race conditions.
    """


@token_group.command("create")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Name for the new token.")
@click.option(
    "--role",
    required=True,
    type=click.Choice(["reader", "publisher", "admin"]),
    help="Role to assign.",
)
@click.option("--expires-in-days", type=int, default=None, help="Optional expiry in days.")
def token_create(server: str, token: str, name: str, role: str, expires_in_days: int | None):
    """Create a new API token on the server."""
    body: dict = {"name": name, "role": role}
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    data = _api_request("post", f"{server.rstrip('/')}/v1/tokens", token, json=body)
    click.echo(f"Created token '{data['name']}' (role: {data['role']})")
    click.echo(f"  Token: {data['token']}")
    click.echo("  ⚠ Store this token securely — it will not be shown again.")
    if data.get("expires_at"):
        click.echo(f"  Expires: {data['expires_at']}")


@token_group.command("list")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
def token_list(server: str, token: str):
    """List all API tokens on the server."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/tokens", token)
    tokens = data.get("tokens", [])
    if not tokens:
        click.echo("No tokens found.")
        return
    for t in tokens:
        status = " [REVOKED]" if t.get("revoked") else ""
        expires = f"  expires={t['expires_at']}" if t.get("expires_at") else ""
        click.echo(f"  {t['name']:<24} role={t['role']:<12}{expires}{status}")


@token_group.command("revoke")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Name of the token to revoke.")
def token_revoke(server: str, token: str, name: str):
    """Revoke an API token on the server."""
    _api_request("delete", f"{server.rstrip('/')}/v1/tokens/{name}", token)
    click.echo(f"Revoked token '{name}'.")


@token_group.command("set-email")
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
@click.option("--name", required=True, help="Token name to update.")
@click.option("--email", required=True, help="New email address.")
def token_set_email(server: str, token: str, name: str, email: str):
    """Set the email address on a token.

    Admins can update any token's email.  Non-admin users can only
    update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/email",
        token,
        json={"email": email},
    )
    click.echo(f"Email for '{name}' updated to '{email}'.")


@token_group.command("requests")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "denied"]),
    default=None,
    help="Filter by status (default: all).",
)
def token_requests(server: str, token: str, status: str | None):
    """List token registration requests."""
    url = f"{server.rstrip('/')}/v1/token-requests"
    params = {}
    if status:
        params["status"] = status
    data = _api_request("get", url, token, params=params)
    requests = data.get("requests", [])
    if not requests:
        click.echo("No token requests found.")
        return
    click.echo(f"{'ID':<6} {'Name':<20} {'Email':<30} {'Role':<12} {'Status':<10}")
    click.echo("-" * 78)
    for r in requests:
        click.echo(
            f"{r['id']:<6} {r['name']:<20} {r['email']:<30} {r['role']:<12} {r['status']:<10}"
        )


@token_group.command("approve")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.argument("request_id", type=int)
def token_approve(server: str, token: str, request_id: int):
    """Approve a pending token registration request."""
    url = f"{server.rstrip('/')}/v1/token-requests/{request_id}/approve"
    data = _api_request("post", url, token)
    click.echo(data.get("message", "approved"))
    raw = data.get("token")
    if raw:
        click.echo(f"  Token: {raw}")
        click.echo("  ⚠ Send this token to the requester — it will not be shown again.")


@token_group.command("deny")
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
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.argument("request_id", type=int)
def token_deny(server: str, token: str, request_id: int):
    """Deny a pending token registration request."""
    url = f"{server.rstrip('/')}/v1/token-requests/{request_id}/deny"
    data = _api_request("post", url, token)
    click.echo(data.get("message", "denied"))


@token_group.command("set-description")
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
@click.option("--name", required=True, help="Token name to update.")
@click.option("--description", required=True, help="New description.")
def token_set_description(server: str, token: str, name: str, description: str):
    """Set the description on a token.

    Admins can update any token's description.  Non-admin users can
    only update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/profile",
        token,
        json={"description": description},
    )
    click.echo(f"Description for '{name}' updated.")


@token_group.command("set-metadata")
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
@click.option("--name", required=True, help="Token name to update.")
@click.option("--metadata", required=True, help="New metadata (JSON or arbitrary text).")
def token_set_metadata(server: str, token: str, name: str, metadata: str):
    """Set the metadata on a token.

    Admins can update any token's metadata.  Non-admin users can
    only update their own.
    """
    _api_request(
        "patch",
        f"{server.rstrip('/')}/v1/tokens/{name}/profile",
        token,
        json={"metadata": metadata},
    )
    click.echo(f"Metadata for '{name}' updated.")


# ── user profile lookup (client → server API) ──────────────────


@cli.group("user")
def user_group() -> None:
    """Look up user profiles on the server."""


@user_group.command("info")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.argument("name")
def user_info(server: str, name: str):
    """Look up a user's public profile by name."""
    import httpx

    url = f"{server.rstrip('/')}/v1/users/{name}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        raise click.ClickException(f"user '{name}' not found")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"  Name:        {data['name']}")
    click.echo(f"  Role:        {data['role']}")
    click.echo(f"  Email:       {data.get('email', '')}")
    click.echo(f"  Description: {data.get('description', '')}")
    if data.get("metadata"):
        click.echo(f"  Metadata:    {data['metadata']}")
    click.echo(f"  Packages:    {data.get('packages_published', 0)}")
    click.echo(f"  Created:     {data.get('created_at', '')}")


@user_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option("--name", default="", help="Filter by username (substring match).")
@click.option("--email", default="", help="Filter by email (substring match).")
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher", "admin"]),
    default=None,
    help="Filter by role.",
)
@click.option("--org", default="", help="Filter by organization membership.")
@click.option(
    "--has-published",
    is_flag=True,
    default=False,
    help="Only show users who have published packages.",
)
@click.option(
    "--sort",
    type=click.Choice(["name", "email", "packages_published"]),
    default="name",
    help="Sort field.  [default: name]",
)
@click.option(
    "--order",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    help="Sort order.  [default: asc]",
)
@click.option("--limit", type=int, default=100, help="Results per page.  [default: 100]")
@click.option("--offset", type=int, default=0, help="Pagination offset.  [default: 0]")
def user_list(
    server: str,
    name: str,
    email: str,
    role: str | None,
    org: str,
    has_published: bool,
    sort: str,
    order: str,
    limit: int,
    offset: int,
):
    """List user identities with pagination and filtering."""
    import httpx

    params: dict = {"limit": limit, "offset": offset, "sort": sort, "order": order}
    if name:
        params["name"] = name
    if email:
        params["email"] = email
    if role:
        params["role"] = role
    if org:
        params["org"] = org
    if has_published:
        params["has_published"] = "true"

    url = f"{server.rstrip('/')}/v1/users"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    users = data.get("users", [])
    total = data.get("total", 0)
    if not users:
        click.echo("No users found.")
        return
    click.echo(f"Showing {len(users)} of {total} users:")
    click.echo(f"  {'Name':<24} {'Role':<12} {'Email':<30} {'Pkgs':<6}")
    click.echo("  " + "-" * 72)
    for u in users:
        click.echo(
            f"  {u['name']:<24} {u['role']:<12} {u.get('email', ''):<30} "
            f"{u.get('packages_published', 0):<6}"
        )


@user_group.command("by-email")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.argument("email")
def user_by_email(server: str, email: str):
    """Look up a user's profile by email address."""
    import httpx

    url = f"{server.rstrip('/')}/v1/users/by-email/{email}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        raise click.ClickException(f"no user with email '{email}' found")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(f"  Name:        {data['name']}")
    click.echo(f"  Role:        {data['role']}")
    click.echo(f"  Email:       {data.get('email', '')}")
    click.echo(f"  Description: {data.get('description', '')}")
    if data.get("metadata"):
        click.echo(f"  Metadata:    {data['metadata']}")
    click.echo(f"  Packages:    {data.get('packages_published', 0)}")
    click.echo(f"  Created:     {data.get('created_at', '')}")


# ── self-service registration (client → server API) ────────────


@cli.command("register")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option("--name", required=True, help="Desired token name / identity.")
@click.option("--email", required=True, help="Email address.")
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher"]),
    default="reader",
    help="Requested role.  [default: reader]",
)
@click.option("--description", default="", help="User description (unicode).")
@click.option("--metadata", default="", help="Arbitrary JSON or text metadata.")
def register_cmd(server: str, name: str, email: str, role: str, description: str, metadata: str):
    """Register for an API token on a cvcpkg-server.

    Depending on the server's registration mode, you will either
    receive a token immediately (open mode) or your request will be
    queued for admin approval (admin-gated mode).
    """
    import httpx

    url = f"{server.rstrip('/')}/v1/register"
    body: dict = {"name": name, "email": email, "role": role}
    if description:
        body["description"] = description
    if metadata:
        body["metadata"] = metadata
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    click.echo(data.get("message", "done"))
    token_value = data.get("token")
    if token_value:
        click.echo(f"  Token: {token_value}")
        click.echo("  ⚠ Save this token — it will not be shown again.")
        click.echo(f"  Configure your client: cvcpkg config set token {token_value}")
    request_id = data.get("request_id")
    if request_id:
        click.echo(f"  Request ID: {request_id}")
        click.echo("  You will be notified when an admin reviews your request.")


# ── remote server management (client → server API) ─────────────


@cli.group("server")
def server_group() -> None:
    """Remote server management commands (requires admin token)."""


@server_group.command("stop")
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
    metavar="TOKEN",
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
@click.confirmation_option(prompt="Are you sure you want to shut down the server?")
def server_stop(server: str, token: str):
    """Gracefully shut down the remote cvcpkg-server (requires admin token)."""
    import httpx

    url = f"{server.rstrip('/')}/v1/admin/shutdown"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 403:
        raise click.ClickException("permission denied — admin token required")
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    click.echo("Server shutdown initiated.")


@server_group.command("status")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
def server_status(server: str):
    """Check the status of the remote cvcpkg-server."""
    import httpx

    url = f"{server.rstrip('/')}/healthz"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
    except httpx.ConnectError as exc:
        raise click.ClickException(f"cannot connect to {server}") from exc
    if resp.status_code != 200:
        raise click.ClickException(f"server returned {resp.status_code}")
    data = resp.json()
    click.echo(f"  Status:     {data.get('status', 'ok')}")
    click.echo(f"  Version:    {data.get('version', '?')}")
    click.echo(f"  Packages:   {data.get('packages_count', '?')}")
    click.echo(f"  Uptime:     {data.get('uptime_seconds', '?')}s")
    click.echo(f"  Mirror:     {data.get('mirror_mode', False)}")


def _human_bytes(n) -> str:
    """Format a byte count as a human-readable string."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


@server_group.command("stats")
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
    metavar="TOKEN",
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
def server_stats(server: str, token: str):
    """Show server resource usage and catalog statistics (admin token)."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/admin/stats", token)

    click.echo("Server statistics")
    click.echo(f"  Version:            {data.get('version', '?')}")
    click.echo(f"  Uptime:             {data.get('uptime_seconds', '?')}s")
    click.echo(f"  Storage scheme:     {data.get('storage_scheme', '?')}")
    click.echo(f"  Mirror mode:        {data.get('mirror_mode', False)}")
    backend = data.get("database_backend") or ("enabled" if data.get("database_enabled") else "n/a")
    click.echo(f"  Database:           {backend}")
    click.echo(f"  Packages:           {data.get('packages_count', '?')}")
    if "total_storage_bytes" in data:
        click.echo(f"  Package storage:    {_human_bytes(data['total_storage_bytes'])}")
    if "orgs_count" in data:
        click.echo(f"  Organizations:      {data['orgs_count']}")
    if "builders_count" in data:
        click.echo(
            f"  Builders:           {data['builders_count']}"
            f" ({data.get('builders_connected', 0)} connected)"
        )
    if "build_jobs_count" in data:
        click.echo(f"  Build jobs:         {data['build_jobs_count']}")
    if "audit_entries" in data:
        click.echo(f"  Audit entries:      {data['audit_entries']}")


@server_group.command("backup")
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
    metavar="TOKEN",
    help="Admin bearer token.  [env: CVCPKG_TOKEN]",
)
def server_backup(server: str, token: str):
    """Trigger a server-side database backup (admin token).

    The backup is written on the server host under its state directory;
    the resulting path and size are reported here.
    """
    data = _api_request("post", f"{server.rstrip('/')}/v1/admin/backup", token)
    click.echo("Backup complete.")
    click.echo(f"  Backend:  {data.get('backend', '?')}")
    click.echo(f"  Path:     {data.get('path', '?')}")
    click.echo(f"  Size:     {_human_bytes(data.get('size_bytes', 0))}")


# ── remote org member management (client → server API) ─────────


@cli.group("org")
def org_group() -> None:
    """Manage organizations on the server.

    Organization owners can add/remove members to control who can
    publish to the organization's namespace — without affecting the
    member's access to anything else.
    """


@org_group.command("members")
@click.argument("slug")
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
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
def org_members(slug: str, server: str, token: str):
    """List members of an organization."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/orgs/{slug}", token)
    members = data.get("members", [])
    if not members:
        click.echo(f"Organization '{slug}' has no members.")
        return
    click.echo(f"Members of '{slug}':")
    for m in members:
        click.echo(f"  {m['token_name']:<24} role={m['role']}")


@org_group.command("add-member")
@click.argument("slug")
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
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to add as member.")
@click.option(
    "--role",
    type=click.Choice(["owner", "member"]),
    default="member",
    help="Org-level role (default: member).",
)
def org_add_member(slug: str, server: str, token: str, name: str, role: str):
    """Add a member to an organization."""
    url = f"{server.rstrip('/')}/v1/orgs/{slug}/members"
    _api_request("post", url, token, params={"token_name": name, "role": role})
    click.echo(f"Added '{name}' to '{slug}' as {role}.")


@org_group.command("remove-member")
@click.argument("slug")
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
    help="Bearer token (org owner or admin).  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Token name to remove.")
def org_remove_member(slug: str, server: str, token: str, name: str):
    """Remove a member from an organization.

    This revokes access to the organization's packages without
    affecting the member's global token or access to other orgs.
    """
    url = f"{server.rstrip('/')}/v1/orgs/{slug}/members/{name}"
    _api_request("delete", url, token)
    click.echo(f"Removed '{name}' from '{slug}'.")
