"""CLI entry point for cvcpkg-server.

Usage::

    # Start the server
    cvcpkg-server run --state-dir ./server-data --port 8420

    # Token management
    cvcpkg-server token create --name ci-bot --role publisher
    cvcpkg-server token list
    cvcpkg-server token revoke --name ci-bot

    # Audit
    cvcpkg-server audit log
    cvcpkg-server audit verify
"""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
@click.version_option(prog_name="cvcpkg-server")
def server_cli() -> None:
    """cvcpkg-server — package server for libcvc-deps bundles."""


# ── run ─────────────────────────────────────────────────────────


@server_cli.command()
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Directory for server state (index, tokens, audit log, archives).",
)
@click.option("--host", default="0.0.0.0", help="Bind address.")
@click.option("--port", default=8420, type=int, help="Listen port.")
@click.option(
    "--storage",
    default="",
    help="Storage backend URI (default: file://<state-dir>).",
)
@click.option(
    "--require-auth-reads",
    is_flag=True,
    help="Require authentication even for read endpoints.",
)
@click.option("--workers", default=1, type=int, help="Number of uvicorn workers.")
@click.option(
    "--database-url",
    default="",
    envvar="CVCPKG_DATABASE_URL",
    help="PostgreSQL URL (e.g. postgresql+asyncpg://user:pass@host/db). "
    "Enables DB backend instead of YAML files.",
)
def run(
    state_dir: str,
    host: str,
    port: int,
    storage: str,
    require_auth_reads: bool,
    workers: int,
    database_url: str,
) -> None:
    """Start the cvcpkg package server."""
    import os

    os.environ["CVCPKG_SERVER_STATE_DIR"] = str(Path(state_dir).resolve())
    if storage:
        os.environ["CVCPKG_SERVER_STORAGE_URI"] = storage
    if require_auth_reads:
        os.environ["CVCPKG_SERVER_REQUIRE_AUTH_READS"] = "1"
    if database_url:
        os.environ["CVCPKG_DATABASE_URL"] = database_url

    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "uvicorn is required to run the server. "
            "Install it with: pip install 'cvcpkg[server]'"
        )

    click.echo(f"cvcpkg-server: starting on {host}:{port}")
    click.echo(f"cvcpkg-server: state directory: {Path(state_dir).resolve()}")
    if database_url:
        # Mask the password in log output
        import re

        masked = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", database_url)
        click.echo(f"cvcpkg-server: database: {masked}")
    else:
        click.echo("cvcpkg-server: backend: YAML files")
    click.echo(f"cvcpkg-server: docs at http://{host}:{port}/docs")

    uvicorn.run(
        "cvcpkg.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )


# ── token ───────────────────────────────────────────────────────


@server_cli.group()
def token() -> None:
    """Manage API tokens."""


@token.command("create")
@click.option("--name", required=True, help="Human-readable token name.")
@click.option(
    "--role",
    type=click.Choice(["reader", "publisher", "admin"], case_sensitive=False),
    default="publisher",
    help="Token role.",
)
@click.option(
    "--expires-in-days",
    type=int,
    default=None,
    help="Token expiry in days (omit for no expiry).",
)
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def token_create(name: str, role: str, expires_in_days: int | None, state_dir: str) -> None:
    """Create a new API token (prints the secret once)."""
    from cvcpkg.server.auth import TokenStore
    from cvcpkg.server.models import TokenRole

    store = TokenStore(Path(state_dir))
    raw = store.create(name=name, role=TokenRole(role), expires_in_days=expires_in_days)
    click.echo(f"Token created for '{name}' (role={role}):")
    click.echo(f"  {raw}")
    click.echo("Save this token — it will not be shown again.")


@token.command("list")
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def token_list(state_dir: str) -> None:
    """List all tokens (without secrets)."""
    from cvcpkg.server.auth import TokenStore

    store = TokenStore(Path(state_dir))
    tokens = store.list_tokens()
    if not tokens:
        click.echo("No tokens found.")
        return
    click.echo(f"{'Name':<20} {'Role':<12} {'Revoked':<8} {'Expires'}")
    click.echo("-" * 60)
    for t in tokens:
        exp = t.expires_at.isoformat() if t.expires_at else "never"
        rev = "yes" if t.revoked else "no"
        click.echo(f"{t.name:<20} {t.role.value:<12} {rev:<8} {exp}")


@token.command("revoke")
@click.option("--name", required=True, help="Name of the token to revoke.")
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def token_revoke(name: str, state_dir: str) -> None:
    """Revoke a token by name."""
    from cvcpkg.server.auth import TokenStore

    store = TokenStore(Path(state_dir))
    if store.revoke(name):
        click.echo(f"Token '{name}' revoked.")
    else:
        click.echo(f"Token '{name}' not found or already revoked.")


# ── audit ───────────────────────────────────────────────────────


@server_cli.group()
def audit() -> None:
    """Inspect the audit trail."""


@audit.command("log")
@click.option("--limit", default=20, type=int, help="Max entries to show.")
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def audit_log(limit: int, state_dir: str) -> None:
    """Show recent audit entries."""
    from cvcpkg.server.audit import AuditLog

    log = AuditLog(Path(state_dir))
    entries, total = log.entries(limit=limit)
    if not entries:
        click.echo("Audit log is empty.")
        return
    click.echo(f"Showing {len(entries)} of {total} entries:")
    for e in entries:
        click.echo(
            f"  [{e.id}] {e.timestamp.isoformat()} {e.action.value:<16} "
            f"actor={e.actor} target={e.target}"
        )
        if e.detail:
            click.echo(f"       {e.detail}")


@audit.command("verify")
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def audit_verify(state_dir: str) -> None:
    """Verify the integrity of the audit chain."""
    from cvcpkg.server.audit import AuditLog

    log = AuditLog(Path(state_dir))
    ok, message = log.verify_chain()
    if ok:
        click.echo(f"OK: {message}")
    else:
        click.echo(f"FAILED: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    server_cli()
