# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The cached /v1/catalog body, its invalidation, and the cheap /healthz.

cvcpkg.org rebuilt the whole catalog (every packages row, sorted, encoded to
~6 MB of JSON) on every /v1/catalog fetch and twice on every /healthz, on a
single uvicorn worker, so each fetch held up every other request for 1-3 s.
The body is now cached per visibility scope until a write changes the
catalog.  These tests pin down that a write is always seen by the next
fetch, that private-org packages never leak into another scope's body, and
that /healthz no longer builds the catalog at all.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import re

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")

from fastapi.testclient import TestClient

from cvcpkg.server import catalog_cache
from cvcpkg.server.app import create_app
from cvcpkg.server.catalog_cache import CatalogBody, CatalogCache, etag_matches
from cvcpkg.server.models import TokenRole

# ── Fixtures / helpers ──────────────────────────────────────────


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """A sqlite-backed app; 'pub' owns the private org 'shell'."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_POPULATE_UPSTREAM", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        tokens = {
            "admin": await store.create("cat-admin", TokenRole.admin),
            "pub": await store.create("cat-pub", TokenRole.publisher),
            "outsider": await store.create("cat-outsider", TokenRole.reader),
        }
        await dispose_engine()
        return tokens

    tokens = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/v1/orgs",
            json={"slug": "shell", "display_name": "Shell", "is_private": True},
            headers=_h(tokens["pub"]),
        )
        assert r.status_code in (200, 201), r.text
        yield client, tokens


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _publish(client, tok, name, version="1.0", **params):
    body = {"name": name, "version": version, "platform": "linux", "arch": "x86_64"}
    body.update(params)
    r = client.post(
        "/v1/publish",
        params=body,
        files={"file": (f"{name}-{version}.tar.zst", io.BytesIO(f"{name}{version}".encode()))},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r


def _names(client, headers=None, **params) -> set[str]:
    r = client.get("/v1/catalog", headers=headers or {}, params=params)
    assert r.status_code == 200, r.text
    return {b["name"] for b in r.json()["bundles"]}


def _cache_counts(client) -> tuple[int, int]:
    text = client.get("/metrics").text
    hits = int(re.search(r"^cvcpkg_catalog_cache_hits_total (\d+)$", text, re.M).group(1))
    misses = int(re.search(r"^cvcpkg_catalog_cache_misses_total (\d+)$", text, re.M).group(1))
    return hits, misses


# ── Invalidation through the HTTP API ───────────────────────────


class TestWritesReachTheNextFetch:
    def test_cache_serves_repeat_fetches(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        assert _names(client) == {"zlib"}
        hits0, misses0 = _cache_counts(client)
        assert _names(client) == {"zlib"}
        assert _names(client) == {"zlib"}
        hits1, misses1 = _cache_counts(client)
        assert (hits1 - hits0, misses1 - misses0) == (2, 0)

    def test_publish(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        assert _names(client) == {"zlib"}  # warm
        _publish(client, tok["pub"], "bzip2")
        assert _names(client) == {"zlib", "bzip2"}
        _publish(client, tok["pub"], "zlib", version="1.1")
        bundles = client.get("/v1/catalog").json()["bundles"]
        # By name, then each name's newest version first.
        assert [(b["name"], b["version"]) for b in bundles] == [
            ("bzip2", "1.0"),
            ("zlib", "1.1"),
            ("zlib", "1.0"),
        ]

    def test_yank_unyank_and_include_yanked(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        _publish(client, tok["pub"], "bzip2")
        assert _names(client) == {"zlib", "bzip2"}
        assert _names(client, include_yanked=True) == {"zlib", "bzip2"}

        r = client.post("/v1/packages/zlib/1.0/yank", headers=_h(tok["admin"]))
        assert r.status_code == 200 and r.json()["count"] == 1
        assert _names(client) == {"bzip2"}
        yanked = client.get("/v1/catalog", params={"include_yanked": True}).json()["bundles"]
        assert {b["name"]: b["yanked"] for b in yanked} == {"zlib": True, "bzip2": False}

        r = client.post("/v1/packages/zlib/1.0/unyank", headers=_h(tok["admin"]))
        assert r.status_code == 200 and r.json()["count"] == 1
        assert _names(client) == {"zlib", "bzip2"}

    def test_delete(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        _publish(client, tok["pub"], "bzip2")
        assert _names(client) == {"zlib", "bzip2"}
        r = client.delete("/v1/packages/zlib/1.0", headers=_h(tok["admin"]))
        assert r.status_code == 200, r.text
        assert _names(client) == {"bzip2"}

    def test_revision_follows_publishes(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        rev = client.get("/v1/catalog").json()["revision"]
        _publish(client, tok["pub"], "bzip2")
        assert client.get("/v1/catalog").json()["revision"] == rev + 1


class TestPrivateOrgScopes:
    def test_member_sees_private_packages_anonymous_does_not(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        _publish(client, tok["pub"], "secret", org="shell")

        # Warm the anonymous body first: the member's fetch must not reuse it,
        # and the anonymous one must not pick up the member's.
        assert _names(client) == {"zlib"}
        assert _names(client, _h(tok["pub"])) == {"zlib", "secret"}
        assert _names(client) == {"zlib"}
        assert _names(client, _h(tok["outsider"])) == {"zlib"}
        assert _names(client, _h(tok["admin"])) == {"zlib", "secret"}

    def test_membership_change_moves_the_caller_between_scopes(self, server):
        client, tok = server
        _publish(client, tok["pub"], "secret", org="shell")
        assert _names(client, _h(tok["outsider"])) == set()
        assert _names(client, _h(tok["pub"])) == {"secret"}

        r = client.post(
            "/v1/orgs/shell/members",
            params={"token_name": "cat-outsider", "principal_kind": "token"},
            headers=_h(tok["pub"]),
        )
        assert r.status_code in (200, 201), r.text
        assert _names(client, _h(tok["outsider"])) == {"secret"}

        r = client.delete("/v1/orgs/shell/members/cat-outsider", headers=_h(tok["pub"]))
        assert r.status_code == 200, r.text
        assert _names(client, _h(tok["outsider"])) == set()

    def test_publish_into_private_org_reaches_the_member(self, server):
        client, tok = server
        _publish(client, tok["pub"], "secret", org="shell")
        assert _names(client, _h(tok["pub"])) == {"secret"}
        _publish(client, tok["pub"], "secret2", org="shell")
        assert _names(client, _h(tok["pub"])) == {"secret", "secret2"}
        assert _names(client) == set()

    def test_org_visibility_change(self, server):
        client, tok = server
        _publish(client, tok["pub"], "secret", org="shell")
        assert _names(client) == set()  # warm

        r = client.patch("/v1/orgs/shell", json={"is_private": False}, headers=_h(tok["pub"]))
        assert r.status_code == 200, r.text
        assert _names(client) == {"secret"}

        r = client.patch("/v1/orgs/shell", json={"is_private": True}, headers=_h(tok["pub"]))
        assert r.status_code == 200, r.text
        assert _names(client) == set()


class TestResponse:
    def test_archive_url_absolute_under_the_requested_base(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        here = client.get("/v1/catalog").json()["bundles"][0]["archive_url"]
        assert here.startswith("http://testserver/v1/download/")

        # Same cached body, another base: the rewrite happens per request.
        other = client.get("/v1/catalog", headers={"Host": "mirror.example:8420"}).json()
        url = other["bundles"][0]["archive_url"]
        assert url == here.replace("http://testserver", "http://mirror.example:8420")

    def test_matches_get_catalog_dict(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib", description='say "hi" \\ ünïcode')
        _publish(client, tok["pub"], "zlib", version="1.1", required_deps='["bzip2>=1"]')
        served = client.get("/v1/catalog").json()

        from cvcpkg.server import app as app_module

        cat = asyncio.run(app_module._db_packages.get_catalog_dict())
        for b in cat["bundles"]:
            b["archive_url"] = "http://testserver" + b["archive_url"]
        assert served == {
            "schema_version": 1,
            "revision": cat["revision"],
            "bundles": cat["bundles"],
        }
        # The fields, in the order clients have always received them.
        assert list(served["bundles"][0]) == [
            "name",
            "version",
            "platform",
            "arch",
            "build_type",
            "link",
            "sha256",
            "size_bytes",
            "archive_url",
            "published_at",
            "yanked",
            "upstream_yanked",
            "diverges_upstream",
            "signature",
            "key_fingerprint",
            "release_tag",
            "recipe_version",
            "description",
            "homepage",
            "license",
            "maintainer",
            "tags",
            "published_by",
            "org",
            "required_deps",
            "provides",
        ]
        assert [b["version"] for b in served["bundles"]] == ["1.1", "1.0"]
        assert served["bundles"][0]["required_deps"] == ["bzip2>=1"]
        assert served["bundles"][1]["description"] == 'say "hi" \\ ünïcode'

    def test_etag_304_until_the_catalog_changes(self, server):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        r = client.get("/v1/catalog")
        etag = r.headers["etag"]
        assert r.headers["cache-control"] == "no-cache"

        again = client.get("/v1/catalog", headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == etag
        assert client.get("/v1/catalog", headers={"If-None-Match": f"W/{etag}"}).status_code == 304

        # Each base has its own body and so its own tag.
        other = client.get("/v1/catalog", headers={"Host": "mirror.example", "If-None-Match": etag})
        assert other.status_code == 200 and other.headers["etag"] != etag

        _publish(client, tok["pub"], "bzip2")
        changed = client.get("/v1/catalog", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert changed.headers["etag"] != etag
        assert {b["name"] for b in changed.json()["bundles"]} == {"zlib", "bzip2"}

    def test_scopes_have_distinct_etags_and_private_cache_control(self, server):
        client, tok = server
        _publish(client, tok["pub"], "secret", org="shell")
        anon = client.get("/v1/catalog")
        member = client.get("/v1/catalog", headers=_h(tok["pub"]))
        assert anon.headers["etag"] != member.headers["etag"]
        assert member.headers["cache-control"] == "private, no-cache"
        # The anonymous tag must not let a member keep the anonymous body.
        r = client.get(
            "/v1/catalog", headers={**_h(tok["pub"]), "If-None-Match": anon.headers["etag"]}
        )
        assert r.status_code == 200 and {b["name"] for b in r.json()["bundles"]} == {"secret"}


class TestHealthz:
    def test_counts_without_building_the_catalog(self, server, monkeypatch):
        client, tok = server
        _publish(client, tok["pub"], "zlib")
        _publish(client, tok["pub"], "bzip2")
        _publish(client, tok["pub"], "secret", org="shell")
        client.post("/v1/packages/bzip2/1.0/yank", headers=_h(tok["admin"]))

        from cvcpkg.server.db_stores import DbPackageIndex

        async def _no_catalog(*a, **kw):
            raise AssertionError("/healthz must not build the catalog")

        monkeypatch.setattr(DbPackageIndex, "get_catalog_dict", _no_catalog)
        monkeypatch.setattr(DbPackageIndex, "catalog_rows", _no_catalog)
        monkeypatch.setattr(DbPackageIndex, "get_bundles", _no_catalog)

        r = client.get("/healthz")
        assert r.status_code == 200, r.text
        # What the anonymous catalog lists: not the yanked one, not the private one.
        assert r.json()["packages_count"] == 1
        assert re.search(r"^cvcpkg_packages_total 1$", client.get("/metrics").text, re.M)
        stats = client.get("/v1/admin/stats", headers=_h(tok["admin"]))
        assert stats.status_code == 200 and stats.json()["packages_count"] == 1


# ── The cache itself ────────────────────────────────────────────


def _body(n: int = 1) -> CatalogBody:
    return CatalogBody.encode(
        n, [{"name": f"p{n}", "archive_url": "/v1/download/p.tar.zst", "description": ""}]
    )


class TestCatalogBody:
    def test_render_rewrites_only_relative_archive_urls(self):
        body = CatalogBody.encode(
            3,
            [
                {"name": "a", "archive_url": "/v1/download/a.tar.zst"},
                {"name": "b", "archive_url": "https://cdn.example/b.tar.zst"},
                # Text that looks like the key inside a value stays as it is.
                {"name": "c", "archive_url": "", "description": '"archive_url":"/x'},
            ],
        )
        out = json.loads(body.render("https://cvcpkg.example/"))
        assert [b["archive_url"] for b in out["bundles"]] == [
            "https://cvcpkg.example/v1/download/a.tar.zst",
            "https://cdn.example/b.tar.zst",
            "",
        ]
        assert out["bundles"][2]["description"] == '"archive_url":"/x'
        assert json.loads(body.render(""))["bundles"][0]["archive_url"] == "/v1/download/a.tar.zst"

    def test_render_escapes_the_base(self):
        out = json.loads(_body().render('http://we"ird\\host'))
        assert out["bundles"][0]["archive_url"] == 'http://we"ird\\host/v1/download/p.tar.zst'

    def test_etag_depends_on_body_and_base(self):
        a, b = _body(1), _body(2)
        assert a.etag("http://x") == _body(1).etag("http://x/")
        assert a.etag("http://x") != b.etag("http://x")
        assert a.etag("http://x") != a.etag("http://y")

    @pytest.mark.parametrize(
        "header, expected",
        [
            ('"abc"', True),
            ('W/"abc"', True),
            ('"zzz", "abc"', True),
            ("*", True),
            ('"zzz"', False),
            ("", False),
            ("abc", False),
        ],
    )
    def test_etag_matches(self, header, expected):
        assert etag_matches(header, '"abc"') is expected


class TestCatalogCache:
    def test_generation_advance_invalidates(self):
        cache = CatalogCache(ttl_seconds=60)
        builds = []

        async def build():
            builds.append(1)
            return _body(len(builds))

        async def run():
            first = await cache.get("k", build)
            assert await cache.get("k", build) is first
            catalog_cache.catalog_changed()
            assert await cache.get("k", build) is not first

        asyncio.run(run())
        assert len(builds) == 2
        assert (cache.hits, cache.misses) == (1, 2)

    def test_write_during_build_is_not_cached_as_current(self):
        cache = CatalogCache(ttl_seconds=60)
        builds = []

        async def build():
            builds.append(1)
            if len(builds) == 1:
                catalog_cache.catalog_changed()  # a publish lands mid-build
            return _body(len(builds))

        async def run():
            await cache.get("k", build)
            await cache.get("k", build)
            await cache.get("k", build)

        asyncio.run(run())
        assert len(builds) == 2

    def test_concurrent_misses_share_one_build(self):
        cache = CatalogCache(ttl_seconds=60)
        builds = []

        async def build():
            builds.append(1)
            await asyncio.sleep(0.01)
            return _body()

        async def run():
            return await asyncio.gather(*(cache.get("k", build) for _ in range(5)))

        results = asyncio.run(run())
        assert len(builds) == 1
        assert all(r is results[0] for r in results)

    def test_ttl_expires(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(catalog_cache.time, "monotonic", lambda: now[0])
        cache = CatalogCache(ttl_seconds=60)
        builds = []

        async def build():
            builds.append(1)
            return _body()

        async def run():
            await cache.get("k", build)
            now[0] += 59
            await cache.get("k", build)
            now[0] += 2
            await cache.get("k", build)

        asyncio.run(run())
        assert len(builds) == 2

    def test_zero_ttl_disables(self):
        cache = CatalogCache(ttl_seconds=0)
        builds = []

        async def build():
            builds.append(1)
            return _body()

        async def run():
            await cache.get("k", build)
            await cache.get("k", build)

        asyncio.run(run())
        assert len(builds) == 2

    def test_least_recently_used_scope_is_evicted(self):
        cache = CatalogCache(ttl_seconds=60, max_entries=2)
        builds = []

        def builder(key):
            async def build():
                builds.append(key)
                return _body()

            return build

        async def run():
            for key in ("a", "b", "a", "c", "a", "b"):
                await cache.get(key, builder(key))

        asyncio.run(run())
        assert builds == ["a", "b", "c", "b"]


# ── Invalidation timing and coverage of the stores ──────────────


class TestInvalidationTiming:
    def test_after_commit_hooks_wait_for_the_atomic_commit(self, tmp_path):
        from cvcpkg.server.db import (
            atomic_session,
            create_tables,
            dispose_engine,
            init_db,
            run_after_commit,
        )

        calls: list[int] = []

        async def run():
            init_db(f"sqlite+aiosqlite:///{tmp_path / 'hooks.db'}")
            await create_tables()
            try:
                run_after_commit(lambda: calls.append(1))  # no transaction: now
                assert calls == [1]
                async with atomic_session():
                    run_after_commit(lambda: calls.append(2))
                    assert calls == [1]
                assert calls == [1, 2]
                with pytest.raises(RuntimeError):
                    async with atomic_session():
                        run_after_commit(lambda: calls.append(3))
                        raise RuntimeError("rolled back")
                assert calls == [1, 2]
            finally:
                await dispose_engine()

        asyncio.run(run())

    def test_store_write_in_a_transaction_advances_on_commit(self, tmp_path):
        from cvcpkg.server.db import atomic_session, create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbPackageIndex

        async def run():
            init_db(f"sqlite+aiosqlite:///{tmp_path / 'gen.db'}")
            await create_tables()
            idx = DbPackageIndex()
            try:
                before = catalog_cache.generation()
                async with atomic_session():
                    await idx.add_package(
                        name="zlib",
                        version="1.0",
                        platform="linux",
                        arch="x86_64",
                        build_type="release",
                        link="shared",
                        sha256="0" * 64,
                        size_bytes=1,
                        archive_url="/v1/download/zlib.tar.zst",
                    )
                    # Not committed: a fetch now would still read the old rows.
                    assert catalog_cache.generation() == before
                assert catalog_cache.generation() > before
            finally:
                await dispose_engine()

        asyncio.run(run())


# Store methods that write without changing what any catalog scope contains.
# Membership decides which scope a caller maps to, not what a scope holds, and
# storage accounting is not in the catalog.
_WRITES_OUTSIDE_THE_CATALOG = {"add_member", "remove_member", "update_storage_used"}

_READS = {
    "DbPackageIndex": {
        "get_archive_org",
        "get_archive_coords",
        "get_archive_is_yanked",
        "get_bundles",
        "get_search_facets",
        "catalog_scope",
        "catalog_rows",
        "get_catalog_dict",
        "count_catalog_bundles",
        "check_duplicate",
        "mirrored_keys",
        "stranded_upstreams",
        "get_tombstones",
        "get_tombstone_by_filename",
        "archive_filename_states",
        "get_release_tags",
        "total_storage_bytes",
        "cache_stats",
    },
    "DbOrgStore": {
        "get",
        "list_orgs",
        "member_org_slugs",
        "private_slugs",
        "get_members",
        "is_member",
        "is_owner",
        "check_storage_limit",
    },
}


@pytest.mark.parametrize("cls_name", sorted(_READS))
def test_every_catalog_write_invalidates(cls_name):
    """A new store method must say whether it changes the catalog.

    A write that skips ``@changes_catalog`` would leave /v1/catalog stale for
    up to the cache TTL, which no other test would notice.
    """
    from cvcpkg.server import db_stores

    cls = getattr(db_stores, cls_name)
    for name, member in vars(cls).items():
        if name.startswith("_") or not inspect.iscoroutinefunction(member):
            continue
        marked = getattr(member, "changes_catalog", False)
        if name in _READS[cls_name] or name in _WRITES_OUTSIDE_THE_CATALOG:
            assert not marked, f"{cls_name}.{name} is listed as not changing the catalog"
        else:
            assert marked, (
                f"{cls_name}.{name} is neither @changes_catalog nor listed as a read here; "
                "decorate it if it writes packages or organizations"
            )
