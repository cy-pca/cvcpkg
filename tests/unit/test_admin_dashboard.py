"""Tests for the /admin dashboard UI (Phase 3, increment 1).

Covers the session-cookie helpers, the login flow (admin-only), and the
server-rendered overview page.
"""

from __future__ import annotations

import asyncio

import pytest

from cvcpkg.server.admin_ui import (
    _SESSION_COOKIE,
    make_session_value,
    verify_session_value,
)

# ── Session cookie helpers (no server needed) ───────────────────


class TestSessionCookie:
    KEY = b"test-key"

    def test_roundtrip(self):
        val = make_session_value(self.KEY)
        assert verify_session_value(self.KEY, val)

    def test_wrong_key_rejected(self):
        val = make_session_value(self.KEY)
        assert not verify_session_value(b"other-key", val)

    def test_expired_rejected(self):
        val = make_session_value(self.KEY, now=0)  # expired long ago
        assert not verify_session_value(self.KEY, val)

    def test_garbage_rejected(self):
        for garbage in ("", "x", "123", "abc.def", "9999999999.zzz"):
            assert not verify_session_value(self.KEY, garbage)

    def test_tampered_expiry_rejected(self):
        val = make_session_value(self.KEY)
        exp, sig = val.split(".", 1)
        assert not verify_session_value(self.KEY, f"{int(exp) + 9999}.{sig}")


# ── Endpoint tests (server extras required) ─────────────────────

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.server.app import create_app  # noqa: E402
from cvcpkg.server.models import TokenRole  # noqa: E402


@pytest.fixture()
def admin_server(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbDownloadStore, DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("test-admin", TokenRole.admin)
        pub = await store.create("test-publisher", TokenRole.publisher)
        await DbDownloadStore().record(
            "zlib", "1.3.1", "linux", arch="x86_64", bytes_sent=2048, cvcpkg_version="2.0.0"
        )
        await dispose_engine()
        return admin, pub

    admin_token, pub_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token


class TestAdminDashboard:
    def test_unauthenticated_shows_login(self, admin_server):
        client, _admin, _pub = admin_server
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert "Admin API token" in r.text

    def test_login_rejects_bad_token(self, admin_server):
        client, _admin, _pub = admin_server
        r = client.post("/admin/login", data={"token": "cvctok_bogus"})
        assert r.status_code == 401
        assert "Invalid token" in r.text
        assert _SESSION_COOKIE not in r.cookies

    def test_login_rejects_non_admin(self, admin_server):
        client, _admin, pub = admin_server
        r = client.post("/admin/login", data={"token": pub})
        assert r.status_code == 401

    def test_login_sets_cookie_and_dashboard_renders(self, admin_server):
        client, admin, _pub = admin_server
        r = client.post("/admin/login", data={"token": admin}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin"
        assert _SESSION_COOKIE in r.cookies

        # Cookie is kept by the TestClient session; dashboard now renders.
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Overview" in r.text
        assert "Top packages" in r.text
        assert "zlib" in r.text  # the seeded download appears
        assert "2.0" in r.text  # client version column
        # The raw admin token never appears in the page or cookie.
        assert admin not in r.text

    def test_logout_clears_session(self, admin_server):
        client, admin, _pub = admin_server
        client.post("/admin/login", data={"token": admin})
        r = client.post("/admin/logout", follow_redirects=False)
        assert r.status_code == 303
        r = client.get("/admin")
        assert "Sign in" in r.text

    def test_forged_cookie_rejected(self, admin_server):
        client, _admin, _pub = admin_server
        client.cookies.set(_SESSION_COOKIE, "9999999999.deadbeef")
        r = client.get("/admin")
        assert "Sign in" in r.text
