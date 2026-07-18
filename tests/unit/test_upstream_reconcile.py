"""A mirror must agree with its upstream — and only about upstream's packages.

Populate only ever added.  A bundle yanked or nuked on cvcpkg.org carried on
being served by every mirror and edge indefinitely, so a client resolving
against a mirror got a different answer than it would have got upstream.

The dangerous half of fixing that is over-reach: an edge hosts its own
packages next to the mirrored ones, and "upstream doesn't have it" is not
evidence that a locally published package should disappear.  Provenance
(``origin_upstream``) is what keeps reconciliation inside its lane, and the
tests below spend most of their effort on that boundary rather than on the
happy path.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

UPSTREAM = "https://cvcpkg.org"
OTHER_UPSTREAM = "https://pkg.example.org"


def key(name, version="1.0.0+cvc.1", platform="linux", arch="x86_64", cfg="release", link="shared"):
    return (name, version, platform, arch, cfg, link)


class TestReconcileFromUpstream:
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield
        asyncio.run(dispose_engine())

    def _run(self, coro):
        return asyncio.run(coro)

    async def _add(self, store, name, *, origin, yanked=False, version="1.0.0+cvc.1"):
        await store.add_package(
            name=name,
            version=version,
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="0" * 64,
            size_bytes=1,
            archive_url=f"/v1/download/{name}.tar.zst",
            origin_upstream=origin,
        )
        if yanked:
            await store.yank(name, version)

    # ── the boundary that matters ───────────────────────────────

    def test_a_locally_published_package_is_never_touched(self):
        """The whole reason provenance exists.

        An edge publishes its own public packages.  Upstream has never heard of
        them, so a naive "not upstream => remove" would delete exactly the data
        the edge is there to serve.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "local-only", origin="")  # published here

            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked=set(),
                upstream_present=set(),  # upstream has nothing at all
                upstream_tombstoned=set(),
            )

            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            local = [r for r in rows if r.name == "local-only"]
            assert len(local) == 1
            assert local[0].yanked is False, "a locally published package must survive"
            assert counts == {"yanked": 0, "tombstoned": 0, "ambiguous": 0}

        self._run(_t())

    def test_a_bundle_from_a_different_upstream_is_never_touched(self):
        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "other-mirror", origin=OTHER_UPSTREAM)

            await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked=set(),
                upstream_present=set(),
                upstream_tombstoned=set(),
            )

            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            row = next(r for r in rows if r.name == "other-mirror")
            assert row.yanked is False, "only this upstream's rows are in scope"

        self._run(_t())

    # ── following upstream ──────────────────────────────────────

    def test_upstream_yank_propagates(self):
        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "zlib", origin=UPSTREAM)

            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked={key("zlib")},
                upstream_present=set(),
                upstream_tombstoned=set(),
            )

            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            row = next(r for r in rows if r.name == "zlib")
            assert row.yanked is True
            assert row.yanked_at is not None, "the retention clock must start"
            assert counts["yanked"] == 1

        self._run(_t())

    def test_upstream_nuke_yanks_and_tombstones_but_keeps_the_bytes(self):
        """Policy (a): stop serving it now, let the retention GC reclaim later.

        Deleting archive bytes the instant an upstream API call is observed
        would let one upstream mistake destroy data across every mirror at
        once; an air-gapped edge may hold the last copy.
        """

        async def _t():
            from cvcpkg.server.db_stores import NUKE_REASON_UPSTREAM, DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "readline", origin=UPSTREAM)

            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked=set(),
                upstream_present=set(),  # gone from the catalogue
                upstream_tombstoned={key("readline")},  # ...because it was nuked
            )

            assert counts["tombstoned"] == 1
            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            row = next(r for r in rows if r.name == "readline")
            assert row.yanked is True, "must stop being served"

            tombs = await store.get_tombstones("readline")
            assert len(tombs) == 1
            entry = tombs[0]
            reason = entry["reason"] if isinstance(entry, dict) else entry.reason
            assert reason == NUKE_REASON_UPSTREAM

        self._run(_t())

    def test_vanished_without_a_tombstone_is_yanked_not_tombstoned(self):
        """A truncated catalogue must not read as a deletion.

        Yank is recoverable (`cvcpkg unyank`); a tombstone claims upstream
        deliberately destroyed the bundle, which we have no evidence of here.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "mystery", origin=UPSTREAM)

            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked=set(),
                upstream_present=set(),
                upstream_tombstoned=set(),  # no evidence either way
            )

            assert counts["ambiguous"] == 1
            assert counts["tombstoned"] == 0
            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            assert next(r for r in rows if r.name == "mystery").yanked is True
            assert await store.get_tombstones("mystery") == []

        self._run(_t())

    def test_a_bundle_upstream_still_serves_is_left_alone(self):
        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "boost", origin=UPSTREAM)

            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked=set(),
                upstream_present={key("boost")},
                upstream_tombstoned=set(),
            )

            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            assert next(r for r in rows if r.name == "boost").yanked is False
            assert counts == {"yanked": 0, "tombstoned": 0, "ambiguous": 0}

        self._run(_t())

    def test_reconcile_is_idempotent(self):
        """Runs every sync cycle; repeating must not re-count or re-tombstone."""

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "openssh", origin=UPSTREAM)

            args = dict(
                upstream_yanked=set(),
                upstream_present=set(),
                upstream_tombstoned={key("openssh")},
            )
            first = await store.reconcile_from_upstream(UPSTREAM, **args)
            second = await store.reconcile_from_upstream(UPSTREAM, **args)

            assert first["tombstoned"] == 1
            # Already yanked, so no *new* yank is counted on the second pass.
            assert second["yanked"] == 0
            assert second["ambiguous"] == 0

        self._run(_t())

    def test_an_already_yanked_local_row_keeps_its_original_yanked_at(self):
        """Reconciling repeatedly must not keep postponing retention expiry."""

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "curl", origin=UPSTREAM, yanked=True)
            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            before = next(r for r in rows if r.name == "curl").yanked_at

            await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked={key("curl")},
                upstream_present=set(),
                upstream_tombstoned=set(),
            )

            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            assert next(r for r in rows if r.name == "curl").yanked_at == before

        self._run(_t())
