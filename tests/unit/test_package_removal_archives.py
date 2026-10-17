"""Every path that removes package rows also removes their archives.

``DELETE /v1/packages/{name}/{version}``, ``DELETE
/v1/packages/by-link/{platform}/{link}`` and the cache GC used to delete rows
only.  The archives stayed on disk, unaccounted for and still downloadable:
on 2026-06-07 the by-link endpoint left 40 files (16.65 GB) on cvcpkg.org that
no row or tombstone explained.  These tests pin the fix: each path deletes the
archive and writes a tombstone so the download answers 410; the GC's storage
rule never evicts a coordinate's newest bundle; ``dry_run`` deletes nothing;
and the orphan sweep finds and removes files nothing accounts for.
"""

from __future__ import annotations

import asyncio
import io
import os
import time

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required for DB tests")


def run(coro):
    return asyncio.run(coro)


# ── Store level ─────────────────────────────────────────────────


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    """Fresh file-backed sqlite DB plus a file:// archive store under tmp_path."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _init():
        init_db(db_url)
        await create_tables()

    run(_init())
    archives = tmp_path / "archives"
    archives.mkdir()
    try:
        yield f"file://{tmp_path}", archives
    finally:
        run(dispose_engine())


async def _add(
    idx,
    archives,
    *,
    name="zlib",
    version="1.0.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
    size_bytes=100,
    filename=None,
    **over,
):
    fname = filename or f"{name}-{version}-{platform}-{arch}-{build_type}-{link}.tar.zst"
    (archives / fname).write_bytes(b"x" * size_bytes)
    await idx.add_package(
        name=name,
        version=version,
        platform=platform,
        arch=arch,
        build_type=build_type,
        link=link,
        sha256="s" * 64,
        size_bytes=size_bytes,
        archive_url=f"/v1/download/{fname}",
        **over,
    )
    return fname


async def _publish_times(times: dict[str, float]):
    """Set published_at to *times*[version] days ago (test setup only)."""
    import datetime

    from sqlalchemy import update

    from cvcpkg.server.db import PackageRow, get_session

    now = datetime.datetime.now(datetime.timezone.utc)
    async with get_session() as session:
        for version, days in times.items():
            await session.execute(
                update(PackageRow)
                .where(PackageRow.version == version)
                .values(published_at=now - datetime.timedelta(days=days))
            )


class TestStoreDelete:
    def test_delete_removes_archive_and_tombstones(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            linux = await _add(idx, archives, platform="linux")
            windows = await _add(idx, archives, platform="windows")
            deleted = await idx.delete(
                "zlib", "1.0.0", platform="linux", storage_uri=uri, deleted_by="ops"
            )
            assert [(d["platform"], d["archive_deleted"]) for d in deleted] == [("linux", True)]
            assert not (archives / linux).exists()
            assert (archives / windows).exists()  # out of scope, untouched
            ts = await idx.get_tombstone_by_filename(linux)
            assert ts["reason"] == "deleted" and ts["nuked_by"] == "ops"
            assert await idx.get_tombstone_by_filename(windows) is None

        run(_t())

    def test_delete_keeps_archive_another_row_references(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            shared = await _add(idx, archives, platform="linux", filename="shared_a.tar.zst")
            await _add(idx, archives, platform="windows", filename="shared_a.tar.zst")
            # "shared-a" would match "shared_a" through an unescaped LIKE "_".
            await _add(idx, archives, name="other", filename="shared-a.tar.zst")
            deleted = await idx.delete("zlib", "1.0.0", platform="linux", storage_uri=uri)
            assert deleted[0]["archive_deleted"] is False
            assert (archives / shared).exists()
            deleted = await idx.delete("other", "1.0.0", storage_uri=uri)
            assert deleted[0]["archive_deleted"] is True

        run(_t())

    def test_delete_without_tombstone(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            fname = await _add(idx, archives)
            await idx.delete("zlib", "1.0.0", storage_uri="", tombstone=False)
            assert (archives / fname).exists()  # the caller unlinks it itself
            assert await idx.get_tombstone_by_filename(fname) is None

        run(_t())

    def test_delete_by_link_removes_archives_and_tombstones(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            a = await _add(idx, archives, name="a", platform="wasm", link="static")
            b = await _add(idx, archives, name="b", platform="wasm", link="shared")
            deleted = await idx.delete_by_link("wasm", "static", storage_uri=uri, deleted_by="ops")
            assert [d["name"] for d in deleted] == ["a"]
            assert not (archives / a).exists() and (archives / b).exists()
            assert (await idx.get_tombstone_by_filename(a))["reason"] == "deleted"

        run(_t())

    def test_filename_states(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            live = await _add(idx, archives, name="live")
            gone = await _add(idx, archives, name="gone")
            await idx.delete("gone", "1.0.0", storage_uri="")  # tombstone, bytes kept
            states = await idx.archive_filename_states([live, gone, "stray.tar.zst"])
            assert states == {live: "row", gone: "tombstone"}

        run(_t())


class TestStoreGc:
    def test_gc_by_age_removes_archives_and_tombstones(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            old = await _add(idx, archives, version="1.0")
            new = await _add(idx, archives, version="2.0")
            await _publish_times({"1.0": 10, "2.0": 0})
            deleted = await idx.gc_by_age(86400, storage_uri=uri, removed_by="ops")
            assert [(d["version"], d["archive_deleted"]) for d in deleted] == [("1.0", True)]
            assert not (archives / old).exists() and (archives / new).exists()
            ts = await idx.get_tombstone_by_filename(old)
            assert ts["reason"] == "gc" and ts["nuked_by"] == "ops"

        run(_t())

    def test_gc_by_staleness_removes_archives_and_tombstones(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            keep = await _add(idx, archives, name="keep", recipe_version="good")
            drop = await _add(idx, archives, name="drop", recipe_version="stale")
            deleted = await idx.gc_by_staleness({"good"}, storage_uri=uri)
            assert [d["name"] for d in deleted] == ["drop"]
            assert not (archives / drop).exists() and (archives / keep).exists()
            assert (await idx.get_tombstone_by_filename(drop))["reason"] == "gc"

        run(_t())

    def test_gc_by_storage_never_evicts_newest_of_a_coordinate(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            # +cvc.10 is the newest by version_sort_key but was published
            # first, so oldest-published-first alone would evict it.
            await _add(idx, archives, version="1.0+cvc.10")
            await _add(idx, archives, version="1.0+cvc.9")
            await _add(idx, archives, version="1.0+cvc.2")
            # A package's only build, published before everything else.
            only = await _add(idx, archives, name="only", version="0.1")
            # Same name, different coordinate (link): its own newest is kept.
            static = await _add(idx, archives, version="1.0+cvc.2", link="static")
            await _publish_times({"0.1": 30, "1.0+cvc.10": 20, "1.0+cvc.9": 10, "1.0+cvc.2": 5})

            deleted = await idx.gc_by_storage(0, storage_uri=uri)
            assert sorted(d["version"] for d in deleted) == ["1.0+cvc.2", "1.0+cvc.9"]
            assert {d["link"] for d in deleted} == {"shared"}
            pkgs, _ = await idx.get_bundles(include_yanked=True, limit=100)
            left = {(p.name, p.version, p.link) for p in pkgs}
            assert left == {
                ("zlib", "1.0+cvc.10", "shared"),
                ("zlib", "1.0+cvc.2", "static"),
                ("only", "0.1", "shared"),
            }
            assert (archives / only).exists() and (archives / static).exists()
            # Nothing left to evict: every coordinate is down to its newest.
            assert await idx.gc_by_storage(0, storage_uri=uri) == []

        run(_t())

    def test_gc_by_storage_newest_is_per_org(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, archives, version="2.0")
            await _add(idx, archives, version="1.0", org_slug="acme", filename="acme-1.tar.zst")
            assert await idx.gc_by_storage(0, storage_uri=uri) == []

        run(_t())

    def test_gc_by_storage_stops_under_budget(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            for v in ("1.0", "2.0", "3.0", "4.0"):
                await _add(idx, archives, version=v, size_bytes=100)
            await _publish_times({"1.0": 4, "2.0": 3, "3.0": 2, "4.0": 1})
            assert await idx.gc_by_storage(400, storage_uri=uri) == []
            deleted = await idx.gc_by_storage(250, storage_uri=uri)
            assert [d["version"] for d in deleted] == ["1.0", "2.0"]

        run(_t())

    def test_dry_run_deletes_nothing_and_matches_real_run(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            for v in ("1.0", "2.0", "3.0"):
                await _add(idx, archives, version=v, recipe_version=f"h{v}")
            await _publish_times({"1.0": 3, "2.0": 2, "3.0": 1})
            rules = dict(max_age_seconds=2.5 * 86400, max_storage_bytes=0, storage_uri=uri)
            preview = await idx.gc(dry_run=True, **rules)
            assert sorted(d["version"] for d in preview) == ["1.0", "2.0"]
            assert all(d["archive_deleted"] is False for d in preview)
            _, total = await idx.get_bundles(include_yanked=True)
            assert total == 3
            assert len(list(archives.iterdir())) == 3
            assert await idx.get_tombstones("zlib") == []

            real = await idx.gc(**rules)
            assert [(d["version"], d["archive_deleted"]) for d in real] == [
                (d["version"], True) for d in preview
            ]
            assert sorted(p.name for p in archives.iterdir()) == [
                "zlib-3.0-linux-x86_64-release-shared.tar.zst"
            ]

        run(_t())

    def test_storage_rule_counts_what_other_rules_freed(self, store_env):
        from cvcpkg.server.db_stores import DbPackageIndex

        uri, archives = store_env

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, archives, name="a", version="1.0", recipe_version="stale")
            await _add(idx, archives, name="a", version="2.0", recipe_version="good")
            await _add(idx, archives, name="b", version="1.0", recipe_version="good")
            await _add(idx, archives, name="b", version="2.0", recipe_version="good")
            await _publish_times({"1.0": 2, "2.0": 1})
            # 400 bytes live; staleness frees a-1.0, which already fits 300.
            deleted = await idx.gc(
                valid_chain_hashes={"good"}, max_storage_bytes=300, storage_uri=uri
            )
            assert [(d["name"], d["version"]) for d in deleted] == [("a", "1.0")]

        run(_t())


# ── HTTP level ──────────────────────────────────────────────────


fastapi = pytest.importorskip("fastapi", reason="server extras not installed")


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """A sqlite-backed TestClient with admin and publisher tokens."""
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.models import TokenRole

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'endpoints.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("db-admin", TokenRole.admin)
        pub = await store.create("db-pub", TokenRole.publisher)
        await dispose_engine()
        return admin, pub

    admin, pub = run(_seed())
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, admin, pub, tmp_path / "archives"


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _publish(client, tok, name="zlib", version="1.0", platform="linux", **params):
    body = {"name": name, "version": version, "platform": platform, "arch": "x86_64"}
    body.update(params)
    r = client.post(
        "/v1/publish",
        params=body,
        files={"file": (f"{name}.tar.zst", io.BytesIO(b"bytes-of-" + name.encode()))},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["archive_url"]


def _fname(url: str) -> str:
    return url.rsplit("/", 1)[-1]


class TestDeleteEndpoints:
    def test_delete_package_removes_archive_and_410s(self, db_env):
        client, admin, pub, archives = db_env
        url = _publish(client, pub)
        assert (archives / _fname(url)).is_file()
        r = client.delete("/v1/packages/zlib/1.0", headers=_h(admin))
        assert r.status_code == 200, r.text
        assert r.json()["removed"] == 1 and r.json()["archives_deleted"] == 1
        assert not (archives / _fname(url)).exists()
        got = client.get(url)
        assert got.status_code == 410 and "(deleted)" in got.json()["detail"]
        assert client.head(url).status_code == 410
        ts = client.get("/v1/packages/zlib/tombstones").json()["tombstones"]
        assert [(t["reason"], t["nuked_by"]) for t in ts] == [("deleted", "db-admin")]

    def test_delete_by_link_removes_archives_and_410s(self, db_env):
        client, admin, pub, archives = db_env
        a = _publish(client, pub, name="a", platform="wasm", link="static")
        b = _publish(client, pub, name="b", platform="wasm", link="shared")
        r = client.delete("/v1/packages/by-link/wasm/static", headers=_h(admin))
        assert r.status_code == 200 and r.json()["archives_deleted"] == 1
        assert not (archives / _fname(a)).exists()
        assert client.get(a).status_code == 410
        assert client.get(b).status_code == 200

    def test_republish_after_delete_downloads_again(self, db_env):
        client, admin, pub, _ = db_env
        url = _publish(client, pub)
        client.delete("/v1/packages/zlib/1.0", headers=_h(admin))
        assert _publish(client, pub) == url
        assert client.get(url).status_code == 200

    def test_delete_gives_org_storage_back(self, db_env):
        client, admin, pub, _ = db_env
        r = client.post("/v1/orgs", headers=_h(pub), json={"slug": "acme", "display_name": "A"})
        assert r.status_code == 200, r.text
        _publish(client, pub, org="acme")

        def used():
            return client.get("/v1/orgs/acme", headers=_h(pub)).json()["org"]["storage_used_bytes"]

        assert used() > 0
        client.delete("/v1/packages/zlib/1.0", headers=_h(admin))
        assert used() == 0


class TestCacheGcEndpoint:
    def _two_builds(self, client, pub):
        old = _publish(client, pub, version="1.0+cvc.1")
        new = _publish(client, pub, version="1.0+cvc.2")
        return old, new

    def test_dry_run_deletes_nothing(self, db_env):
        client, admin, pub, archives = db_env
        old, new = self._two_builds(client, pub)
        r = client.post(
            "/v1/cache/gc", headers=_h(admin), json={"max_storage_bytes": 0, "dry_run": True}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert [d["version"] for d in body["deleted"]] == ["1.0+cvc.1"]
        assert body["deleted_bytes"] == body["deleted"][0]["size_bytes"]
        assert (archives / _fname(old)).is_file()
        assert client.get(old).status_code == 200
        assert client.get("/v1/packages/zlib/tombstones").json()["count"] == 0

    def test_gc_removes_archive_tombstones_and_keeps_newest(self, db_env):
        client, admin, pub, archives = db_env
        old, new = self._two_builds(client, pub)
        only = _publish(client, pub, name="only")
        r = client.post("/v1/cache/gc", headers=_h(admin), json={"max_storage_bytes": 0})
        assert r.status_code == 200, r.text
        assert [d["version"] for d in r.json()["deleted"]] == ["1.0+cvc.1"]
        assert not (archives / _fname(old)).exists()
        assert client.get(old).status_code == 410
        assert client.get(new).status_code == 200
        assert client.get(only).status_code == 200
        ts = client.get("/v1/packages/zlib/tombstones").json()["tombstones"]
        assert [t["reason"] for t in ts] == ["gc"]

    def test_invalid_rule_deletes_nothing(self, db_env):
        client, admin, pub, archives = db_env
        old, _ = self._two_builds(client, pub)
        r = client.post(
            "/v1/cache/gc",
            headers=_h(admin),
            json={"max_age_seconds": 1e-9, "max_storage_bytes": -1},
        )
        assert r.status_code == 422
        assert (archives / _fname(old)).is_file()
        assert client.get("/v1/packages").json()["total"] == 2
        bad = client.post(
            "/v1/cache/gc", headers=_h(admin), json={"max_age_seconds": 1, "dry_run": "y"}
        )
        assert bad.status_code == 422

    def test_bulk_delete_keeps_newest(self, db_env):
        client, admin, pub, archives = db_env
        old, new = self._two_builds(client, pub)
        r = client.delete("/v1/cache", headers=_h(admin))
        assert r.status_code == 200 and r.json()["deleted_count"] == 1
        assert client.get(old).status_code == 410
        assert client.get(new).status_code == 200


class TestOrphanSweep:
    def _stray(self, archives, name, age_seconds=7200):
        archives.mkdir(parents=True, exist_ok=True)
        p = archives / name
        p.write_bytes(b"orphan")
        t = time.time() - age_seconds
        os.utime(p, (t, t))
        return p

    def test_lists_then_deletes_orphans(self, db_env):
        client, admin, pub, archives = db_env
        live = _publish(client, pub)
        gone = _publish(client, pub, name="gone")
        client.delete("/v1/packages/gone/1.0", headers=_h(admin))
        stray = self._stray(archives, "stray-1.0-wasm-wasm32-release-static.tar.zst")
        tombstoned = self._stray(archives, _fname(gone))  # bytes back, row gone
        recent = self._stray(archives, "recent.tar.zst", age_seconds=0)
        staging = self._stray(archives, "tmpabc.upload")

        r = client.post("/v1/admin/gc/orphans", headers=_h(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True and body["skipped_recent"] == 1
        assert {(o["filename"], o["tombstoned"]) for o in body["orphans"]} == {
            (stray.name, False),
            (tombstoned.name, True),
        }
        assert body["size_bytes"] == 2 * len(b"orphan")
        assert stray.exists() and tombstoned.exists()
        assert client.get(f"/v1/download/{stray.name}").status_code == 200

        r = client.post("/v1/admin/gc/orphans?dry_run=false", headers=_h(admin))
        assert r.status_code == 200, r.text
        assert all(o["deleted"] for o in r.json()["orphans"])
        assert not stray.exists() and not tombstoned.exists()
        assert recent.exists() and staging.exists()
        assert (archives / _fname(live)).is_file()
        assert client.get(live).status_code == 200

    def test_admin_only(self, db_env):
        client, admin, pub, _ = db_env
        assert client.post("/v1/admin/gc/orphans", headers=_h(pub)).status_code == 403

    def test_mirror_lists_but_refuses_delete(self, db_env, monkeypatch):
        from cvcpkg.server import app as app_module

        client, admin, pub, archives = db_env
        stray = self._stray(archives, "cached-proxy.tar.zst")
        monkeypatch.setattr(app_module, "MIRROR_MODE", True)
        assert client.post("/v1/admin/gc/orphans", headers=_h(admin)).json()["count"] == 1
        r = client.post("/v1/admin/gc/orphans?dry_run=false", headers=_h(admin))
        assert r.status_code == 409
        assert stray.exists()


class TestYamlBackendDelete:
    def test_delete_removes_archive(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from cvcpkg.server.app import create_app
        from cvcpkg.server.auth import TokenStore
        from cvcpkg.server.models import TokenRole

        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        store = TokenStore(tmp_path)
        admin = store.create("admin-user", TokenRole.admin)
        pub = store.create("pub-user", TokenRole.publisher)
        with TestClient(create_app(state_dir=tmp_path)) as client:
            url = _publish(client, pub)
            archive = tmp_path / "archives" / _fname(url)
            assert archive.is_file()
            r = client.delete("/v1/packages/zlib/1.0", headers=_h(admin))
            assert r.status_code == 200 and r.json()["archives_deleted"] == 1
            assert not archive.exists()
            assert client.get(url).status_code == 404
