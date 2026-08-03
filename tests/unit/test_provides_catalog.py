"""provides: slots must survive the publish → store → catalog round trip.

Every column of a script-shipping python package (pytest-cp311 ...
pytest-cp313t) declares ``provides: [pytest]``.  The client resolver
matches an install request for the slot name against its providers
(resolver virtual names) and the conflict layer makes providers mutually
exclusive — but both only work if the slot list actually reaches the
served catalog.  It used to die at the publish boundary: the recipe and
packed manifest carried it, the server dropped it (regression guarded
here), so after the retired bare-name packages are yanked
``cvcpkg install pytest`` would have found nothing.
"""

from __future__ import annotations

import asyncio

import pytest

aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for provides catalog tests")

from cvcpkg.catalog import catalog_entries


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'provides.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _setup():
        init_db(db_url)
        await create_tables()

    asyncio.run(_setup())
    yield db_url
    asyncio.run(dispose_engine())


def _add(store, name, provides="[]"):
    return store.add_package(
        name=name,
        version="9.1.1",
        platform="any",
        arch="noarch",
        build_type="release",
        link="shared",
        sha256="0" * 64,
        size_bytes=10,
        archive_url=f"/v1/download/{name}-9.1.1-any-noarch-release-shared.tar.zst",
        provides=provides,
    )


class TestProvidesRoundTrip:
    def test_catalog_dict_carries_provides(self, db):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _run():
            store = DbPackageIndex()
            await _add(store, "pytest-cp311", provides='["pytest"]')
            await _add(store, "click-cp311")  # no slots — the common case
            return await store.get_catalog_dict()

        cat = asyncio.run(_run())
        by_name = {b["name"]: b for b in cat["bundles"]}
        assert by_name["pytest-cp311"]["provides"] == ["pytest"]
        assert by_name["click-cp311"]["provides"] == []

    def test_client_entries_resolve_slot_names(self, db):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _run():
            store = DbPackageIndex()
            await _add(store, "pytest-cp311", provides='["pytest"]')
            await _add(store, "pytest-cp313", provides='["pytest"]')
            return await store.get_catalog_dict()

        cat = asyncio.run(_run())
        entries = catalog_entries(cat, platform="linux", arch="x86_64")
        providers = [e.name for e in entries if "pytest" in e.provides]
        assert sorted(providers) == ["pytest-cp311", "pytest-cp313"]
