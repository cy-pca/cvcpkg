"""A build job may only ever be claimed by one worker.

Two workers that both build one variant race each other's publish, and the
losing publish errors.  Combined with a builder that swallowed publish errors,
that produced the worst possible outcome: two jobs both reporting success while
nothing reached the catalog.  These tests pin both halves shut.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import BuildJobAlreadyClaimed, BuildJobStatus, TokenRole


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Store-level: the claim itself must be exclusive ─────────────


class TestClaimExclusivity:
    """DbBuildJobStore.claim must hand a job to exactly one worker."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}"
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

    async def _two_builders_and_job(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        bstore = DbBuilderStore()
        b1 = await bstore.register(
            name="b1", platform="linux", arch="x86_64", registered_by="a"
        )
        b2 = await bstore.register(
            name="b2", platform="linux", arch="x86_64", registered_by="a"
        )
        store = DbBuildJobStore()
        job = await store.create(
            recipe_name="zlib",
            platform="linux",
            arch="x86_64",
            submitted_by="admin",
        )
        return store, b1, b2, job

    def test_second_claim_by_another_builder_is_refused(self):
        """The loser must be told no -- not handed the job.

        Regression: claim() used to return the job info for any non-claimable
        job, so a second builder got a full BuildJobInfo back and went on to
        build a variant the first builder was already building.
        """

        async def _test():
            store, b1, b2, job = await self._two_builders_and_job()

            first = await store.claim(job.id, b1.id)
            assert first is not None
            assert first.status == BuildJobStatus.running
            assert first.builder_id == b1.id

            with pytest.raises(BuildJobAlreadyClaimed) as excinfo:
                await store.claim(job.id, b2.id)
            assert excinfo.value.job_id == job.id

            # The winner still owns it; the loser did not steal the row.
            still = await store.get(job.id)
            assert still.builder_id == b1.id
            assert still.status == BuildJobStatus.running

        self._run(_test())

    def test_concurrent_claims_exactly_one_wins(self):
        """Under a race, one claim succeeds and the rest raise."""

        async def _test():
            store, b1, b2, job = await self._two_builders_and_job()

            results = await asyncio.gather(
                store.claim(job.id, b1.id),
                store.claim(job.id, b2.id),
                return_exceptions=True,
            )
            winners = [r for r in results if not isinstance(r, Exception)]
            losers = [r for r in results if isinstance(r, BuildJobAlreadyClaimed)]

            assert len(winners) == 1, f"expected exactly one winner, got {results}"
            assert len(losers) == 1, f"expected exactly one refusal, got {results}"

            # The row must agree with whoever won.
            row = await store.get(job.id)
            assert row.builder_id == winners[0].builder_id
            assert row.status == BuildJobStatus.running

        self._run(_test())

    def test_reclaim_by_the_holder_still_refuses_at_store_level(self):
        """Idempotency is an API concern, not a store one.

        The store's job is the atomic transition; it refuses any claim of an
        already-running job.  The endpoint is what recognises "you already hold
        this" and answers 200 (see TestClaimEndpointConflict).
        """

        async def _test():
            store, b1, _b2, job = await self._two_builders_and_job()
            await store.claim(job.id, b1.id)
            with pytest.raises(BuildJobAlreadyClaimed):
                await store.claim(job.id, b1.id)

        self._run(_test())

    def test_claim_of_missing_job_returns_none_not_raises(self):
        """A job that does not exist is a 404, distinct from a conflict."""

        async def _test():
            from cvcpkg.server.db_stores import DbBuildJobStore

            store = DbBuildJobStore()
            assert await store.claim(999_999, 1) is None

        self._run(_test())


# ── API-level: conflict vs idempotent re-claim ──────────────────


class TestClaimEndpointConflict:
    """The endpoint must 409 a rival and 200 the holder."""

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _seed():
            init_db(db_url)
            await create_tables()
            raw = await DbTokenStore(tmp_path).create("admin", TokenRole.admin)
            await dispose_engine()
            return raw

        token = asyncio.run(_seed())
        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            yield client, token

    def _setup_job(self, client, token):
        builders = []
        for name in ("b1", "b2"):
            r = client.post(
                "/v1/builders/register",
                headers=_auth(token),
                json={"name": name, "platform": "linux", "arch": "x86_64"},
            )
            assert r.status_code in (200, 201), r.text
            builders.append(r.json())
        r = client.post(
            "/v1/builds",
            headers=_auth(token),
            json={
                "recipe_name": "zlib",
                "platform": "linux",
                "arch": "x86_64",
            },
        )
        assert r.status_code in (200, 201), r.text
        return builders, r.json()["id"]

    def test_rival_builder_gets_409_not_the_job(self, env):
        client, token = env
        builders, job_id = self._setup_job(client, token)

        first = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builders[0]["id"]},
        )
        assert first.status_code == 200, first.text

        rival = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builders[1]["id"]},
        )
        assert rival.status_code == 409, (
            "a rival builder must be refused, never handed a running job: "
            f"got {rival.status_code} {rival.text}"
        )

    def test_holder_may_reclaim_idempotently(self, env):
        """A builder whose claim response was lost must be able to retry."""
        client, token = env
        builders, job_id = self._setup_job(client, token)

        for _ in range(2):
            r = client.post(
                f"/v1/builds/{job_id}/claim",
                headers=_auth(token),
                json={"builder_id": builders[0]["id"]},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == BuildJobStatus.running


# ── The publish half: a failed publish must fail the job ────────


class TestPublishFailureIsFatal:
    """A build that could not publish must not report success."""

    def test_builder_reraises_publish_failure(self):
        """Drift guard: the publish handler must re-raise, not continue.

        _publish_to_server does NOT raise for an already-published variant --
        it skips via _variant_exists up front, and both upload paths turn a 409
        into "skipped".  So any ClickException escaping it is a real failure,
        and swallowing it marks the job succeeded while the catalog has nothing.
        """
        import ast
        import inspect

        from cvcpkg.cli import _builder

        tree = ast.parse(inspect.getsource(_builder))

        def _is_direct_publish_stmt(stmt: ast.stmt) -> bool:
            """True when *stmt* is literally the `_publish_to_server(...)` call.

            Matching only a direct child of a try body picks the innermost
            guard.  Walking the whole subtree would also match the enclosing
            try, whose `except Exception` legitimately reports the job failed
            without re-raising.
            """
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                return False
            fn = stmt.value.func
            return (getattr(fn, "id", None) or getattr(fn, "attr", None)) == "_publish_to_server"

        guarded = [
            t
            for t in ast.walk(tree)
            if isinstance(t, ast.Try) and any(_is_direct_publish_stmt(s) for s in t.body)
        ]
        assert guarded, "no try/except directly wraps the _publish_to_server call"

        for try_node in guarded:
            for handler in try_node.handlers:
                # Every handler around the publish must end the job, not
                # continue past it.  A bare `raise` (or any raise) propagates to
                # the outer handler, which reports the job failed.
                raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
                assert raises, (
                    "the builder swallows a publish failure: this handler has no "
                    "raise, so the job is reported succeeded while the bundle is "
                    "absent from the catalog"
                )

    def test_already_published_variant_does_not_raise(self):
        """The premise the fix rests on: a 409 is 'skipped', never an error."""
        import inspect

        from cvcpkg.cli import _publish

        src = inspect.getsource(_publish)
        # Both upload paths must map 409 -> skipped rather than raising, so the
        # benign case never reaches the builder's handler.
        assert src.count('return "skipped"') >= 2, (
            "the simple and chunked upload paths must both treat 409 "
            "(already published) as skipped, not as an error"
        )
        assert "_variant_exists(" in src, (
            "publish must pre-check for an existing variant and skip it"
        )
