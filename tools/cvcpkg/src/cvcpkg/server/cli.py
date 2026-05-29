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
@click.option(
    "--log-json",
    is_flag=True,
    envvar="CVCPKG_LOG_JSON",
    help="Emit structured JSON log lines.",
)
@click.option(
    "--mirror-mode",
    is_flag=True,
    envvar="CVCPKG_MIRROR_MODE",
    help="Run as a read-only mirror that syncs from an upstream primary.",
)
@click.option(
    "--mirror-upstream",
    default="",
    envvar="CVCPKG_MIRROR_UPSTREAM",
    help="Upstream server URL to mirror from (required with --mirror-mode).",
)
@click.option(
    "--mirror-token",
    default="",
    envvar="CVCPKG_MIRROR_TOKEN",
    help="Bearer token for authenticating with the upstream server.",
)
@click.option(
    "--mirror-sync-interval",
    default=3600,
    type=int,
    envvar="CVCPKG_MIRROR_SYNC_INTERVAL",
    help="Seconds between catalog syncs from upstream.  [default: 3600]",
)
@click.option(
    "--registration-mode",
    type=click.Choice(["open", "admin-gated"], case_sensitive=False),
    default="open",
    envvar="CVCPKG_REGISTRATION_MODE",
    help="Registration policy: 'open' (anyone can register) or 'admin-gated' "
    "(requests require admin approval).  [default: open]",
)
def run(
    state_dir: str,
    host: str,
    port: int,
    storage: str,
    require_auth_reads: bool,
    workers: int,
    database_url: str,
    log_json: bool,
    mirror_mode: bool,
    mirror_upstream: str,
    mirror_token: str,
    mirror_sync_interval: int,
    registration_mode: str,
) -> None:
    """Start the cvcpkg package server."""
    import os

    if mirror_mode and not mirror_upstream:
        raise click.ClickException("--mirror-upstream is required when --mirror-mode is set.")

    os.environ["CVCPKG_SERVER_STATE_DIR"] = str(Path(state_dir).resolve())
    if storage:
        os.environ["CVCPKG_SERVER_STORAGE_URI"] = storage
    if require_auth_reads:
        os.environ["CVCPKG_SERVER_REQUIRE_AUTH_READS"] = "1"
    if database_url:
        os.environ["CVCPKG_DATABASE_URL"] = database_url
    if mirror_mode:
        os.environ["CVCPKG_MIRROR_MODE"] = "1"
    if mirror_upstream:
        os.environ["CVCPKG_MIRROR_UPSTREAM"] = mirror_upstream
    if mirror_token:
        os.environ["CVCPKG_MIRROR_TOKEN"] = mirror_token
    if mirror_sync_interval != 3600:
        os.environ["CVCPKG_MIRROR_SYNC_INTERVAL"] = str(mirror_sync_interval)
    if registration_mode != "open":
        os.environ["CVCPKG_REGISTRATION_MODE"] = registration_mode

    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "uvicorn is required to run the server. "
            "Install it with: pip install 'cvcpkg[server]'"
        ) from None

    click.echo(f"cvcpkg-server: starting on {host}:{port}")
    click.echo(f"cvcpkg-server: state directory: {Path(state_dir).resolve()}")
    if database_url:
        # Mask the password in log output
        import re

        masked = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", database_url)
        click.echo(f"cvcpkg-server: database: {masked}")
    else:
        click.echo("cvcpkg-server: backend: YAML files")
    if mirror_mode:
        click.echo(f"cvcpkg-server: MIRROR MODE — upstream: {mirror_upstream}")
    click.echo(f"cvcpkg-server: registration mode: {registration_mode}")
    click.echo(f"cvcpkg-server: docs at http://{host}:{port}/docs")

    log_config = None
    if log_json:
        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "cvcpkg.server.log_fmt.JsonFormatter",
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "cvcpkg.server": {"handlers": ["default"], "level": "INFO", "propagate": False},
            },
        }

    uvicorn.run(
        "cvcpkg.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level="info",
        log_config=log_config,
    )


# ── bootstrap ───────────────────────────────────────────────────


@server_cli.command()
@click.option(
    "--name",
    default="admin",
    help="Name for the initial admin token.  [default: admin]",
)
@click.option(
    "--email",
    default="",
    help="Email address for the initial admin.",
)
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def bootstrap(name: str, email: str, state_dir: str) -> None:
    """Create the initial admin token for a fresh server.

    This command will only succeed when no admin tokens exist yet.
    The generated token is printed exactly once — store it securely.
    """
    import asyncio
    import os

    from cvcpkg.server.models import TokenRole

    db_url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if db_url:
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _bootstrap():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(Path(state_dir))
            tokens = await store.list_tokens()
            admins = [t for t in tokens if t.role == TokenRole.admin and not t.revoked]
            if admins:
                raise click.ClickException(
                    f"An admin token already exists ('{admins[0].name}'). "
                    "Bootstrap is only for initial server setup."
                )
            raw = await store.create(name=name, role=TokenRole.admin, email=email)
            await dispose_engine()
            return raw

        raw = asyncio.run(_bootstrap())
    else:
        from cvcpkg.server.auth import TokenStore

        store = TokenStore(Path(state_dir))
        tokens = store.list_tokens()
        admins = [t for t in tokens if t.role == TokenRole.admin and not t.revoked]
        if admins:
            raise click.ClickException(
                f"An admin token already exists ('{admins[0].name}'). "
                "Bootstrap is only for initial server setup."
            )
        raw = store.create(name=name, role=TokenRole.admin, email=email)

    click.echo("=" * 60)
    click.echo("  ADMIN TOKEN CREATED — SAVE THIS NOW!")
    click.echo("=" * 60)
    click.echo()
    click.echo(f"  Name:  {name}")
    click.echo(f"  Role:  admin")
    click.echo(f"  Token: {raw}")
    click.echo()
    click.echo("  This token will NOT be shown again.")
    click.echo("  Store it in a password manager or secrets vault.")
    click.echo()
    click.echo("  To configure the CLI client:")
    click.echo(f"    cvcpkg config set token {raw}")
    click.echo("=" * 60)


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
@click.option("--email", default="", help="Email address for the token owner.")
@click.option(
    "--state-dir",
    type=click.Path(),
    default="./cvcpkg-server-data",
    help="Server state directory.",
)
def token_create(name: str, role: str, expires_in_days: int | None, email: str, state_dir: str) -> None:
    """Create a new API token (prints the secret once)."""
    import asyncio
    import os

    from cvcpkg.server.models import TokenRole

    db_url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if db_url:
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _create():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(Path(state_dir))
            raw = await store.create(
                name=name, role=TokenRole(role), expires_in_days=expires_in_days, email=email
            )
            await dispose_engine()
            return raw

        raw = asyncio.run(_create())
    else:
        from cvcpkg.server.auth import TokenStore

        store = TokenStore(Path(state_dir))
        raw = store.create(name=name, role=TokenRole(role), expires_in_days=expires_in_days, email=email)
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
    import asyncio
    import os

    db_url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if db_url:
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _list():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(Path(state_dir))
            tokens = await store.list_tokens()
            await dispose_engine()
            return tokens

        tokens = asyncio.run(_list())
    else:
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
    import asyncio
    import os

    db_url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if db_url:
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _revoke():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(Path(state_dir))
            result = await store.revoke(name)
            await dispose_engine()
            return result

        if asyncio.run(_revoke()):
            click.echo(f"Token '{name}' revoked.")
        else:
            click.echo(f"Token '{name}' not found or already revoked.")
    else:
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


# ── migrate ─────────────────────────────────────────────────────


@server_cli.group()
def migrate() -> None:
    """Database schema migrations (requires Alembic)."""


def _require_alembic():
    """Import alembic.command or raise a user-friendly error."""
    try:
        from alembic import command

        return command
    except ImportError:
        raise click.ClickException(
            "alembic is required for migrations. Install it with: pip install 'cvcpkg[db]'"
        ) from None


def _require_db_url():
    """Ensure CVCPKG_DATABASE_URL is set."""
    import os

    if not os.environ.get("CVCPKG_DATABASE_URL"):
        raise click.ClickException("CVCPKG_DATABASE_URL must be set for migrations.")


@migrate.command("upgrade")
@click.argument("revision", default="head")
def migrate_upgrade(revision: str) -> None:
    """Upgrade the database schema to REVISION (default: head)."""
    command = _require_alembic()
    _require_db_url()
    alembic_cfg = _alembic_config()
    command.upgrade(alembic_cfg, revision)
    click.echo(f"Upgraded to {revision}.")


@migrate.command("downgrade")
@click.argument("revision")
def migrate_downgrade(revision: str) -> None:
    """Downgrade the database schema to REVISION."""
    command = _require_alembic()
    _require_db_url()
    alembic_cfg = _alembic_config()
    command.downgrade(alembic_cfg, revision)
    click.echo(f"Downgraded to {revision}.")


@migrate.command("stamp")
@click.argument("revision")
def migrate_stamp(revision: str) -> None:
    """Stamp the alembic_version table with REVISION without running migrations."""
    command = _require_alembic()
    _require_db_url()
    alembic_cfg = _alembic_config()
    command.stamp(alembic_cfg, revision)
    click.echo(f"Stamped at {revision}.")


@migrate.command("current")
def migrate_current() -> None:
    """Show the current migration revision."""
    command = _require_alembic()
    _require_db_url()
    alembic_cfg = _alembic_config()
    command.current(alembic_cfg, verbose=True)


@migrate.command("history")
def migrate_history() -> None:
    """Show migration revision history."""
    command = _require_alembic()
    _require_db_url()
    alembic_cfg = _alembic_config()
    command.history(alembic_cfg, verbose=True)


def _alembic_config():
    """Build an Alembic Config pointing at our migrations."""
    from alembic.config import Config

    cfg_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not cfg_path.is_file():
        # Fallback: build config programmatically
        cfg = Config()
        cfg.set_main_option(
            "script_location", str(Path(__file__).resolve().parent.parent / "migrations")
        )
        return cfg
    return Config(str(cfg_path))


if __name__ == "__main__":
    server_cli()
