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


# ── Management pages (increment 2) ──────────────────────────────


@pytest.fixture()
def manage_server(tmp_path, monkeypatch):
    """Admin server seeded with one package variant for management tests."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'manage.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex, DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("test-admin", TokenRole.admin)
        await DbPackageIndex().add_package(
            name="zlib",
            version="1.3.1",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="0" * 64,
            size_bytes=10,
            archive_url="/v1/download/zlib-1.3.1-linux-x86_64-release-shared.tar.zst",
        )
        await dispose_engine()
        return admin

    admin_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        client.post("/admin/login", data={"token": admin_token})
        yield client


class TestAdminPackagesPage:
    def test_requires_session(self, manage_server):
        # A fresh client without the login cookie sees the login page.
        anon = TestClient(manage_server.app)
        r = anon.get("/admin/packages")
        assert r.status_code == 200
        assert "Sign in" in r.text

    def test_lists_and_filters(self, manage_server):
        r = manage_server.get("/admin/packages")
        assert r.status_code == 200
        assert "zlib" in r.text
        r = manage_server.get("/admin/packages?q=nomatch")
        assert "no packages match" in r.text

    def test_yank_unyank_delete_cycle(self, manage_server):
        variant = {
            "name": "zlib",
            "version": "1.3.1",
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
        }
        r = manage_server.post(
            "/admin/packages/action",
            data={**variant, "action": "yank"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "yanked" in manage_server.get("/admin/packages").text

        r = manage_server.post(
            "/admin/packages/action",
            data={**variant, "action": "unyank"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        page = manage_server.get("/admin/packages").text
        assert ">unyank<" not in page  # back to the yank button

        r = manage_server.post(
            "/admin/packages/action",
            data={**variant, "action": "delete"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "no packages match" in manage_server.get("/admin/packages?q=zlib").text

    def test_action_without_session_403(self, manage_server):
        anon = TestClient(manage_server.app)
        r = anon.post(
            "/admin/packages/action",
            data={"action": "yank", "name": "zlib", "version": "1.3.1"},
        )
        assert r.status_code == 403

    def test_unknown_action_422(self, manage_server):
        r = manage_server.post(
            "/admin/packages/action",
            data={"action": "explode", "name": "zlib", "version": "1.3.1"},
        )
        assert r.status_code == 422


class TestAdminTokensPage:
    def test_create_shows_raw_once_and_revoke(self, manage_server):
        r = manage_server.post("/admin/tokens/create", data={"name": "ci-bot", "role": "publisher"})
        assert r.status_code == 200
        assert "ci-bot" in r.text
        assert "cvctok_" in r.text  # raw token shown once

        page = manage_server.get("/admin/tokens").text
        assert "ci-bot" in page
        assert "cvctok_" not in page  # never shown again

        r = manage_server.post(
            "/admin/tokens/revoke", data={"name": "ci-bot"}, follow_redirects=False
        )
        assert r.status_code == 303
        assert "revoked" in manage_server.get("/admin/tokens").text

    def test_duplicate_create_conflict(self, manage_server):
        manage_server.post("/admin/tokens/create", data={"name": "dup", "role": "reader"})
        r = manage_server.post("/admin/tokens/create", data={"name": "dup", "role": "reader"})
        assert r.status_code == 409
        assert "create failed" in r.text

    def test_bad_role_422(self, manage_server):
        r = manage_server.post("/admin/tokens/create", data={"name": "x", "role": "root"})
        assert r.status_code == 422


class TestAdminAuditPage:
    def test_entries_and_chain_verify(self, manage_server):
        # Generate an audited action first.
        manage_server.post("/admin/tokens/create", data={"name": "aud", "role": "reader"})
        r = manage_server.get("/admin/audit")
        assert r.status_code == 200
        assert "token_create" in r.text

        r = manage_server.get("/admin/audit?verify=1")
        assert "Audit chain intact" in r.text
