"""Tests for the mirror feature — models, DB store, and REST endpoints."""

from __future__ import annotations

import asyncio
import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for mirror tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import AuditAction, TokenRole

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


# ── DbMirrorStore unit tests ───────────────────────────────────


class TestDbMirrorStore:
    """Direct tests for the DbMirrorStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "mirror_store.db"
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

    def test_register_and_list(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            info = await store.register("https://m1.example.com", "Mirror 1", "ops@m1.com")
            assert info.url == "https://m1.example.com"
            assert info.display_name == "Mirror 1"
            assert info.healthy is True

            healthy = await store.list_healthy()
            assert len(healthy) == 1
            assert healthy[0].url == "https://m1.example.com"

        self._run(_test())

    def test_register_duplicate_clears_rejection(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")
            await store.reject("https://m1.example.com", "admin")

            info = await store.get("https://m1.example.com")
            assert info.rejected is True

            # Re-register should clear rejection
            info2 = await store.register("https://m1.example.com", "Mirror 1 reborn")
            assert info2.rejected is False
            assert info2.healthy is True

        self._run(_test())

    def test_reject(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")
            found = await store.reject("https://m1.example.com", "admin")
            assert found is True

            # Should not appear in healthy list
            healthy = await store.list_healthy()
            assert len(healthy) == 0

            # Should appear in full list
            all_mirrors = await store.list_all()
            assert len(all_mirrors) == 1
            assert all_mirrors[0].rejected is True

        self._run(_test())

    def test_reject_not_found(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            found = await store.reject("https://nonexistent.com", "admin")
            assert found is False

        self._run(_test())

    def test_remove(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")
            removed = await store.remove("https://m1.example.com")
            assert removed is True

            all_mirrors = await store.list_all()
            assert len(all_mirrors) == 0

        self._run(_test())

    def test_remove_not_found(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            removed = await store.remove("https://nonexistent.com")
            assert removed is False

        self._run(_test())

    def test_record_health_check_healthy(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")
            info = await store.record_health_check("https://m1.example.com", healthy=True)
            assert info is not None
            assert info.healthy is True
            assert info.consecutive_failures == 0

        self._run(_test())

    def test_record_health_check_failures_mark_unhealthy(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")

            # 3 consecutive failures should mark unhealthy
            for i in range(3):
                info = await store.record_health_check("https://m1.example.com", healthy=False)

            assert info.healthy is False
            assert info.consecutive_failures == 3

            # Should not appear in healthy list
            healthy = await store.list_healthy()
            assert len(healthy) == 0

        self._run(_test())

    def test_health_check_recovery(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")

            # Fail twice (not enough to go unhealthy)
            await store.record_health_check("https://m1.example.com", healthy=False)
            await store.record_health_check("https://m1.example.com", healthy=False)

            # Recover
            info = await store.record_health_check("https://m1.example.com", healthy=True)
            assert info.healthy is True
            assert info.consecutive_failures == 0

        self._run(_test())

    def test_update_packages_count(self):
        from cvcpkg.server.db_stores import DbMirrorStore

        async def _test():
            store = DbMirrorStore()
            await store.register("https://m1.example.com")
            await store.update_packages_count("https://m1.example.com", 42)

            info = await store.get("https://m1.example.com")
            assert info.packages_count == 42

        self._run(_test())


# ── Mirror endpoint tests ──────────────────────────────────────


class TestMirrorRegister:
    def test_register_mirror(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={
                "url": "https://mirror1.example.com",
                "display_name": "EU Mirror",
                "contact": "ops@mirror1.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://mirror1.example.com"
        assert data["display_name"] == "EU Mirror"
        assert data["healthy"] is True

    def test_register_invalid_url_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "ftp://bad.example.com"},
        )
        assert resp.status_code == 422

    def test_register_re_registration_clears_rejection(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        # Register
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        # Reject it
        client.post(
            "/v1/mirrors/reject",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        # Re-register
        resp = client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["rejected"] is False


class TestMirrorList:
    def test_list_healthy_mirrors(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        # Register two mirrors
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m2.example.com"},
        )

        resp = client.get("/v1/mirrors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        urls = {m["url"] for m in data["mirrors"]}
        assert urls == {"https://m1.example.com", "https://m2.example.com"}

    def test_list_all_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        # Without auth
        resp = client.get("/v1/mirrors/all")
        assert resp.status_code in (401, 403)

        # With publisher token — should be forbidden
        resp = client.get(
            "/v1/mirrors/all",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

        # With admin token
        resp = client.get(
            "/v1/mirrors/all",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

    def test_empty_mirror_list(self, db_server_env):
        client, *_ = db_server_env
        resp = client.get("/v1/mirrors")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestMirrorReject:
    def test_reject_mirror(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )

        resp = client.post(
            "/v1/mirrors/reject",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        # Verify it's no longer in healthy list
        resp = client.get("/v1/mirrors")
        assert resp.json()["total"] == 0

    def test_reject_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        resp = client.post(
            "/v1/mirrors/reject",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_reject_not_found(self, db_server_env):
        client, admin_tok, *_ = db_server_env
        resp = client.post(
            "/v1/mirrors/reject",
            params={"url": "https://nonexistent.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404


class TestMirrorRemove:
    def test_remove_mirror(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )

        resp = client.delete(
            "/v1/mirrors",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        # Verify it's gone from all lists
        resp = client.get(
            "/v1/mirrors/all",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.json()["total"] == 0

    def test_remove_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        resp = client.delete(
            "/v1/mirrors",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_remove_not_found(self, db_server_env):
        client, admin_tok, *_ = db_server_env
        resp = client.delete(
            "/v1/mirrors",
            params={"url": "https://nonexistent.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404


class TestMirrorDownloadProxy:
    def test_download_proxy_not_in_primary_mode(self, db_server_env):
        """Mirror download proxy should return 404 when not in mirror mode."""
        client, *_ = db_server_env
        resp = client.get("/v1/mirror/download/test.tar.gz")
        assert resp.status_code == 404


class TestHealthzMirrorMode:
    def test_healthz_includes_mirror_mode(self, db_server_env):
        client, *_ = db_server_env
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "mirror_mode" in data
        assert data["mirror_mode"] is False


class TestMirrorModeGuards:
    """Verify that publish and upload are blocked in mirror mode."""

    def test_publish_blocked_in_mirror_mode(self, tmp_path, monkeypatch):
        """Publish should return 403 when CVCPKG_MIRROR_MODE is set."""
        import cvcpkg.server.app as app_mod

        db_path = tmp_path / "test.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.setenv("CVCPKG_MIRROR_MODE", "1")
        monkeypatch.setenv("CVCPKG_MIRROR_UPSTREAM", "https://primary.example.com")

        # Patch the module-level globals
        monkeypatch.setattr(app_mod, "MIRROR_MODE", True)
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "https://primary.example.com")

        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _seed():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(tmp_path)
            pub_raw = await store.create("pub", TokenRole.publisher)
            await dispose_engine()
            return pub_raw

        pub_token = asyncio.run(_seed())

        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/publish",
                params={"name": "test", "version": "1.0"},
                files={"file": ("test.tar.gz", io.BytesIO(b"test-data"))},
                headers={"Authorization": f"Bearer {pub_token}"},
            )
            assert resp.status_code == 403
            assert "mirror mode" in resp.json()["detail"].lower()


class TestMirrorReadsServeIndex:
    """A DB-backed mirror must serve reads from the sync-maintained index.

    pkg.tx.wtf regression (2026-08-02): the mirror runs the production
    compose stack, so a database is configured and ``_use_db`` is true —
    /v1/packages, /v1/search and /v1/packages/{name} then queried the empty
    DB and listed 0 packages, while /healthz and /v1/catalog correctly
    served the full synced index (1748 bundles).
    """

    def test_listing_search_detail_use_index_in_mirror_mode(self, tmp_path, monkeypatch):
        import yaml as _yaml

        import cvcpkg.server.app as app_mod

        db_path = tmp_path / "test.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.setenv("CVCPKG_MIRROR_MODE", "1")
        monkeypatch.setenv("CVCPKG_MIRROR_UPSTREAM", "https://primary.example.com")
        monkeypatch.setattr(app_mod, "MIRROR_MODE", True)
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "https://primary.example.com")

        # The DB exists but is EMPTY — a mirror's catalog rows live in the
        # index maintained by the sync loop, not the DB.
        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _seed():
            init_db(db_url)
            await create_tables()
            await dispose_engine()

        asyncio.run(_seed())

        (tmp_path / "index.yaml").write_text(
            _yaml.safe_dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [
                        {
                            "name": "zlib",
                            "version": "1.3.1+cvc.1",
                            "platform": "linux",
                            "arch": "x86_64",
                            "build_type": "release",
                            "link": "shared",
                        },
                        {
                            "name": "libcvc-cuda",
                            "version": "1.0.0+cvc.1",
                            "platform": "linux",
                            "arch": "x86_64",
                            "build_type": "release",
                            "link": "shared",
                        },
                    ],
                }
            )
        )

        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            listed = client.get("/v1/packages").json()
            assert listed["total"] == 2
            assert {p["name"] for p in listed["packages"]} == {"zlib", "libcvc-cuda"}

            found = client.get("/v1/search", params={"q": "zlib"}).json()
            assert found["total"] == 1
            assert found["packages"][0]["name"] == "zlib"

            detail = client.get("/v1/packages/zlib").json()
            assert detail["total"] == 1
            assert detail["packages"][0]["version"] == "1.3.1+cvc.1"


class TestMirrorAudit:
    """Verify mirror actions are properly audited."""

    def test_mirror_register_audited(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com", "display_name": "EU Mirror"},
        )

        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        entries = resp.json().get("entries", [])
        mirror_entries = [
            e for e in entries if e.get("action") == AuditAction.mirror_register.value
        ]
        assert len(mirror_entries) >= 1
        assert mirror_entries[0]["target"] == "https://m1.example.com"

    def test_mirror_reject_audited(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        client.post(
            "/v1/mirrors/reject",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )

        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        entries = resp.json().get("entries", [])
        reject_entries = [e for e in entries if e.get("action") == AuditAction.mirror_reject.value]
        assert len(reject_entries) >= 1

    def test_mirror_remove_audited(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        client.post(
            "/v1/mirrors/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"url": "https://m1.example.com"},
        )
        client.delete(
            "/v1/mirrors",
            params={"url": "https://m1.example.com"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )

        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        entries = resp.json().get("entries", [])
        remove_entries = [e for e in entries if e.get("action") == AuditAction.mirror_remove.value]
        assert len(remove_entries) >= 1


# ── Mirror archive sync ─────────────────────────────────────────


class TestMirrorArchiveSync:
    """_mirror_sync_archives — a mirror fetches archive bytes, not just
    the catalog index (regression for the pkg.tx.wtf archive-stale
    mirror, whose /v1/download 404'd everything published after its
    one-time seed)."""

    def _state(self, tmp_path):
        from cvcpkg.server import app as app_mod

        state = app_mod.ServerState(tmp_path, storage_uri="", require_auth_for_reads=False)
        state.archives_dir().mkdir(parents=True, exist_ok=True)
        return state

    def _bundle(self, fname, content=b"archive-bytes", **over):
        import hashlib

        b = {
            "name": fname.split("-")[0],
            "version": "1.0.0",
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "archive_url": f"/v1/download/{fname}",
            "yanked": False,
        }
        b.update(over)
        return b

    def _fake_client(self, content_by_suffix):
        class _Resp:
            def __init__(self, content):
                self._content = content

            def raise_for_status(self):
                pass

            async def aiter_bytes(self, n):
                yield self._content

        class _Stream:
            def __init__(self, content):
                self._resp = _Resp(content)

            async def __aenter__(self):
                return self._resp

            async def __aexit__(self, *a):
                pass

        class _Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def stream(self, method, url, **kw):
                for suffix, content in content_by_suffix.items():
                    if url.endswith(suffix):
                        return _Stream(content)
                return _Stream(b"")

        return _Client

    def test_fetches_missing_archive(self, tmp_path, monkeypatch):
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        content = b"new archive published upstream"
        catalog = {"bundles": [self._bundle("newpkg-1.0.0-linux.tar.zst", content)]}
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr(
            "httpx.AsyncClient", self._fake_client({"newpkg-1.0.0-linux.tar.zst": content})
        )

        n = asyncio.run(app_mod._mirror_sync_archives(state, catalog))
        assert n == 1
        assert (state.archives_dir() / "newpkg-1.0.0-linux.tar.zst").read_bytes() == content

    def test_present_archive_not_refetched(self, tmp_path, monkeypatch):
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        content = b"already here"
        (state.archives_dir() / "have-1.0.0.tar.zst").write_bytes(content)
        catalog = {"bundles": [self._bundle("have-1.0.0.tar.zst", content)]}
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr("httpx.AsyncClient", self._fake_client({}))

        assert asyncio.run(app_mod._mirror_sync_archives(state, catalog)) == 0

    def test_size_drift_triggers_refetch(self, tmp_path, monkeypatch):
        """An archive whose row was corrected upstream is re-downloaded."""
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        (state.archives_dir() / "fixed-1.0.0.tar.zst").write_bytes(b"stale bytes!")
        new_content = b"corrected bytes"
        catalog = {"bundles": [self._bundle("fixed-1.0.0.tar.zst", new_content)]}
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr(
            "httpx.AsyncClient", self._fake_client({"fixed-1.0.0.tar.zst": new_content})
        )

        assert asyncio.run(app_mod._mirror_sync_archives(state, catalog)) == 1
        assert (state.archives_dir() / "fixed-1.0.0.tar.zst").read_bytes() == new_content

    def test_sha_mismatch_discarded(self, tmp_path, monkeypatch):
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        bundle = self._bundle("bad-1.0.0.tar.zst", b"expected bytes")
        catalog = {"bundles": [bundle]}
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr(
            "httpx.AsyncClient", self._fake_client({"bad-1.0.0.tar.zst": b"tampered bytes!!"})
        )

        assert asyncio.run(app_mod._mirror_sync_archives(state, catalog)) == 0
        assert not (state.archives_dir() / "bad-1.0.0.tar.zst").exists()

    def test_per_cycle_cap(self, tmp_path, monkeypatch):
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        content = b"x"
        catalog = {"bundles": [self._bundle(f"pkg{i}-1.0.0.tar.zst", content) for i in range(5)]}
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr(app_mod, "MIRROR_MAX_ARCHIVES_PER_SYNC", 2)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            self._fake_client({f"pkg{i}-1.0.0.tar.zst": content for i in range(5)}),
        )

        assert asyncio.run(app_mod._mirror_sync_archives(state, catalog)) == 2

    def test_yanked_and_placeholder_skipped(self, tmp_path, monkeypatch):
        import asyncio

        from cvcpkg.server import app as app_mod

        state = self._state(tmp_path)
        catalog = {
            "bundles": [
                self._bundle("gone-1.0.0.tar.zst", yanked=True),
                self._bundle("stub-1.0.0.tar.zst", archive_url=""),
            ]
        }
        monkeypatch.setattr(app_mod, "MIRROR_UPSTREAM", "http://primary.example")
        monkeypatch.setattr("httpx.AsyncClient", self._fake_client({}))

        assert asyncio.run(app_mod._mirror_sync_archives(state, catalog)) == 0
