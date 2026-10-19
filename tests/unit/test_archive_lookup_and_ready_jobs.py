"""Archive-name lookups and ready-job selection, as rewritten to stay off full
table scans: an indexed ``archive_name`` equality instead of a LIKE suffix
match, and a correlated NOT EXISTS instead of NOT IN over every unmet edge."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from cvcpkg.server.models import BuildJobStatus


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'lookups.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    asyncio.run(_init(init_db, create_tables, db_url))
    yield
    asyncio.run(dispose_engine())


async def _init(init_db, create_tables, url):
    init_db(url)
    await create_tables()


def _add(idx, name, version="1.0", *, url=None, org=""):
    fn = f"{name}-{version}-linux-x86_64-release-shared.tar.zst"
    return idx.add_package(
        name=name,
        version=version,
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256="0" * 64,
        size_bytes=1,
        archive_url=url if url is not None else f"/v1/download/{fn}",
        org_slug=org,
    )


class TestArchiveNameLookups:
    def test_archive_name_follows_archive_url(self, db):
        from sqlalchemy import select

        from cvcpkg.server.db import PackageRow, get_session
        from cvcpkg.server.db_stores import DbPackageIndex

        async def go():
            await _add(DbPackageIndex(), "zlib")
            async with get_session() as s:
                row = (await s.execute(select(PackageRow))).scalar_one()
                assert row.archive_name == "zlib-1.0-linux-x86_64-release-shared.tar.zst"
                row.archive_url = "https://m.example/v1/download/renamed.tar.zst"
                await s.flush()
                assert row.archive_name == "renamed.tar.zst"

        asyncio.run(go())

    def test_lookups_are_exact_and_include_yanked(self, db):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def go():
            idx = DbPackageIndex()
            await _add(idx, "a-b", org="acme")
            await _add(idx, "old")
            await idx.yank("old", "1.0", platform="linux", arch="x86_64")
            ab = "a-b-1.0-linux-x86_64-release-shared.tar.zst"
            # "_" was a LIKE wildcard: a_b's name must not resolve to a-b's row.
            a_under_b = "a_b-1.0-linux-x86_64-release-shared.tar.zst"
            old = "old-1.0-linux-x86_64-release-shared.tar.zst"
            assert await idx.get_archive_org(ab) == "acme"
            assert await idx.get_archive_org(a_under_b) is None
            assert await idx.get_archive_is_yanked(ab) is False
            assert await idx.get_archive_is_yanked(old) is True
            assert await idx.get_archive_is_yanked(a_under_b) is None
            coords = await idx.get_archive_coords(old)
            assert coords == {
                "name": "old",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
            }

        asyncio.run(go())


class TestFindReadyJobs:
    def test_ready_only_when_every_dependency_succeeded(self, db):
        from sqlalchemy import update

        from cvcpkg.server.db import BuildJobRow, get_session
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def go():
            st = DbBuildJobStore()
            new = lambda name, deps=None: st.create(  # noqa: E731
                name, "linux", "x86_64", "t", depends_on=deps or []
            )
            ok = await new("ok-dep")
            bad = await new("bad-dep")
            # Only the dependencies' statuses matter to the query; set them directly.
            async with get_session() as s:
                for job, status in ((ok, BuildJobStatus.succeeded), (bad, BuildJobStatus.failed)):
                    await s.execute(
                        update(BuildJobRow).where(BuildJobRow.id == job.id).values(status=status)
                    )
            free = await new("no-deps")
            after_ok = await new("after-ok", [ok.id])
            blocked = await new("blocked", [ok.id, bad.id])

            ready = await st.find_ready_jobs()
            by_name = {j.recipe_name: j for j in ready}
            assert set(by_name) == {"no-deps", "after-ok"}
            assert by_name["after-ok"].depends_on == [ok.id]
            assert by_name["no-deps"].depends_on == []
            assert blocked.id not in {j.id for j in ready}
            assert free.id in {j.id for j in ready}
            assert after_ok.id in {j.id for j in ready}

        asyncio.run(go())


class TestListJobsDagPrefix:
    def test_prefix_filter_matches_and_treats_underscore_literally(self, db):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def go():
            st = DbBuildJobStore()
            for dag in ("populate-1_a-x", "populate-1_a-y", "populate-1xa-z", "other"):
                await st.create("r", "linux", "x86_64", "t", dag_id=dag)
            jobs, total = await st.list_jobs(dag_id="populate-1_a*", limit=1000)
            assert total == 2
            assert sorted(j.dag_id for j in jobs) == ["populate-1_a-x", "populate-1_a-y"]
            jobs, total = await st.list_jobs(dag_id="populate-*", limit=1000)
            assert total == 3
            jobs, total = await st.list_jobs(dag_id="populate-1_a-x", limit=1000)
            assert total == 1

        asyncio.run(go())
