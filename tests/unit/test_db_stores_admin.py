"""Direct unit tests for the DB token, token-request, audit, download, and
telemetry stores.

Same harness shape as the other ``test_db_stores_*`` files.  Covers token
minting/verification (including the rotation grace window and delegated-
principal substitution), the user search/profile paths, the tamper-evident
audit chain, and the download/telemetry aggregation queries.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required for db_stores tests")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required for db_stores tests")


@pytest.fixture(autouse=True)
def _db_store_env(tmp_path, monkeypatch):
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


async def _add_principal(name, *, last_role="publisher", disabled=False):
    from cvcpkg.server.db import PrincipalRow, get_session

    async with get_session() as session:
        row = PrincipalRow(
            name=name,
            issuer="idp",
            subject=f"sub-{name}",
            last_role=last_role,
            disabled=disabled,
        )
        session.add(row)
        await session.flush()
        return row.id


# ── DbTokenStore: create / verify ───────────────────────────────


class TestDbTokenCreateVerify:
    def test_create_and_verify(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            raw = await store.create("alice", TokenRole.publisher, email="a@x.com", description="d")
            assert raw.startswith("cvctok_")
            rec = await store.verify(raw)
            assert rec is not None
            assert rec.name == "alice"
            assert rec.role == TokenRole.publisher
            assert rec.credential_kind == "token"
            # A bogus secret verifies to nothing.
            assert await store.verify("cvctok_nope") is None

        run(_t())

    def test_create_reserved_principal_name(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            await _add_principal("joe")
            store = DbTokenStore(tmp_path)
            with pytest.raises(ValueError, match="reserved identity name"):
                await store.create("joe", TokenRole.publisher)
            # Minting FOR that principal (principal_id set) is exempt.
            pid = await _principal_id("joe")
            raw = await store.create("joe.laptop", TokenRole.publisher, principal_id=pid)
            assert raw.startswith("cvctok_")

        run(_t())

    def test_create_duplicate_active_name(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            await store.create("alice", TokenRole.publisher)
            with pytest.raises(ValueError, match="already exists"):
                await store.create("alice", TokenRole.publisher)

        run(_t())

    def test_verify_revoked_and_expired(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            raw = await store.create("bob", TokenRole.publisher)
            await store.revoke("bob")
            assert await store.verify(raw) is None
            # An already-expired token does not verify.
            raw2 = await store.create("carol", TokenRole.publisher, expires_in_days=-1)
            assert await store.verify(raw2) is None

        run(_t())

    def test_verify_delegated_principal(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            pid = await _add_principal("joe", last_role="publisher")
            store = DbTokenStore(tmp_path)
            raw = await store.create("joe.laptop", TokenRole.publisher, principal_id=pid)
            rec = await store.verify(raw)
            assert rec is not None
            # Presents to the rest of the server AS the principal.
            assert rec.name == "joe"
            assert rec.credential_kind == "delegated"
            assert rec.credential_name == "joe.laptop"
            assert rec.role == TokenRole.publisher

        run(_t())

    def test_verify_disabled_principal(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            pid = await _add_principal("joe", disabled=True)
            store = DbTokenStore(tmp_path)
            raw = await store.create("joe.laptop", TokenRole.publisher, principal_id=pid)
            # A disabled principal invalidates every token it minted.
            assert await store.verify(raw) is None

        run(_t())

    def test_rotate_grace_window(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            old = await store.create("bob", TokenRole.publisher)
            new = await store.rotate("bob", grace_minutes=10)
            assert new and new != old
            # Both the new and (within the window) the old secret verify.
            new_rec = await store.verify(new)
            assert new_rec is not None and new_rec.name == "bob"
            old_rec = await store.verify(old)
            assert old_rec is not None and old_rec.via_previous_hash is True

        run(_t())

    def test_rotate_no_grace_invalidates_old(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            old = await store.create("bob", TokenRole.publisher)
            new = await store.rotate("bob")  # grace_minutes=0
            assert await store.verify(new) is not None
            assert await store.verify(old) is None  # old secret dead immediately

        run(_t())

    def test_rotate_missing_and_expired(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            assert await store.rotate("ghost") is None
            await store.create("carol", TokenRole.publisher, expires_in_days=-1)
            assert await store.rotate("carol") is None  # expired → dead on arrival

        run(_t())


# ── DbTokenStore: mutation + profile + search ───────────────────


class TestDbTokenAdmin:
    def test_revoke_email_profile(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            await store.create("bob", TokenRole.publisher)
            assert await store.revoke("bob") is True
            assert await store.revoke("bob") is False  # already revoked
            assert await store.revoke("ghost") is False
            await store.create("alice", TokenRole.publisher)
            assert await store.update_email("alice", "new@x.com") is True
            assert await store.update_email("ghost", "x") is False
            assert await store.update_profile("alice", description="d", metadata="m") is True
            # Nothing to update short-circuits to True.
            assert await store.update_profile("alice") is True
            assert await store.update_profile("ghost", description="d") is False
            prof = await store.get_public_profile("alice")
            assert prof.email == "new@x.com" and prof.description == "d"

        run(_t())

    def test_public_profile_and_by_email_and_counts(self, tmp_path):
        from cvcpkg.server.db_stores import DbPackageIndex, DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            await store.create("alice", TokenRole.publisher, email="a@x.com")
            idx = DbPackageIndex()
            await idx.add_package(
                name="zlib",
                version="1.0",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="s",
                size_bytes=1,
                archive_url="/v1/download/z.tar.zst",
                published_by="alice",
            )
            prof = await store.get_public_profile("alice")
            assert prof.packages_published == 1
            assert await store.get_public_profile("ghost") is None
            by_email = await store.get_profile_by_email("a@x.com")
            assert by_email.name == "alice"
            assert await store.get_profile_by_email("none@x.com") is None
            assert await store.count_packages_by_user("alice") == 1
            assert await store.count_packages_by_user("nobody") == 0

        run(_t())

    def test_search_users_filters_and_sort(self, tmp_path):
        from cvcpkg.server.db_stores import DbOrgStore, DbPackageIndex, DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            await store.create("alice", TokenRole.publisher, email="alice@x.com")
            await store.create("bob", TokenRole.admin, email="bob@y.com")
            await store.create("carol", TokenRole.publisher, email="carol@x.com")
            idx = DbPackageIndex()
            await idx.add_package(
                name="p",
                version="1",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="s",
                size_bytes=1,
                archive_url="/v1/download/p.tar.zst",
                published_by="alice",
            )
            orgs = DbOrgStore()
            await orgs.create(slug="acme", display_name="A", created_by="bob")

            users, total = await store.search_users(name="ali")
            assert total == 1 and users[0].name == "alice"
            _, by_email = await store.search_users(email="@x.com")
            assert by_email == 2
            _, admins = await store.search_users(role="admin")
            assert admins == 1
            _, in_org = await store.search_users(org="acme")
            assert in_org == 1  # only bob (the creator/owner)
            _, published = await store.search_users(has_published=True)
            assert published == 1
            _, unpublished = await store.search_users(has_published=False)
            assert unpublished == 2
            # sort by packages_published desc → alice (1) first.
            ranked, _ = await store.search_users(sort_by="packages_published", sort_order="desc")
            assert ranked[0].name == "alice"
            # sort by email asc.
            by_mail, _ = await store.search_users(sort_by="email", sort_order="asc")
            assert by_mail[0].email == "alice@x.com"
            # pagination
            page, ptotal = await store.search_users(limit=1, offset=0)
            assert ptotal == 3 and len(page) == 1

        run(_t())

    def test_active_bare_and_principal_tokens(self, tmp_path):
        from cvcpkg.server.db_stores import DbTokenStore
        from cvcpkg.server.models import TokenRole

        async def _t():
            store = DbTokenStore(tmp_path)
            await store.create("machine", TokenRole.publisher)
            await store.create("revoked-tok", TokenRole.publisher)
            await store.revoke("revoked-tok")
            pid = await _add_principal("joe")
            await store.create("joe.laptop", TokenRole.publisher, principal_id=pid)

            assert await store.active_bare_by_name("machine") is True
            assert await store.active_bare_by_name("revoked-tok") is False
            # A delegated token's row name is NOT a bare machine token.
            assert await store.active_bare_by_name("joe.laptop") is False
            assert await store.active_bare_by_name("ghost") is False
            names = await store.active_bare_names()
            assert "machine" in names
            assert "joe.laptop" not in names and "revoked-tok" not in names
            # tokens_for_principal filters to that principal's live tokens.
            toks = await store.tokens_for_principal(pid)
            assert [t.name for t in toks] == ["joe.laptop"]
            # list_tokens returns everything, revoked included.
            all_names = {t.name for t in await store.list_tokens()}
            assert {"machine", "revoked-tok", "joe.laptop"} <= all_names

        run(_t())


# ── DbTokenRequestStore ─────────────────────────────────────────


class TestDbTokenRequestStore:
    def test_create_list_get_resolve(self):
        from cvcpkg.server.db_stores import DbTokenRequestStore
        from cvcpkg.server.models import TokenRequestStatus, TokenRole

        async def _t():
            store = DbTokenRequestStore()
            rec = await store.create("newuser", "n@x.com", TokenRole.publisher)
            assert rec.name == "newuser"
            assert rec.status == TokenRequestStatus.pending
            got = await store.get(rec.id)
            assert got.email == "n@x.com"
            assert await store.get(9999) is None
            all_reqs = await store.list_requests()
            assert len(all_reqs) == 1
            pending = await store.list_requests(status=TokenRequestStatus.pending)
            assert len(pending) == 1
            assert await store.list_requests(status=TokenRequestStatus.approved) == []
            # Resolve moves it out of pending; a second resolve is a no-op.
            assert await store.resolve(rec.id, TokenRequestStatus.approved, "admin") is True
            assert await store.resolve(rec.id, TokenRequestStatus.approved, "admin") is False
            after = await store.get(rec.id)
            assert after.status == TokenRequestStatus.approved
            assert after.reviewed_by == "admin"

        run(_t())


# ── DbAuditLog ──────────────────────────────────────────────────


class TestDbAuditLog:
    def test_record_and_entries_filters(self):
        from cvcpkg.server.db_stores import DbAuditLog
        from cvcpkg.server.models import AuditAction

        async def _t():
            log = DbAuditLog()
            await log.record(AuditAction.publish, "alice", "zlib", "v1")
            await log.record(AuditAction.yank, "bob", "zlib", "v1")
            await log.record(AuditAction.publish, "alice", "png", "")
            entries, total = await log.entries()
            assert total == 3
            # Filter by action.
            pubs, ptotal = await log.entries(action=AuditAction.publish)
            assert ptotal == 2 and all(e.action == AuditAction.publish for e in pubs)
            # Filter by target.
            _, zlib_total = await log.entries(target="zlib")
            assert zlib_total == 2
            # Pagination.
            page, all_total = await log.entries(limit=1, offset=0)
            assert all_total == 3 and len(page) == 1

        run(_t())

    def test_verify_chain_intact_and_empty(self):
        from cvcpkg.server.db_stores import DbAuditLog
        from cvcpkg.server.models import AuditAction

        async def _t():
            log = DbAuditLog()
            ok, msg = await log.verify_chain()
            assert ok is True and "empty" in msg
            await log.record(AuditAction.publish, "a", "t", "")
            await log.record(AuditAction.yank, "b", "t", "")
            ok2, msg2 = await log.verify_chain()
            assert ok2 is True and "intact" in msg2

        run(_t())

    def test_verify_chain_detects_tampering(self):
        from sqlalchemy import update

        from cvcpkg.server.db import AuditRow, get_session
        from cvcpkg.server.db_stores import DbAuditLog
        from cvcpkg.server.models import AuditAction

        async def _t():
            log = DbAuditLog()
            await log.record(AuditAction.publish, "a", "t", "")
            await log.record(AuditAction.yank, "b", "t", "")
            # Tamper with the first entry's actor: the second entry's stored
            # prev hash no longer matches the recomputed first-entry hash.
            async with get_session() as session:
                await session.execute(
                    update(AuditRow).where(AuditRow.id == 1).values(actor="mallory")
                )
            ok, msg = await log.verify_chain()
            assert ok is False and "chain broken" in msg

        run(_t())

    def test_verify_chain_bad_first_entry(self):
        from cvcpkg.server.db import AuditRow, get_session
        from cvcpkg.server.db_stores import DbAuditLog
        from cvcpkg.server.models import AuditAction

        async def _t():
            # Insert a lone first row with a non-empty prev hash.
            async with get_session() as session:
                session.add(
                    AuditRow(
                        action=AuditAction.publish.value,
                        actor="a",
                        target="t",
                        detail="",
                        prev_sha256="bogus",
                    )
                )
            ok, msg = await DbAuditLog().verify_chain()
            assert ok is False and "first entry" in msg

        run(_t())

    def test_coerce_unknown_action(self):
        from cvcpkg.server.db import AuditRow, get_session
        from cvcpkg.server.db_stores import DbAuditLog
        from cvcpkg.server.models import AuditAction

        async def _t():
            async with get_session() as session:
                session.add(
                    AuditRow(
                        action="totally_unknown_action",
                        actor="a",
                        target="t",
                        detail="",
                        prev_sha256="",
                    )
                )
            entries, _ = await DbAuditLog().entries()
            # An unknown stored action deserializes to a safe fallback.
            assert entries[0].action == AuditAction.admin_settings_update

        run(_t())


# ── DbDownloadStore ─────────────────────────────────────────────


class TestDbDownloadStore:
    def test_record_and_totals(self):
        from cvcpkg.server.db_stores import DbDownloadStore

        async def _t():
            store = DbDownloadStore()
            await store.record(
                "zlib", "1.0", "linux", arch="x86_64", bytes_sent=100, cvcpkg_version="2.2.2"
            )
            await store.record("zlib", "1.0", "linux", bytes_sent=50)
            await store.record("boost", "1.86", "windows", bytes_sent=10)
            assert await store.get_total_downloads() == 3
            assert await store.get_total_downloads("zlib") == 2

        run(_t())

    def test_top_packages_and_platform_and_versions(self):
        from cvcpkg.server.db_stores import DbDownloadStore

        async def _t():
            store = DbDownloadStore()
            for _ in range(3):
                await store.record(
                    "zlib", "1.0", "linux", arch="x86_64", bytes_sent=10, cvcpkg_version="2.2.2"
                )
            await store.record("boost", "1.86", "windows", arch="x86_64", bytes_sent=5)
            top = await store.get_top_packages()
            assert top[0]["name"] == "zlib" and top[0]["count"] == 3
            assert top[0]["bytes_sent"] == 30
            dist = await store.get_platform_distribution()
            by = {(d["platform"], d["arch"]): d["count"] for d in dist}
            assert by[("linux", "x86_64")] == 3
            assert by[("windows", "x86_64")] == 1
            versions = {v["version"]: v["count"] for v in await store.get_client_versions()}
            assert versions["2.2.2"] == 3
            assert versions[""] == 1  # boost had no client version

        run(_t())

    def test_daily_and_bandwidth_zero_filled(self):
        from cvcpkg.server.db_stores import DbDownloadStore

        async def _t():
            store = DbDownloadStore()
            await store.record("zlib", "1.0", "linux", bytes_sent=100)
            await store.record("zlib", "1.0", "linux", bytes_sent=200)
            daily = await store.get_daily_downloads(days=7)
            assert len(daily) == 7  # zero-filled window
            assert sum(d["count"] for d in daily) == 2
            today = datetime.date.today().isoformat()
            assert any(d["date"] == today and d["count"] == 2 for d in daily)
            bw = await store.get_bandwidth(days=7)
            assert len(bw["daily"]) == 7
            assert bw["total_bytes"] == 300
            # Filter by package name.
            bw_zlib = await store.get_bandwidth(package_name="zlib", days=7)
            assert bw_zlib["total_bytes"] == 300
            daily_zlib = await store.get_daily_downloads(package_name="zlib", days=7)
            assert sum(d["count"] for d in daily_zlib) == 2

        run(_t())


# ── DbTelemetryStore ────────────────────────────────────────────


class TestDbTelemetryStore:
    def test_record_and_summary(self):
        from cvcpkg.server.db_stores import DbTelemetryStore

        async def _t():
            store = DbTelemetryStore()
            await store.record(
                platform="linux",
                arch="x86_64",
                python_version="3.12",
                cvcpkg_version="2.2.2",
                ci=True,
                tools={"cmake": "3.30"},
            )
            await store.record(
                platform="linux",
                arch="x86_64",
                python_version="3.11",
                cvcpkg_version="2.2.2",
                ci=False,
            )
            summary = await store.get_summary()
            assert summary["total"] == 2
            plats = {(p["platform"], p["arch"]): p["count"] for p in summary["platforms"]}
            assert plats[("linux", "x86_64")] == 2
            pys = {p["version"]: p["count"] for p in summary["python_versions"]}
            assert pys["3.12"] == 1 and pys["3.11"] == 1
            vers = {v["version"]: v["count"] for v in summary["cvcpkg_versions"]}
            assert vers["2.2.2"] == 2
            ci = {c["ci"]: c["count"] for c in summary["ci"]}
            assert ci[True] == 1 and ci[False] == 1

        run(_t())


async def _principal_id(name):
    """Look up a principal id by name (helper for the exempt-mint test)."""
    from sqlalchemy import select

    from cvcpkg.server.db import PrincipalRow, get_session

    async with get_session() as session:
        return (
            await session.execute(select(PrincipalRow.id).where(PrincipalRow.name == name))
        ).scalar()
