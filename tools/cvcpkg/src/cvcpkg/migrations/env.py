"""Alembic environment configuration for cvcpkg-server.

Uses the async engine directly so no synchronous driver (psycopg2)
is required — the same ``CVCPKG_DATABASE_URL`` used by the server
works for migrations.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from cvcpkg.server.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Return the database URL from the environment."""
    url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "CVCPKG_DATABASE_URL must be set for migrations "
            "(e.g. postgresql+asyncpg://user:pass@host/db)"
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a live DB."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Configure context with the given connection and run migrations."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live database using the async engine."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database connection (async)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
