"""Audit-log atomicity and chain-serialization.

Covers the unit-of-work mechanism that makes a mutation and its audit
entry commit as one transaction (``atomic_session`` / ``_audit_txn``) and
the serialization that stops concurrent audited writes from forking the
tamper-evident hash chain.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import AuditAction, TokenRole

# ── Direct store-level mechanism tests ──────────────────────────


class TestAtomicSessionMechanism:
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        self.db_url = db_url
        self.tmp_path = tmp_path

    def _run(self, coro_fn):
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbAuditLog, DbTokenStore

        async def _wrapped():
            init_db(self.db_url)
            await create_tables()
            try:
                return await coro_fn(DbTokenStore(self.tmp_path), DbAuditLog())
            finally:
                await dispose_engine()

        return asyncio.run(_wrapped())

    def test_mutation_and_audit_commit_together(self):
        async def scenario(tokens, audit):
            from cvcpkg.server.db import atomic_session, audit_append_lock

            async with audit_append_lock():
                async with atomic_session():
                    raw = await tokens.create("alice", TokenRole.publisher)
                    await audit.record(AuditAction.token_create, "admin", "alice")
            assert await tokens.verify(raw) is not None
            entries, total = await audit.entries()
            assert total == 1 and entries[0].action == AuditAction.token_create

        self._run(scenario)

    def test_exception_rolls_back_both(self):
        async def scenario(tokens, audit):
            from cvcpkg.server.db import atomic_session, audit_append_lock

            with pytest.raises(RuntimeError):
                async with audit_append_lock():
                    async with atomic_session():
                        await tokens.create("bob", TokenRole.publisher)
                        await audit.record(AuditAction.token_create, "admin", "bob")
                        raise RuntimeError("boom after both writes")
            # Neither the token nor the audit row survived.
            assert all(t.name != "bob" for t in await tokens.list_tokens())
            _, total = await audit.entries()
            assert total == 0

        self._run(scenario)

    def test_audit_failure_rolls_back_mutation(self):
        # If the audit append fails, the mutation in the same unit of work
        # must NOT persist — the whole point of atomicity.
        async def scenario(tokens, audit):
            from cvcpkg.server.db import atomic_session, audit_append_lock

            async def boom(*a, **k):
                raise RuntimeError("audit write failed")

            with pytest.raises(RuntimeError):
                async with audit_append_lock():
                    async with atomic_session():
                        await tokens.create("carol", TokenRole.publisher)
                        # simulate the audit write blowing up
                        await boom()
            assert all(t.name != "carol" for t in await tokens.list_tokens())

        self._run(scenario)

    def test_concurrent_appends_do_not_fork_chain(self):
        async def scenario(tokens, audit):
            async def append(n):
                await audit.record(AuditAction.publish, "u", f"pkg{n}")

            await asyncio.gather(*[append(i) for i in range(32)])
            ok, msg = await audit.verify_chain()
            assert ok, msg
            _, total = await audit.entries()
            assert total == 32

        self._run(scenario)

    def test_concurrent_atomic_mutations_keep_chain_intact(self):
        async def scenario(tokens, audit):
            from cvcpkg.server.db import atomic_session, audit_append_lock

            async def create_and_audit(n):
                async with audit_append_lock():
                    async with atomic_session():
                        await tokens.create(f"user{n}", TokenRole.publisher)
                        await audit.record(AuditAction.token_create, "admin", f"user{n}")

            await asyncio.gather(*[create_and_audit(i) for i in range(16)])
            ok, msg = await audit.verify_chain()
            assert ok, msg
            _, total = await audit.entries()
            assert total == 16
            assert len(await tokens.list_tokens()) == 16

        self._run(scenario)

    def test_get_session_joins_ambient(self):
        async def scenario(tokens, audit):
            from cvcpkg.server.db import atomic_session, get_session, in_atomic_session

            assert not in_atomic_session()
            async with atomic_session() as outer:
                assert in_atomic_session()
                async with get_session() as inner:
                    assert inner is outer
            assert not in_atomic_session()

        self._run(scenario)


# ── Endpoint-level atomicity ────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'srv.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        await dispose_engine()
        return admin_raw

    admin_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, tmp_path


class TestEndpointAtomicity:
    def test_failed_audit_leaves_no_token(self, db_server_env, monkeypatch):
        # Force the audit append to fail; the create must roll back so no
        # orphan token is left without an audit trail.  (TestClient
        # re-raises the server exception by default, which is itself proof
        # the write path aborted; the rollback is the real assertion.)
        client, admin_tok, _ = db_server_env
        from cvcpkg.server import db_stores

        async def boom(self, *a, **k):
            raise RuntimeError("audit backend down")

        monkeypatch.setattr(db_stores.DbAuditLog, "_append", boom)
        with pytest.raises(RuntimeError, match="audit backend down"):
            client.post(
                "/v1/tokens",
                json={"name": "ghost", "role": "reader"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        # Undo the injected failure to inspect state.
        monkeypatch.undo()
        listing = client.get("/v1/tokens", headers={"Authorization": f"Bearer {admin_tok}"})
        names = {t["name"] for t in listing.json()["tokens"]}
        assert "ghost" not in names  # mutation rolled back with the failed audit

    def test_successful_mutation_is_audited(self, db_server_env):
        client, admin_tok, _ = db_server_env
        client.post(
            "/v1/tokens",
            json={"name": "logged", "role": "reader"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        audit = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_tok}"})
        entries = audit.json()["entries"]
        assert any(e["action"] == "token_create" and e["target"] == "logged" for e in entries)
        # And the chain still verifies.
        v = client.get("/v1/audit/verify", headers={"Authorization": f"Bearer {admin_tok}"})
        assert v.json().get("ok") is True

    def test_duplicate_mutation_writes_no_audit(self, db_server_env):
        # A 409 (duplicate) must not leave an audit row — the mutation
        # never happened.
        client, admin_tok, _ = db_server_env
        body = {"name": "dup", "role": "reader"}
        h = {"Authorization": f"Bearer {admin_tok}"}
        assert client.post("/v1/tokens", json=body, headers=h).status_code == 200
        assert client.post("/v1/tokens", json=body, headers=h).status_code == 409
        audit = client.get("/v1/audit", headers=h).json()["entries"]
        creates = [e for e in audit if e["action"] == "token_create" and e["target"] == "dup"]
        assert len(creates) == 1  # only the first, successful create
