"""The audit log must tolerate an unknown action string in a stored row.

``DbAuditLog.record`` reads the newest row on every write to chain the
hash; if that row's ``action`` is not in the ``AuditAction`` enum (e.g. a
hand-inserted operator entry) a naive ``AuditAction(row.action)`` raises
and 500s every subsequent publish/push. Regression for the July 2026
prod incident where hand-inserted ``package_integrity_fix`` rows bricked
writes on cvcpkg.org.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")


def _run(tmp_path, monkeypatch, coro_fn):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _inner():
        init_db(db_url)
        await create_tables()
        try:
            return await coro_fn()
        finally:
            await dispose_engine()

    return asyncio.run(_inner())


def test_record_tolerates_unknown_action_in_last_row(tmp_path, monkeypatch):
    from cvcpkg.server.db import get_session
    from cvcpkg.server.db_stores import DbAuditLog
    from cvcpkg.server.models import AuditAction

    async def _t():
        from cvcpkg.server.db import AuditRow

        audit = DbAuditLog()
        # A legitimate first entry.
        await audit.record(AuditAction.publish, actor="ci", target="zlib==1.0")

        # A hand-inserted row with an action string not in the enum
        # (as an operator might INSERT directly).
        async with get_session() as session:
            session.add(
                AuditRow(action="some_unknown_manual_action", actor="ops", target="x", detail="")
            )
            await session.flush()

        # This read-newest-then-write path must NOT raise.
        entry = await audit.record(AuditAction.build_submit, actor="ci", target="zlib@linux")
        assert entry.prev_sha256  # chained off the unknown-action row without crashing

        # Listing must also tolerate the unknown row.
        entries, total = await audit.entries(limit=10)
        assert total == 3
        actions = [e.action for e in entries]
        # Known rows keep their value; the unknown one degrades gracefully.
        assert AuditAction.publish in actions
        assert AuditAction.build_submit in actions

    _run(tmp_path, monkeypatch, _t)


def test_package_integrity_fix_is_a_valid_action():
    from cvcpkg.server.models import AuditAction

    # The value hand-inserted during the prod integrity fix is now a
    # first-class enum member, so those rows deserialize exactly.
    assert AuditAction("package_integrity_fix") is AuditAction.package_integrity_fix
