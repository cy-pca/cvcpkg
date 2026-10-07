"""Database backends for the build-job state-machine tests.

SQLite always runs.  Postgres runs when ``CVCPKG_TEST_POSTGRES_URL`` points at
a scratch database (``postgresql+asyncpg://user:pass@host:port/db``) -- the
production backend, and the one whose READ COMMITTED row locking the
conditional-UPDATE transitions are written for.  CI's ``Test (Postgres)`` job
sets it; locally, for example::

    docker run --rm -d --name cvcpkg-pg -p 127.0.0.1:55432:5432 \\
        -e POSTGRES_PASSWORD=pg --tmpfs /var/lib/postgresql/data postgres:16-alpine
    CVCPKG_TEST_POSTGRES_URL=postgresql+asyncpg://postgres:pg@127.0.0.1:55432/postgres \\
        pytest tests/unit/test_build_job_races.py tests/unit/test_builder_ws_job_auth.py

The Postgres database is WIPED (``DROP SCHEMA public CASCADE``) before every
test: never point it at anything but a throwaway database.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager

import pytest

PG_URL = os.environ.get("CVCPKG_TEST_POSTGRES_URL", "").strip()

BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(
            not PG_URL, reason="set CVCPKG_TEST_POSTGRES_URL to run against Postgres"
        ),
    ),
]


def backend_url(backend: str, tmp_path) -> str:
    if backend == "postgres":
        return PG_URL
    return f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"


async def fresh_schema(url: str) -> None:
    """init_db(*url*) and create every table on an empty schema."""
    from sqlalchemy import text

    from cvcpkg.server import db as dbmod

    dbmod.init_db(url)
    if url.startswith("postgresql"):
        async with dbmod._engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    await dbmod.create_tables()


def run_on(backend: str, tmp_path, monkeypatch, coro_fn):
    """Run ``await coro_fn()`` on a fresh *backend* database, in ONE event loop.

    One loop for init, test and dispose: asyncpg connections are bound to the
    loop that opened them, so the engine must not outlive it.
    """
    from cvcpkg.server.db import dispose_engine

    url = backend_url(backend, tmp_path)
    monkeypatch.setenv("CVCPKG_DATABASE_URL", url)

    async def _main():
        await fresh_schema(url)
        try:
            return await coro_fn()
        finally:
            await dispose_engine()

    return asyncio.run(_main())


@contextmanager
def server_on(backend: str, tmp_path, monkeypatch, tokens: dict[str, str]):
    """A TestClient app on a fresh *backend* database.

    *tokens* maps token name -> role name; yields ``(client, {name: raw})``.
    The background scheduler is slowed to once an hour so it never dispatches
    a job behind a test's back.
    """
    from fastapi.testclient import TestClient

    import cvcpkg.server.app as app_mod
    from cvcpkg.server.db import dispose_engine
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.models import TokenRole

    url = backend_url(backend, tmp_path)
    monkeypatch.setenv("CVCPKG_DATABASE_URL", url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_POPULATE_UPSTREAM", raising=False)
    monkeypatch.setattr(app_mod, "_SCHEDULER_INTERVAL", 3600)

    async def _seed():
        await fresh_schema(url)
        store = DbTokenStore(tmp_path)
        raw = {name: await store.create(name, TokenRole(role)) for name, role in tokens.items()}
        await dispose_engine()
        return raw

    raw = asyncio.run(_seed())
    app = app_mod.create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, raw
