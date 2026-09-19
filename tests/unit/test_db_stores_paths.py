"""Direct unit tests for the DB package index, org, and tag stores.

These drive :mod:`cvcpkg.server.db_stores` against a fresh file-backed
aiosqlite database (one per test), reusing the ``TestDbMirrorStore`` harness
shape from ``test_mirror.py``: init the engine + tables in the autouse
fixture, then run all of a test's async work inside a single coroutine passed
to :func:`run` (one ``asyncio.run`` per test — the file-backed engine uses
``NullPool`` so nothing is bound to a stale event loop).

Focus: the query/admin paths and their empty / not-found / error branches —
``get_bundles``/``get_search_facets`` filters and visibility, yank/unyank,
delete/nuke/delete_by_link, check_duplicate, the GC sweeps, upstream
reconciliation, and org/tag listing.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required for db_stores tests")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required for db_stores tests")


@pytest.fixture(autouse=True)
def _db_store_env(tmp_path, monkeypatch):
    """Fresh file-backed sqlite DB + created tables for each test."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _init():
        init_db(db_url)
        await create_tables()

    asyncio.run(_init())
    try:
        yield
    finally:
        asyncio.run(dispose_engine())


def run(coro):
    return asyncio.run(coro)


async def _add(
    idx,
    *,
    name="zlib",
    version="1.0.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
    sha256="s" * 64,
    size_bytes=100,
    archive_url=None,
    **over,
):
    if archive_url is None:
        archive_url = f"/v1/download/{name}-{version}-{platform}-{arch}-{build_type}-{link}.tar.zst"
    await idx.add_package(
        name=name,
        version=version,
        platform=platform,
        arch=arch,
        build_type=build_type,
        link=link,
        sha256=sha256,
        size_bytes=size_bytes,
        archive_url=archive_url,
        **over,
    )


async def _backdate(*, published_at=None, yanked_at=None, name=None):
    """Rewind published_at / yanked_at on matching rows (test setup only)."""
    from sqlalchemy import update

    from cvcpkg.server.db import PackageRow, get_session

    values = {}
    if published_at is not None:
        values["published_at"] = published_at
    if yanked_at is not None:
        values["yanked_at"] = yanked_at
    async with get_session() as session:
        stmt = update(PackageRow).values(**values)
        if name is not None:
            stmt = stmt.where(PackageRow.name == name)
        await session.execute(stmt)


# ── DbPackageIndex: get_bundles filters + ordering ──────────────


class TestGetBundles:
    def test_empty_index(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            pkgs, total = await DbPackageIndex().get_bundles()
            assert pkgs == [] and total == 0

        run(_t())

    def test_name_and_pagination_and_publisher_email(self, tmp_path):
        from cvcpkg.server.db_stores import DbPackageIndex, DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t(tmp):
            tokens = DbTokenStore(tmp)
            await tokens.create("alice", TokenRole.publisher, email="alice@example.com")
            idx = DbPackageIndex()
            await _add(idx, name="boost", version="1.86", published_by="alice")
            await _add(idx, name="boost", version="1.87", published_by="alice")
            await _add(idx, name="zlib", version="1.3.1")

            pkgs, total = await idx.get_bundles(name="boost")
            assert total == 2
            assert {p.name for p in pkgs} == {"boost"}
            assert pkgs[0].published_by_email == "alice@example.com"

            page1, t1 = await idx.get_bundles(limit=1, offset=0)
            page2, t2 = await idx.get_bundles(limit=1, offset=1)
            assert t1 == 3 and t2 == 3
            assert len(page1) == 1 and len(page2) == 1

        run(_t(tmp_path))

    def test_version_order_within_name(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            # Publish an older version last so SQL publish-order != version order.
            await _add(idx, name="boost", version="1.85")
            await _add(idx, name="boost", version="1.87")
            await _add(idx, name="boost", version="1.86")
            ordered, _ = await idx.get_bundles(name="boost", order="version")
            assert [p.version for p in ordered] == ["1.87", "1.86", "1.85"]
            # published_at order leaves the SQL publish-order untouched.
            pub, _ = await idx.get_bundles(name="boost", order="published_at")
            assert {p.version for p in pub} == {"1.85", "1.87", "1.86"}

        run(_t())

    def test_variant_filters(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", platform="linux", arch="x86_64", link="shared",
                       build_type="release", recipe_version="r1", release_tag="")
            await _add(idx, name="b", platform="windows", arch="arm64", link="static",
                       build_type="debug", recipe_version="r2", release_tag="2026.1")
            assert (await idx.get_bundles(platform="windows"))[1] == 1
            assert (await idx.get_bundles(arch="arm64"))[1] == 1
            assert (await idx.get_bundles(link="static"))[1] == 1
            assert (await idx.get_bundles(build_type="debug"))[1] == 1
            assert (await idx.get_bundles(recipe_version="r1"))[1] == 1
            assert (await idx.get_bundles(release="2026.1"))[1] == 1
            assert (await idx.get_bundles(org_slug="acme"))[1] == 0

        run(_t())

    def test_search_matches_multiple_columns(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="libpng", description="PNG image codec", tags="graphics")
            await _add(idx, name="zlib", maintainer="jane", pkg_license="Zlib")
            assert (await idx.get_bundles(search="image"))[1] == 1
            assert (await idx.get_bundles(search="graphics"))[1] == 1
            assert (await idx.get_bundles(search="jane"))[1] == 1
            assert (await idx.get_bundles(search="Zlib"))[1] == 1
            assert (await idx.get_bundles(search="nomatch"))[1] == 0

        run(_t())

    def test_include_yanked_and_default_hides(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a")
            await _add(idx, name="b")
            await idx.yank("b", "1.0.0")
            _, live = await idx.get_bundles()
            _, allp = await idx.get_bundles(include_yanked=True)
            assert live == 1 and allp == 2

        run(_t())

    def test_caller_visibility_filter(self):
        from cvcpkg.server.db_stores import DbOrgStore, DbPackageIndex

        async def _t():
            orgs = DbOrgStore()
            await orgs.create(slug="puborg", display_name="Pub", is_private=False,
                              created_by="root")
            await orgs.create(slug="secret", display_name="Secret", is_private=True,
                              created_by="root")
            await orgs.add_member("secret", "bob")
            idx = DbPackageIndex()
            await _add(idx, name="base")  # public base (org_slug "")
            await _add(idx, name="pubpkg", org_slug="puborg")
            await _add(idx, name="secretpkg", org_slug="secret")

            # Anonymous named caller ("") sees base + public org, not the private one.
            _, anon = await idx.get_bundles(caller_token_name="")
            assert anon == 2
            # A member of the private org additionally sees its package.
            _, member = await idx.get_bundles(caller_token_name="bob")
            assert member == 3
            # None (internal/admin) disables filtering entirely.
            _, internal = await idx.get_bundles(caller_token_name=None)
            assert internal == 3

        run(_t())


# ── DbPackageIndex: facets ──────────────────────────────────────


class TestSearchFacets:
    def test_facets_and_totals(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="boost", platform="linux", link="shared", size_bytes=10,
                       pkg_license="BSL", tags="cpp,headers")
            await _add(idx, name="boost", platform="macos", link="static", size_bytes=20,
                       pkg_license="BSL", tags="cpp")
            await _add(idx, name="fftw3", platform="linux", link="static", size_bytes=30,
                       pkg_license="GPL", tags="math")
            facets, total, distinct_names, total_size = await idx.get_search_facets()
            assert total == 3
            assert distinct_names == 2
            assert total_size == 60
            platforms = dict(facets["platforms"])
            # Facet counts are distinct package NAMES per bucket.
            assert platforms == {"linux": 2, "macos": 1}
            assert dict(facets["links"]) == {"shared": 1, "static": 2}
            assert dict(facets["licenses"]) == {"BSL": 1, "GPL": 1}
            tags = dict(facets["tags"])
            assert tags["cpp"] == 1 and tags["headers"] == 1 and tags["math"] == 1

        run(_t())

    def test_facets_empty(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            facets, total, distinct, size = await DbPackageIndex().get_search_facets()
            assert total == 0 and distinct == 0 and size == 0
            assert facets["platforms"] == [] and facets["tags"] == []

        run(_t())

    def test_facets_respect_filters_and_max_buckets(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            for i in range(3):
                await _add(idx, name=f"p{i}", platform=f"plat{i}")
            await _add(idx, name="only-linux", platform="linux")
            facets, total, _, _ = await idx.get_search_facets(platform="linux")
            assert total == 1
            assert dict(facets["platforms"]) == {"linux": 1}
            # max_buckets caps the per-facet list length.
            facets2, _, _, _ = await idx.get_search_facets(max_buckets=2)
            assert len(facets2["platforms"]) == 2

        run(_t())

    def test_facets_visibility(self):
        from cvcpkg.server.db_stores import DbOrgStore, DbPackageIndex

        async def _t():
            orgs = DbOrgStore()
            await orgs.create(slug="secret", display_name="s", is_private=True,
                              created_by="root")
            idx = DbPackageIndex()
            await _add(idx, name="base")
            await _add(idx, name="hidden", org_slug="secret", size_bytes=999)
            _, total, _, size = await idx.get_search_facets(caller_token_name="")
            assert total == 1 and size == 100  # the private package is excluded

        run(_t())


# ── DbPackageIndex: catalog dict ────────────────────────────────


class TestCatalogDict:
    def test_admin_vs_anonymous_vs_member(self):
        from cvcpkg.server.db_stores import DbOrgStore, DbPackageIndex

        async def _t():
            orgs = DbOrgStore()
            await orgs.create(slug="pub", display_name="p", is_private=False, created_by="r")
            await orgs.create(slug="sec", display_name="s", is_private=True, created_by="r")
            await orgs.add_member("sec", "bob")
            idx = DbPackageIndex()
            await _add(idx, name="base", required_deps='[{"name":"dep","version":"^1"}]',
                       provides='["cvc::base"]')
            await _add(idx, name="pubpkg", org_slug="pub")
            await _add(idx, name="secpkg", org_slug="sec")

            anon = await idx.get_catalog_dict()
            assert {b["name"] for b in anon["bundles"]} == {"base", "pubpkg"}
            assert anon["revision"] == 3  # counts ALL rows, filtered view or not

            member = await idx.get_catalog_dict(caller_token_name="bob")
            assert {b["name"] for b in member["bundles"]} == {"base", "pubpkg", "secpkg"}

            admin = await idx.get_catalog_dict(is_admin=True)
            assert len(admin["bundles"]) == 3
            base = next(b for b in admin["bundles"] if b["name"] == "base")
            assert base["required_deps"] == [{"name": "dep", "version": "^1"}]
            assert base["provides"] == ["cvc::base"]

        run(_t())

    def test_yanked_hidden_unless_included(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a")
            await _add(idx, name="b")
            await idx.yank("b", "1.0.0")
            assert len((await idx.get_catalog_dict())["bundles"]) == 1
            assert len((await idx.get_catalog_dict(include_yanked=True))["bundles"]) == 2

        run(_t())


# ── DbPackageIndex: duplicate / add ─────────────────────────────


class TestCheckDuplicateAndAdd:
    def test_check_duplicate(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib", version="1.0.0")
            assert await idx.check_duplicate(
                "zlib", "1.0.0", "linux", "x86_64", "release", "shared"
            ) is True
            assert await idx.check_duplicate(
                "zlib", "9.9.9", "linux", "x86_64", "release", "shared"
            ) is False

        run(_t())

    def test_add_duplicate_raises(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib")
            with pytest.raises(ValueError, match="already exists"):
                await _add(idx, name="zlib")
            # Same variant key in an org names the org in the error.
            await _add(idx, name="zlib", org_slug="acme")
            with pytest.raises(ValueError, match="in org 'acme'"):
                await _add(idx, name="zlib", org_slug="acme")

        run(_t())


# ── DbPackageIndex: yank / unyank / delete ──────────────────────


class TestYankDelete:
    def test_yank_scoped_and_reyank_keeps_clock(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib", version="1.0", platform="linux")
            await _add(idx, name="zlib", version="1.0", platform="windows")
            n = await idx.yank("zlib", "1.0", platform="linux")
            assert n == 1
            # Grab the yanked_at that was set.
            pkgs, _ = await idx.get_bundles(name="zlib", platform="linux",
                                            include_yanked=True)
            first_at = pkgs[0].yanked_at
            # Re-yank the whole version: coalesce keeps the original clock.
            n2 = await idx.yank("zlib", "1.0")
            assert n2 == 2
            pkgs2, _ = await idx.get_bundles(name="zlib", platform="linux",
                                             include_yanked=True)
            assert pkgs2[0].yanked_at == first_at

        run(_t())

    def test_unyank_clears_clock(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib")
            await idx.yank("zlib", "1.0.0")
            n = await idx.unyank("zlib", "1.0.0", platform="linux")
            assert n == 1
            pkgs, _ = await idx.get_bundles(name="zlib")
            assert pkgs[0].yanked is False and pkgs[0].yanked_at is None

        run(_t())

    def test_delete_scoped(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib", platform="linux")
            await _add(idx, name="zlib", platform="windows")
            n = await idx.delete("zlib", "1.0.0", platform="linux")
            assert n == 1
            _, total = await idx.get_bundles(include_yanked=True)
            assert total == 1

        run(_t())

    def test_delete_by_link(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", platform="linux", link="shared")
            await _add(idx, name="b", platform="linux", link="static")
            n = await idx.delete_by_link("linux", "static")
            assert n == 1
            _, total = await idx.get_bundles()
            assert total == 1

        run(_t())


# ── DbPackageIndex: nuke + tombstones ───────────────────────────


class TestNukeAndTombstones:
    def test_nuke_no_match(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            res = await DbPackageIndex().nuke_bundles("ghost", "0.0.0")
            assert res == {"nuked": [], "count": 0}

        run(_t())

    def test_nuke_requires_yanked(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib")
            with pytest.raises(ValueError, match="not yanked"):
                await idx.nuke_bundles("zlib", "1.0.0")

        run(_t())

    def test_nuke_yanked_writes_tombstone(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib")
            await idx.yank("zlib", "1.0.0")
            res = await idx.nuke_bundles("zlib", "1.0.0", nuked_by="admin",
                                         reason="manual")
            assert res["count"] == 1
            _, total = await idx.get_bundles(include_yanked=True)
            assert total == 0
            tombs = await idx.get_tombstones("zlib")
            assert len(tombs) == 1
            assert tombs[0]["reason"] == "manual"
            assert tombs[0]["nuked_by"] == "admin"

        run(_t())

    def test_nuke_deletes_archive_when_unreferenced(self, monkeypatch):
        from cvcpkg.server import archive_store
        from cvcpkg.server.db_stores import DbPackageIndex

        deleted = []
        monkeypatch.setattr(archive_store, "delete",
                            lambda uri, fname: deleted.append(fname) or True)

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib",
                       archive_url="/v1/download/zlib-1.0.0-linux.tar.zst")
            await idx.yank("zlib", "1.0.0")
            res = await idx.nuke_bundles("zlib", "1.0.0", storage_uri="file:///tmp")
            assert res["nuked"][0]["archive_deleted"] is True
            assert deleted == ["zlib-1.0.0-linux.tar.zst"]

        run(_t())

    def test_nuke_keeps_archive_if_still_referenced(self, monkeypatch):
        from cvcpkg.server import archive_store
        from cvcpkg.server.db_stores import DbPackageIndex

        monkeypatch.setattr(archive_store, "delete",
                            lambda uri, fname: (_ for _ in ()).throw(
                                AssertionError("must not delete")))

        async def _t():
            idx = DbPackageIndex()
            shared_url = "/v1/download/shared.tar.zst"
            await _add(idx, name="zlib", version="1.0", archive_url=shared_url)
            await _add(idx, name="zlib", version="2.0", archive_url=shared_url)
            await idx.yank("zlib", "1.0")
            res = await idx.nuke_bundles("zlib", "1.0", storage_uri="file:///tmp")
            # Another surviving row references the same file → not deleted.
            assert res["nuked"][0]["archive_deleted"] is False

        run(_t())

    def test_get_tombstones_filters_and_by_filename(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib", platform="linux",
                       archive_url="/v1/download/zlib-linux.tar.zst")
            await _add(idx, name="zlib", platform="windows",
                       archive_url="/v1/download/zlib-win.tar.zst")
            await idx.yank("zlib", "1.0.0")
            await idx.nuke_bundles("zlib", "1.0.0", nuked_by="a")
            all_t = await idx.get_tombstones("zlib")
            assert len(all_t) == 2
            lin = await idx.get_tombstones("zlib", platform="linux")
            assert len(lin) == 1 and lin[0]["platform"] == "linux"
            byname = await idx.get_tombstone_by_filename("zlib-win.tar.zst")
            assert byname is not None and byname["platform"] == "windows"
            assert await idx.get_tombstone_by_filename("nope.tar.zst") is None
            assert await idx.get_tombstone_by_filename("") is None

        run(_t())


# ── DbPackageIndex: archive lookups ─────────────────────────────


class TestArchiveLookups:
    def test_get_archive_org_and_yanked(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="zlib", org_slug="acme",
                       archive_url="/v1/download/zlib_x.tar.zst")
            assert await idx.get_archive_org("zlib_x.tar.zst") == "acme"
            assert await idx.get_archive_org("missing.tar.zst") is None
            # Underscore is escaped, so it is a literal, not a wildcard.
            assert await idx.get_archive_is_yanked("zlib_x.tar.zst") is False
            await idx.yank("zlib", "1.0.0")
            assert await idx.get_archive_is_yanked("zlib_x.tar.zst") is True
            assert await idx.get_archive_is_yanked("missing.tar.zst") is None

        run(_t())


# ── DbPackageIndex: release tags / storage / cache stats ────────


class TestStatsAndTags:
    def test_release_tags(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", release_tag="")
            await _add(idx, name="b", release_tag="2026.1")
            await _add(idx, name="c", release_tag="2026.1")
            tags = await idx.get_release_tags()
            by = {t["tag"]: t["count"] for t in tags}
            assert by == {"": 1, "2026.1": 2}

        run(_t())

    def test_total_storage_bytes_excludes_yanked(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", size_bytes=100)
            await _add(idx, name="b", size_bytes=200)
            await idx.yank("b", "1.0.0")
            assert await idx.total_storage_bytes() == 100

        run(_t())

    def test_cache_stats(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", size_bytes=100)
            await _add(idx, name="b", size_bytes=50, org_slug="acme")
            stats = await idx.cache_stats()
            assert stats["total_packages"] == 2
            assert stats["total_size_bytes"] == 150
            assert stats["orgs"][""]["count"] == 1
            assert stats["orgs"]["acme"]["size_bytes"] == 50

        run(_t())


# ── DbPackageIndex: GC sweeps ───────────────────────────────────


class TestGcSweeps:
    def test_gc_by_age(self):
        import datetime

        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="old")
            await _add(idx, name="tagged", release_tag="2026.1")
            await _add(idx, name="fresh")
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
            await _backdate(published_at=old, name="old")
            await _backdate(published_at=old, name="tagged")
            deleted = await idx.gc_by_age(max_age_seconds=86400)
            names = {d["name"] for d in deleted}
            assert names == {"old"}  # tagged is exempt, fresh is too new
            _, total = await idx.get_bundles()
            assert total == 2

        run(_t())

    def test_gc_by_storage(self):
        import datetime

        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="oldest", size_bytes=100)
            await _add(idx, name="newer", size_bytes=100)
            # Make "oldest" genuinely older so it is evicted first.
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
            await _backdate(published_at=old, name="oldest")
            assert await idx.gc_by_storage(max_bytes=1000) == []  # under budget
            deleted = await idx.gc_by_storage(max_bytes=100)
            assert [d["name"] for d in deleted] == ["oldest"]

        run(_t())

    def test_gc_by_staleness(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="keep", recipe_version="good")
            await _add(idx, name="drop", recipe_version="stale")
            deleted = await idx.gc_by_staleness({"good"})
            assert [d["name"] for d in deleted] == ["drop"]
            _, total = await idx.get_bundles()
            assert total == 1

        run(_t())

    def test_purge_yanked_disabled_and_dry_run_and_real(self):
        import datetime

        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="z")
            await idx.yank("z", "1.0.0")
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
            await _backdate(yanked_at=old, name="z")

            assert await idx.purge_yanked(older_than_days=0) == []  # disabled
            dry = await idx.purge_yanked(older_than_days=30, dry_run=True)
            assert len(dry) == 1
            _, still = await idx.get_bundles(include_yanked=True)
            assert still == 1  # dry-run kept it

            purged = await idx.purge_yanked(older_than_days=30)
            assert len(purged) == 1
            _, gone = await idx.get_bundles(include_yanked=True)
            assert gone == 0
            # A retention tombstone explains the removal.
            tombs = await idx.get_tombstones("z")
            assert tombs and tombs[0]["reason"] == "retention"

        run(_t())


# ── DbPackageIndex: upstream reconciliation ─────────────────────


class TestUpstreamReconcile:
    def test_mirrored_keys_and_stranded(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            assert await idx.mirrored_keys("") == set()
            await _add(idx, name="a", origin_upstream="http://up")
            await _add(idx, name="b", origin_upstream="http://old")
            keys = await idx.mirrored_keys("http://up")
            assert ("a", "1.0.0", "linux", "x86_64", "release", "shared") in keys
            stranded = await idx.stranded_upstreams("http://up")
            assert stranded == {"http://old": 1}

        run(_t())

    def test_reconcile_empty_upstream(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            counts = await DbPackageIndex().reconcile_from_upstream(
                "", upstream_yanked=set(), upstream_present=set(),
                upstream_tombstoned=set())
            assert counts == {"yanked": 0, "tombstoned": 0, "ambiguous": 0,
                              "overridden": 0, "unyanked": 0}

        run(_t())

    def test_reconcile_yank_tombstone_ambiguous(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        up = "http://up"

        def key(name):
            return (name, "1.0.0", "linux", "x86_64", "release", "shared")

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="yankme", origin_upstream=up)
            await _add(idx, name="tombme", origin_upstream=up)
            await _add(idx, name="ambme", origin_upstream=up)
            counts = await idx.reconcile_from_upstream(
                up,
                upstream_yanked={key("yankme")},
                upstream_present=set(),
                upstream_tombstoned={key("tombme")},
            )
            assert counts["yanked"] == 1
            assert counts["tombstoned"] == 1
            assert counts["ambiguous"] == 1
            # tombme got a tombstone recorded
            assert await idx.get_tombstones("tombme")

        run(_t())

    def test_reconcile_present_unyanks_inherited(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        up = "http://up"

        def key(name):
            return (name, "1.0.0", "linux", "x86_64", "release", "shared")

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="p", origin_upstream=up)
            # First: upstream yanks it → we inherit the yank + record verdict.
            c1 = await idx.reconcile_from_upstream(
                up, upstream_yanked={key("p")}, upstream_present=set(),
                upstream_tombstoned=set())
            assert c1["yanked"] == 1
            # Then: upstream serves it again → we lift our inherited yank.
            c2 = await idx.reconcile_from_upstream(
                up, upstream_yanked=set(), upstream_present={key("p")},
                upstream_tombstoned=set())
            assert c2["unyanked"] == 1
            pkgs, _ = await idx.get_bundles(name="p")
            assert pkgs[0].yanked is False

        run(_t())

    def test_reconcile_override_after_local_unyank(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        up = "http://up"

        def key(name):
            return (name, "1.0.0", "linux", "x86_64", "release", "shared")

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="p", origin_upstream=up)
            await idx.reconcile_from_upstream(
                up, upstream_yanked={key("p")}, upstream_present=set(),
                upstream_tombstoned=set())
            # Operator overrides: locally unyank while upstream still yanks it.
            await idx.unyank("p", "1.0.0")
            counts = await idx.reconcile_from_upstream(
                up, upstream_yanked={key("p")}, upstream_present=set(),
                upstream_tombstoned=set())
            assert counts["overridden"] == 1
            pkgs, _ = await idx.get_bundles(name="p")
            assert pkgs[0].yanked is False  # local decision left standing

        run(_t())

    def test_reconcile_public_divergence(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        def key(name):
            return (name, "1.0.0", "linux", "x86_64", "release", "shared")

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="local")  # origin_upstream "" → local public build
            changed = await idx.reconcile_public_divergence({key("local")})
            assert changed == 1
            pkgs, _ = await idx.get_bundles(name="local")
            assert pkgs[0].diverges_upstream is True
            # Re-converge: flag clears.
            changed2 = await idx.reconcile_public_divergence(set())
            assert changed2 == 1
            pkgs2, _ = await idx.get_bundles(name="local")
            assert pkgs2[0].diverges_upstream is False
            # No-op when nothing changes.
            assert await idx.reconcile_public_divergence(set()) == 0

        run(_t())


# ── DbOrgStore ──────────────────────────────────────────────────


class TestDbOrgStore:
    def test_create_get_duplicate(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            info = await store.create(slug="acme", display_name="Acme", created_by="root")
            assert info.slug == "acme"
            assert (await store.get("acme")).display_name == "Acme"
            assert await store.get("ghost") is None
            with pytest.raises(ValueError, match="already exists"):
                await store.create(slug="acme", display_name="Dup", created_by="root")

        run(_t())

    def test_list_orgs_visibility_and_pagination(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            await store.create(slug="pub", display_name="Pub", created_by="root")
            await store.create(slug="sec", display_name="Sec", is_private=True,
                               created_by="root")
            await store.add_member("sec", "bob")

            anon, total = await store.list_orgs()
            assert {o.slug for o in anon} == {"pub"} and total == 1
            allo, tall = await store.list_orgs(include_private=True)
            assert tall == 2
            member, _ = await store.list_orgs(caller_token_name="bob")
            assert {o.slug for o in member} == {"pub", "sec"}
            page, _ = await store.list_orgs(include_private=True, limit=1, offset=1)
            assert len(page) == 1

        run(_t())

    def test_member_org_slugs(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            await store.create(slug="acme", display_name="A", created_by="root")
            await store.add_member("acme", "bob")
            assert await store.member_org_slugs("") == set()
            assert await store.member_org_slugs("bob") == {"acme"}
            assert await store.member_org_slugs("nobody") == set()

        run(_t())

    def test_update(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            await store.create(slug="acme", display_name="A", created_by="root")
            updated = await store.update("acme", display_name="Acme Inc",
                                         description="d", is_private=True,
                                         storage_limit_bytes=42, logo_url="l",
                                         homepage="h")
            assert updated.display_name == "Acme Inc"
            assert updated.is_private is True
            assert updated.storage_limit_bytes == 42
            assert await store.update("ghost", display_name="x") is None

        run(_t())

    def test_membership_operations(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            await store.create(slug="acme", display_name="A", created_by="alice")
            # Creator is auto-owner.
            assert await store.is_owner("acme", "alice") is True
            assert await store.is_member("acme", "alice") is True
            # add new member; duplicate returns False.
            assert await store.add_member("acme", "bob") is True
            assert await store.add_member("acme", "bob") is False
            assert await store.is_member("acme", "bob") is True
            assert await store.is_owner("acme", "bob") is False
            members = await store.get_members("acme")
            assert {m.token_name for m in members} == {"alice", "bob"}
            # add_member on a missing org raises.
            with pytest.raises(ValueError, match="not found"):
                await store.add_member("ghost", "bob")

        run(_t())

    def test_remove_member_and_last_owner_guard(self):
        from cvcpkg.server.db_stores import DbOrgStore
        from cvcpkg.server.models import OrgRole

        async def _t():
            store = DbOrgStore()
            await store.create(slug="acme", display_name="A", created_by="alice")
            # Removing the only owner is refused.
            with pytest.raises(ValueError, match="last owner"):
                await store.remove_member("acme", "alice")
            # Add a second owner, then the first can be removed.
            await store.add_member("acme", "carol", role=OrgRole.owner)
            assert await store.remove_member("acme", "alice") is True
            # Removing a non-member returns False.
            assert await store.remove_member("acme", "nobody") is False
            # Missing org raises.
            with pytest.raises(ValueError, match="not found"):
                await store.remove_member("ghost", "x")

        run(_t())

    def test_missing_org_lookups(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            assert await store.get_members("ghost") == []
            assert await store.is_member("ghost", "x") is False
            assert await store.is_owner("ghost", "x") is False
            assert await store.check_storage_limit("ghost", 10) is False
            # update_storage_used on a missing org is a no-op (no raise).
            await store.update_storage_used("ghost", 10)

        run(_t())

    def test_storage_accounting(self):
        from cvcpkg.server.db_stores import DbOrgStore

        async def _t():
            store = DbOrgStore()
            await store.create(slug="acme", display_name="A", created_by="root",
                               storage_limit_bytes=1000)
            await store.update_storage_used("acme", 400)
            assert (await store.get("acme")).storage_used_bytes == 400
            assert await store.check_storage_limit("acme", 500) is True
            assert await store.check_storage_limit("acme", 700) is False
            # Never goes negative.
            await store.update_storage_used("acme", -100000)
            assert (await store.get("acme")).storage_used_bytes == 0

        run(_t())


# ── DbTagStore ──────────────────────────────────────────────────


class TestDbTagStore:
    def test_create_update_get_delete(self):
        from cvcpkg.server.db_stores import DbTagStore

        async def _t():
            store = DbTagStore()
            info = await store.create(name="graphics", description="viz",
                                      created_by="root")
            assert info.name == "graphics" and info.display_name == "graphics"
            with pytest.raises(ValueError, match="already exists"):
                await store.create(name="graphics")
            # Org-scoped duplicate names the org in the error.
            await store.create(name="x", org_slug="acme")
            with pytest.raises(ValueError, match="acme/x"):
                await store.create(name="x", org_slug="acme")
            # update() on a missing tag returns None (no row → no _row_to_info).
            assert await store.update(name="ghost") is None
            got = await store.get(name="graphics")
            assert got.display_name == "graphics"
            assert await store.get(name="ghost") is None
            assert await store.delete(name="graphics") is True
            assert await store.delete(name="graphics") is False

        run(_t())

    def test_ensure_tags_and_counts(self):
        from cvcpkg.server.db_stores import DbTagStore

        async def _t():
            store = DbTagStore()
            await store.ensure_tags(tags_csv="")  # no-op
            await store.ensure_tags(tags_csv="  ,  ")  # only blanks → no-op
            await store.ensure_tags(tags_csv="Cpp, Headers", created_by="root")
            got = await store.get(name="cpp")
            assert got is not None  # normalized to lowercase
            # ensure_tags again is idempotent (existing rows not duplicated).
            await store.ensure_tags(tags_csv="cpp")
            tags, total = await store.list_tags()
            assert total == 2

        run(_t())

    def test_get_with_package_count(self):
        from cvcpkg.server.db_stores import DbPackageIndex, DbTagStore

        async def _t():
            idx = DbPackageIndex()
            await _add(idx, name="a", tags="graphics")
            await _add(idx, name="b", tags="graphics,cli")
            store = DbTagStore()
            await store.create(name="graphics")
            got = await store.get(name="graphics")
            assert got.package_count == 2

        run(_t())

    def test_list_tags_org_filter_and_pagination(self):
        from cvcpkg.server.db_stores import DbTagStore

        async def _t():
            store = DbTagStore()
            await store.create(name="a")
            await store.create(name="b", org_slug="acme")
            await store.create(name="c", org_slug="acme")
            base, total = await store.list_tags(org_slug="")
            assert total == 1 and base[0].name == "a"
            org, ototal = await store.list_tags(org_slug="acme")
            assert ototal == 2
            page, _ = await store.list_tags(limit=1)
            assert len(page) == 1

        run(_t())

    def test_list_all_tag_names_curated_and_adhoc(self):
        from cvcpkg.server.db_stores import DbPackageIndex, DbTagStore

        async def _t():
            store = DbTagStore()
            await store.create(name="curated", description="c")
            idx = DbPackageIndex()
            await _add(idx, name="pkg1", tags="adhoc,curated")
            await _add(idx, name="pkg2", tags="adhoc")
            names = await store.list_all_tag_names()
            by = {t["name"]: t for t in names}
            assert "curated" in by and "adhoc" in by
            assert by["adhoc"]["package_count"] == 2

        run(_t())
