"""Alembic environment configuration for cvcpkg-server.

Reads ``CVCPKG_DATABASE_URL`` (the async URL) and converts it to a
synchronous URL for Alembic's offline and online migration modes.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from cvcpkg.server.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_sync_url() -> str:
    """Convert the async database URL to a synchronous one for Alembic."""
    url = os.environ.get("CVCPKG_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "CVCPKG_DATABASE_URL must be set for migrations "
            "(e.g. postgresql+asyncpg://user:pass@host/db)"
        )
    # asyncpg → psycopg2, aiosqlite → pysqlite (built-in)
    url = url.replace("+asyncpg", "+psycopg2")
    url = url.replace("+aiosqlite", "")
    url = url.replace("+aiomysql", "+pymysql")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a live DB."""
    context.configure(
        url=_get_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_sync_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
