"""Tests for the curated-tag feature — DB store and REST endpoints.

Regression coverage for the ``PUT /v1/tags/{name}`` happy path: the
``tags.updated_at`` column carries a SQL-side ``onupdate=func.now()``, so
after an UPDATE flushes SQLAlchemy expires the attribute and re-fetches it
on next access.  ``DbTagStore._row_to_info`` reads it synchronously, which
used to trigger a lazy load outside the async greenlet and raise
``sqlalchemy.exc.MissingGreenlet`` on the sqlite+aiosqlite backend, 500ing
the update endpoint.  The store now refreshes the value before reading it.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for tag tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and publisher tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


# ── DbTagStore unit tests ──────────────────────────────────────


class TestDbTagStore:
    """Direct tests for the DbTagStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "tag_store.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield

        async def _cleanup():
            await dispose_engine()

        asyncio.run(_cleanup())

    def _run(self, coro):
        return asyncio.run(coro)

    def test_create_and_update(self):
        from cvcpkg.server.db_stores import DbTagStore

        async def _test():
            store = DbTagStore()
            created = await store.create(
                name="scientific",
                display_name="Scientific",
                description="Science packages",
            )
            assert created.name == "scientific"
            assert created.display_name == "Scientific"

            # Regression: updating a row whose ``updated_at`` is refreshed by a
            # SQL-side ``onupdate`` must not raise MissingGreenlet.
            updated = await store.update(
                name="scientific",
                display_name="Scientific Computing",
            )
            assert updated is not None
            assert updated.display_name == "Scientific Computing"
            # Untouched fields survive; the refreshed timestamp is readable.
            assert updated.description == "Science packages"
            assert updated.updated_at is not None
            assert updated.updated_at >= created.created_at

        self._run(_test())

    def test_update_missing_returns_none(self):
        from cvcpkg.server.db_stores import DbTagStore

        async def _test():
            store = DbTagStore()
            result = await store.update(name="does-not-exist", display_name="x")
            assert result is None

        self._run(_test())

    def test_update_inside_atomic_session(self):
        from cvcpkg.server.db import atomic_session
        from cvcpkg.server.db_stores import DbTagStore

        async def _test():
            store = DbTagStore()
            await store.create(name="graphics", display_name="Graphics")
            # The update endpoint runs the store call inside an ambient
            # atomic_session (the audit unit of work); the lazy-load path
            # differs from a standalone session, so cover it explicitly.
            async with atomic_session():
                updated = await store.update(name="graphics", description="GPU libs")
            assert updated is not None
            assert updated.description == "GPU libs"
            assert updated.updated_at is not None

        self._run(_test())


# ── REST endpoint tests ────────────────────────────────────────


class TestTagEndpoints:
    """Tests for the /v1/tags endpoints against a DB-backed server."""

    def test_update_tag_happy_path(self, db_server_env):
        client, admin_tok, _pub_tok, _ = db_server_env
        auth = {"Authorization": f"Bearer {admin_tok}"}

        create = client.post(
            "/v1/tags",
            json={"name": "scientific", "display_name": "Scientific"},
            headers=auth,
        )
        assert create.status_code == 200, create.text

        resp = client.put(
            "/v1/tags/scientific",
            json={"display_name": "Scientific Computing", "description": "Sci pkgs"},
            headers=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "scientific"
        assert data["display_name"] == "Scientific Computing"
        assert data["description"] == "Sci pkgs"
        assert data["updated_at"]

    def test_update_tag_not_found(self, db_server_env):
        client, admin_tok, _pub_tok, _ = db_server_env
        resp = client.put(
            "/v1/tags/ghost",
            json={"display_name": "Ghost"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404

    def test_update_tag_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/tags",
            json={"name": "scientific", "display_name": "Scientific"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        resp = client.put(
            "/v1/tags/scientific",
            json={"display_name": "Nope"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403
