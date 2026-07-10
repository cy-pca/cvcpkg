"""Regression tests for the SQLite connection-pool configuration.

File-backed SQLite must use NullPool: the default AsyncAdaptedQueuePool
intermittently fails at shutdown with "sqlite3.OperationalError: no active
connection" when a task is cancelled mid-session (the pool later terminates
an aiosqlite connection whose worker thread is already gone). In-memory
SQLite must keep StaticPool so every connection shares the one database.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required")

from sqlalchemy.pool import NullPool, StaticPool

from cvcpkg.server import db as db_mod


def _pool_class_for(url: str) -> str:
    async def _go() -> str:
        db_mod.init_db(url)
        try:
            return type(db_mod._engine.pool).__name__
        finally:
            await db_mod.dispose_engine()

    return asyncio.run(_go())


def test_file_sqlite_uses_null_pool(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}"
    assert _pool_class_for(url) == NullPool.__name__


def test_memory_sqlite_uses_static_pool():
    assert _pool_class_for("sqlite+aiosqlite://") == StaticPool.__name__
