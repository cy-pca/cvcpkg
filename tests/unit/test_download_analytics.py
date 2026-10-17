"""Tests for Phase 2 download analytics.

Covers the extended ``download_events`` schema (arch, client_ip_hash,
user_agent, cvcpkg_version, bytes_sent), the DbDownloadStore aggregate
queries, and the admin-gated /v1/analytics/* endpoints.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Store-level tests ───────────────────────────────────────────


class TestDownloadStore:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "analytics.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield
        asyncio.run(dispose_engine())

    def _record_events(self):
        from cvcpkg.server.db_stores import DbDownloadStore

        store = DbDownloadStore()

        async def _do():
            # zlib: 2 downloads on linux/x86_64 from cvcpkg 2.0.0
            for _ in range(2):
                await store.record(
                    "zlib",
                    "1.3.1",
                    "linux",
                    arch="x86_64",
                    client_ip_hash="a" * 64,
                    user_agent="cvcpkg/2.0.0",
                    cvcpkg_version="2.0.0",
                    bytes_sent=1000,
                )
            # boost: 1 download on windows/x86_64 from a browser
            await store.record(
                "boost",
                "1.90.0",
                "windows",
                arch="x86_64",
                client_ip_hash="b" * 64,
                user_agent="Mozilla/5.0",
                cvcpkg_version="",
                bytes_sent=5000,
            )
            return store

        return asyncio.run(_do())

    def test_record_and_totals(self):
        from cvcpkg.server.db_stores import DbDownloadStore

        self._record_events()
        store = DbDownloadStore()
        assert asyncio.run(store.get_total_downloads()) == 3
        assert asyncio.run(store.get_total_downloads(package_name="zlib")) == 2

    def test_top_packages_order_and_bytes(self):
        self._record_events()
        from cvcpkg.server.db_stores import DbDownloadStore

        top = asyncio.run(DbDownloadStore().get_top_packages(days=7))
        assert top[0]["name"] == "zlib"
        assert top[0]["count"] == 2
        assert top[0]["bytes_sent"] == 2000
        assert top[1]["name"] == "boost"
        assert top[1]["bytes_sent"] == 5000

    def test_platform_distribution(self):
        self._record_events()
        from cvcpkg.server.db_stores import DbDownloadStore

        dist = asyncio.run(DbDownloadStore().get_platform_distribution(days=7))
        by_platform = {(d["platform"], d["arch"]): d["count"] for d in dist}
        assert by_platform[("linux", "x86_64")] == 2
        assert by_platform[("windows", "x86_64")] == 1

    def test_bandwidth_totals_and_daily_shape(self):
        self._record_events()
        from cvcpkg.server.db_stores import DbDownloadStore

        bw = asyncio.run(DbDownloadStore().get_bandwidth(days=7))
        assert bw["total_bytes"] == 7000
        assert len(bw["daily"]) == 7
        assert sum(d["bytes"] for d in bw["daily"]) == 7000
        # Filtered by package
        bw_zlib = asyncio.run(DbDownloadStore().get_bandwidth(package_name="zlib", days=7))
        assert bw_zlib["total_bytes"] == 2000

    def test_client_versions(self):
        self._record_events()
        from cvcpkg.server.db_stores import DbDownloadStore

        versions = asyncio.run(DbDownloadStore().get_client_versions(days=7))
        by_ver = {v["version"]: v["count"] for v in versions}
        assert by_ver["2.0.0"] == 2
        assert by_ver[""] == 1  # the browser download


# ── Endpoint tests ──────────────────────────────────────────────


@pytest.fixture()
def analytics_server(tmp_path, monkeypatch):
    """DB-backed test server with admin + publisher tokens and seed events."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbDownloadStore, DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        dl = DbDownloadStore()
        await dl.record(
            "zlib",
            "1.3.1",
            "linux",
            arch="x86_64",
            client_ip_hash="c" * 64,
            user_agent="cvcpkg/2.0.0",
            cvcpkg_version="2.0.0",
            bytes_sent=1234,
        )
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token


class TestAnalyticsEndpoints:
    ENDPOINTS = (
        "/v1/analytics/downloads",
        "/v1/analytics/bandwidth",
        "/v1/analytics/platforms",
        "/v1/analytics/trends",
    )

    def test_requires_admin(self, analytics_server):
        client, _admin, pub = analytics_server
        for ep in self.ENDPOINTS:
            r = client.get(ep, headers={"Authorization": f"Bearer {pub}"})
            assert r.status_code == 403, ep
            r = client.get(ep)
            assert r.status_code in (401, 403), ep

    def test_downloads_shape(self, analytics_server):
        client, admin, _pub = analytics_server
        r = client.get(
            "/v1/analytics/downloads",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_all_time"] == 1
        assert data["top_packages"][0]["name"] == "zlib"
        assert data["top_packages"][0]["bytes_sent"] == 1234

    def test_bandwidth_shape(self, analytics_server):
        client, admin, _pub = analytics_server
        r = client.get(
            "/v1/analytics/bandwidth?days=7",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_bytes"] == 1234
        assert len(data["daily"]) == 7

    def test_platforms_shape(self, analytics_server):
        client, admin, _pub = analytics_server
        r = client.get(
            "/v1/analytics/platforms",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert {"platform": "linux", "arch": "x86_64", "count": 1} in data["platforms"]
        assert {"version": "2.0.0", "count": 1} in data["client_versions"]

    def test_trends_shape(self, analytics_server):
        client, admin, _pub = analytics_server
        r = client.get(
            "/v1/analytics/trends?days=7&name=zlib",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["daily"]) == 7
        assert sum(d["count"] for d in data["daily"]) == 1


class TestDownloadRecordingEnrichment:
    """The /v1/download endpoint records the enriched analytics fields."""

    def test_download_records_ua_and_bytes(self, analytics_server, tmp_path):
        client, admin, _pub = analytics_server
        # Drop a fake archive into the server's archives dir
        # (state_dir/archives — create_app used tmp_path as state_dir).
        archives = tmp_path / "archives"
        archives.mkdir(exist_ok=True)
        payload = b"x" * 512
        (archives / "fake-1.0-linux-x86_64-release-shared.tar.zst").write_bytes(payload)

        r = client.get(
            "/v1/download/fake-1.0-linux-x86_64-release-shared.tar.zst",
            headers={"User-Agent": "cvcpkg/9.9.9"},
        )
        assert r.status_code == 200
        assert r.content == payload

        r = client.get(
            "/v1/analytics/platforms",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        versions = {v["version"]: v["count"] for v in r.json()["client_versions"]}
        assert versions.get("9.9.9") == 1

        r = client.get(
            "/v1/analytics/bandwidth?days=2",
            headers={"Authorization": f"Bearer {admin}"},
        )
        # 1234 seeded + 512 from the fake archive
        assert r.json()["total_bytes"] == 1234 + 512


# ── Resolving a downloaded file to its package ──────────────────


class TestArchiveCoordsFromName:
    """The filename fallback, used only when no package row has the file."""

    def test_hyphenated_name(self):
        from cvcpkg.server.app import _archive_coords_from_name

        assert _archive_coords_from_name(
            "torch-cp311-cuda-2.5.1+cvc.2-linux-x86_64-release-shared.tar.zst"
        ) == {
            "name": "torch-cp311-cuda",
            "version": "2.5.1+cvc.2",
            "platform": "linux",
            "arch": "x86_64",
        }

    def test_unparseable_name_keeps_stem(self):
        from cvcpkg.server.app import _archive_coords_from_name

        assert _archive_coords_from_name("tmpk64z88ty.chunk") == {
            "name": "tmpk64z88ty.chunk",
            "version": "",
            "platform": "",
            "arch": "",
        }


@pytest.fixture()
def resolving_server(tmp_path, monkeypatch):
    """A DB-backed server with package rows whose archives sit on disk."""
    db_path = tmp_path / "resolve.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex

    archives = tmp_path / "archives"
    archives.mkdir()
    rows = [
        # (name, version, yank it?)
        ("torch-cp311-cuda", "2.5.1+cvc.2", True),
        ("a-b", "1.0", False),
    ]

    async def _seed():
        init_db(db_url)
        await create_tables()
        idx = DbPackageIndex()
        for name, version, yank in rows:
            fn = f"{name}-{version}-linux-x86_64-release-shared.tar.zst"
            (archives / fn).write_bytes(b"z" * 64)
            await idx.add_package(
                name=name,
                version=version,
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="0" * 64,
                size_bytes=64,
                archive_url=f"/v1/download/{fn}",
            )
            if yank:
                await idx.yank(name, version, platform="linux", arch="x86_64")
        await dispose_engine()

    asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, archives, db_path


def _last_event(db_path):
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT package_name, version, platform, arch FROM download_events ORDER BY id DESC LIMIT 1"
        ).fetchone()


class TestDownloadVersionResolution:
    def test_yanked_bundle_records_its_version(self, resolving_server):
        client, _archives, db_path = resolving_server
        fn = "torch-cp311-cuda-2.5.1+cvc.2-linux-x86_64-release-shared.tar.zst"
        r = client.get(f"/v1/download/{fn}")
        assert r.status_code == 200
        assert _last_event(db_path) == ("torch-cp311-cuda", "2.5.1+cvc.2", "linux", "x86_64")

    def test_underscore_does_not_match_a_hyphen(self, resolving_server):
        # "_" is a LIKE wildcard: unescaped, a_b's file would resolve to a-b's row.
        client, archives, db_path = resolving_server
        fn = "a_b-1.0-linux-x86_64-release-shared.tar.zst"
        (archives / fn).write_bytes(b"q" * 8)
        r = client.get(f"/v1/download/{fn}")
        assert r.status_code == 200
        assert _last_event(db_path) == ("a_b", "1.0", "linux", "x86_64")

    def test_store_lookup_includes_yanked_rows(self, resolving_server):
        from cvcpkg.server.db_stores import DbPackageIndex

        _client, _archives, _db = resolving_server
        coords = asyncio.run(
            DbPackageIndex().get_archive_coords(
                "torch-cp311-cuda-2.5.1+cvc.2-linux-x86_64-release-shared.tar.zst"
            )
        )
        assert coords == {
            "name": "torch-cp311-cuda",
            "version": "2.5.1+cvc.2",
            "platform": "linux",
            "arch": "x86_64",
        }
