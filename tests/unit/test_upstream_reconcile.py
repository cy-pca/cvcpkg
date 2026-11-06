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

    async def _add(
        self,
        store,
        name,
        *,
        origin,
        yanked=False,
        version="1.0.0+cvc.1",
        platform="linux",
        sha256="0" * 64,
        org="",
    ):
        await store.add_package(
            name=name,
            version=version,
            platform=platform,
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256=sha256,
            size_bytes=1,
            archive_url=f"/v1/download/{name}-{platform}{org}.tar.zst",
            org_slug=org,
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
            assert all(v == 0 for v in counts.values()), counts

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

        A yank is recoverable (it is lifted when upstream lists the bundle
        again); a tombstone claims upstream deliberately destroyed the bundle,
        which we have no evidence of here.
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

    async def _walk(self, store, name, steps, *, listed="0" * 64):
        """Run *steps* against one mirrored bundle; return its row and the last counts.

        ``P``/``Y``/``T``/``V``: a sync where upstream lists the bundle (with
        sha256 *listed*), yanks it, has nuked it (tombstone), or dropped it with
        no tombstone; ``P*`` lists it with other bytes.  ``yank``/``unyank``: an
        operator here.
        """
        k, counts = key(name), None
        for step in steps:
            if step in ("yank", "unyank"):
                await getattr(store, step)(name, "1.0.0+cvc.1", actor="joe")
                continue
            present = step in ("P", "P*")
            counts = await store.reconcile_from_upstream(
                UPSTREAM,
                upstream_yanked={k} if step == "Y" else set(),
                upstream_present={k} if present else set(),
                upstream_tombstoned={k} if step == "T" else set(),
                upstream_sha256={k: listed if step == "P" else "f" * 64} if present else {},
            )
        rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
        return next(r for r in rows if r.name == name), counts

    def test_a_vanished_bundle_is_unyanked_when_upstream_lists_it_again(self):
        """The ambiguous yank is recoverable, so the next sync must recover it.

        It did not: only a yank that recorded upstream's verdict was lifted, and
        this one records none.  On 2026-10-31 dev ran migration 036 before
        cvcpkg.org did; its relabelled none/none rows looked missing upstream,
        were yanked, and 1,024 of them stayed yanked after cvcpkg.org caught up.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "flit-core", origin=UPSTREAM)

            row, gone = await self._walk(store, "flit-core", ["V"])
            assert gone["ambiguous"] == 1
            assert row.yanked is True
            assert row.upstream_yanked is False, "an absence is not upstream's ruling"

            row, back = await self._walk(store, "flit-core", ["P"])
            assert back["unyanked"] == 1
            assert row.yanked is False, "upstream serves it again, so must we"
            assert row.yanked_at is None

        self._run(_t())

    def test_a_yank_made_here_is_not_lifted_when_upstream_returns(self):
        """Only the reconciler's own yank follows upstream back.

        Whether an operator yanked before the bundle vanished or after the
        reconciler's yank, theirs is the newest yank and it stays.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            for name, steps in (
                ("bad-build", ["yank", "V", "P"]),
                ("bad-later", ["V", "unyank", "yank", "P"]),
            ):
                await self._add(store, name, origin=UPSTREAM)
                row, counts = await self._walk(store, name, steps)
                assert row.yanked is True, f"{name}: upstream's catalog lifted a local yank"
                assert counts["unyanked"] == 0, name

        self._run(_t())

    def test_whose_yank_it_is_is_decided_per_variant_and_org(self):
        """Events of another variant, or of another org's row at the same
        coordinates, must not decide whose yank a row carries."""

        async def _t():
            from cvcpkg.server.db import PackageEventRow, get_session
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            lin, win = key("pair"), key("pair", platform="windows")
            await self._add(store, "pair", origin=UPSTREAM)
            await self._add(store, "pair", origin=UPSTREAM, platform="windows")
            # The operator yanks linux only; then both vanish and come back.
            await store.yank("pair", "1.0.0+cvc.1", platform="linux", actor="joe")
            for present in (set(), {lin, win}):
                await store.reconcile_from_upstream(
                    UPSTREAM,
                    upstream_yanked=set(),
                    upstream_present=present,
                    upstream_tombstoned=set(),
                    upstream_sha256={k: "0" * 64 for k in present},
                )
            rows, _ = await store.get_bundles(include_yanked=True, limit=100, offset=0)
            assert {r.platform: r.yanked for r in rows if r.name == "pair"} == {
                "linux": True,
                "windows": False,
            }

            # A newer yank event of an org row with the same coordinates.
            await self._add(store, "shared", origin=UPSTREAM)
            await self._walk(store, "shared", ["V"])
            async with get_session() as s:
                s.add(
                    PackageEventRow(
                        kind="yank",
                        name="shared",
                        version="1.0.0+cvc.1",
                        platform="linux",
                        arch="x86_64",
                        build_type="release",
                        link="shared",
                        org_slug="acme",
                        actor="joe",
                    )
                )
            row, counts = await self._walk(store, "shared", ["P"])
            assert row.yanked is False, "another org's event decided the public row"
            assert counts["unyanked"] == 1

        self._run(_t())

    def test_a_bundle_relisted_with_other_bytes_stays_yanked(self):
        """A coordinate upstream nuked and republished holds other bytes now.

        Populate never re-imports a key it already holds, so lifting the yank
        would serve the old bytes as live.  It does not matter how the
        reconciler saw the bundle go: nuked, yanked then nuked, or merely
        vanished because the tombstone lookup failed.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            for name, steps in (
                ("vanished-nuked", ["V", "T", "P*"]),
                ("yanked-nuked", ["Y", "T", "P*"]),
                ("lookup-failed", ["V", "P*"]),
            ):
                await self._add(store, name, origin=UPSTREAM)
                row, counts = await self._walk(store, name, steps)
                assert row.yanked is True, f"{name}: a relisting with other bytes lifted it"
                assert counts["mismatched"] == 1, name
                assert counts["unyanked"] == 0, name

        self._run(_t())

    def test_a_reimported_bundle_follows_upstream_again(self):
        """A coordinate's old tombstone must not pin its next incarnation.

        Upstream nukes the bundle; the mirror drops its copy; upstream
        republishes and populate imports the new bytes.  An upstream yank and
        unyank of that new row must still be followed.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "reborn", origin=UPSTREAM, sha256="1" * 64)
            await self._walk(store, "reborn", ["T"])
            await store.delete("reborn", "1.0.0+cvc.1", storage_uri="", tombstone=False)
            await self._add(store, "reborn", origin=UPSTREAM, sha256="2" * 64)

            row, counts = await self._walk(store, "reborn", ["Y", "P"], listed="2" * 64)
            assert row.yanked is False, "the old incarnation's tombstone pinned the new one"
            assert counts["unyanked"] == 1

        self._run(_t())

    def test_an_upstream_yank_after_a_vanish_is_enforced(self):
        """An unyank here while the bundle is missing upstream is not a dissent.

        It answers no ruling of upstream's, so it neither survives the next
        sync that still finds the bundle missing nor absorbs a later upstream
        yank as an override.
        """

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "stale", origin=UPSTREAM)
            row, counts = await self._walk(store, "stale", ["V", "unyank", "V"])
            assert row.yanked is True
            assert counts["ambiguous"] == 1

            await self._add(store, "cve", origin=UPSTREAM)
            row, counts = await self._walk(store, "cve", ["V", "unyank", "Y"])
            assert row.yanked is True, "an upstream yank was taken for an override"
            assert row.upstream_yanked is True
            assert counts["yanked"] == 1
            assert counts["overridden"] == 0

        self._run(_t())

    def test_a_relisting_clears_an_overridden_verdict(self):
        """Upstream unyanking ends the dissent: the flag that hides the bundle
        from default clients must clear even though there is no yank to lift."""

        async def _t():
            from cvcpkg.server.db_stores import DbPackageIndex

            store = DbPackageIndex()
            await self._add(store, "dissent", origin=UPSTREAM)
            row, counts = await self._walk(store, "dissent", ["Y", "unyank", "P"])
            assert row.yanked is False
            assert row.upstream_yanked is False
            assert counts["unyanked"] == 0

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
            assert all(v == 0 for v in counts.values()), counts

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
