"""The credential seam: a delegated token authorizes exactly as its principal.

This file exists because the seam shipped with no executing test. The one test
that claimed to cover it probed a `/v1/whoami` route that does not exist and so
skipped unconditionally — which is how two defects reached a review with a
green suite: `list_tokens()` silently dropped `principal_id`, and a disabled
principal could complete a login.

So these assert at the STORE layer, where the substitution actually happens,
against the predicates that actually authorize:

    a token row named "joe.laptop"  ==  a bare token named "joe"

for `is_member`, `is_owner` and `member_org_slugs`. If that equivalence ever
breaks, an SSO user silently loses access to their private orgs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from cvcpkg.server.models import TokenRole


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """A token store, principal store and org store over one fresh database."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'seam.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, init_db
    from cvcpkg.server.db_stores import DbOrgStore, DbTokenStore
    from cvcpkg.server.identities import DbPrincipalStore

    async def _mk():
        init_db(db_url)
        await create_tables()

    asyncio.run(_mk())
    yield {
        "tokens": DbTokenStore(Path(tmp_path)),
        "principals": DbPrincipalStore(),
        "orgs": DbOrgStore(),
        "dir": Path(tmp_path),
    }


def _run(coro):
    return asyncio.run(coro)


class TestDelegatedTokenIsItsPrincipal:
    def test_verify_substitutes_the_principal_name(self, stores):
        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            raw = await stores["tokens"].create(
                "joe.laptop", TokenRole.publisher, principal_id=p.id
            )
            rec = await stores["tokens"].verify(raw)
            assert rec is not None
            # The whole point: the row is joe.laptop, the identity is joe.
            assert rec.name == "joe"
            assert rec.credential_name == "joe.laptop"
            assert rec.credential_kind == "delegated"
            assert rec.principal_id == p.id

        _run(body())

    def test_org_predicates_cannot_tell_it_from_a_bare_token(self, stores):
        """If this ever diverges, SSO users lose their private orgs."""

        async def body():
            orgs = stores["orgs"]
            await orgs.create(slug="cvc", display_name="CVC", created_by="admin")
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            # The org owner adds the HANDLE, exactly as they would type it.
            await orgs.add_member("cvc", "joe")

            raw = await stores["tokens"].create(
                "joe.laptop", TokenRole.publisher, principal_id=p.id
            )
            rec = await stores["tokens"].verify(raw)

            assert await orgs.is_member("cvc", rec.name) is True
            assert "cvc" in await orgs.member_org_slugs(rec.name)
            # And the literal row name grants nothing on its own.
            assert await orgs.is_member("cvc", rec.credential_name) is False

        _run(body())

    def test_a_plain_machine_token_is_untouched(self, stores):
        async def body():
            raw = await stores["tokens"].create("builders", TokenRole.publisher)
            rec = await stores["tokens"].verify(raw)
            assert rec.name == "builders"
            assert rec.credential_kind == "token"
            assert rec.principal_id is None

        _run(body())


class TestOffboarding:
    def test_disabling_the_principal_invalidates_its_tokens_at_once(self, stores):
        """A property a hand-minted cvctok_ has never had."""

        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            raw = await stores["tokens"].create(
                "joe.laptop", TokenRole.publisher, principal_id=p.id
            )
            assert await stores["tokens"].verify(raw) is not None

            await stores["principals"].set_disabled("joe", True)
            assert await stores["tokens"].verify(raw) is None

            await stores["principals"].set_disabled("joe", False)
            assert await stores["tokens"].verify(raw) is not None

        _run(body())


class TestDemotionClamp:
    def test_a_delegated_token_cannot_outrank_its_principal(self, stores):
        async def body():
            from sqlalchemy import select

            from cvcpkg.server.db import PrincipalRow, atomic_session

            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            raw = await stores["tokens"].create(
                "joe.laptop", TokenRole.publisher, principal_id=p.id
            )
            assert (await stores["tokens"].verify(raw)).role == TokenRole.publisher

            async with atomic_session() as s:
                row = (
                    (await s.execute(select(PrincipalRow).where(PrincipalRow.id == p.id)))
                    .scalars()
                    .first()
                )
                row.last_role = "reader"

            assert (await stores["tokens"].verify(raw)).role == TokenRole.reader

        _run(body())

    def test_the_clamp_only_lowers(self, stores):
        """A reader token does not become publisher because the principal is one."""

        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            raw = await stores["tokens"].create("joe.ci", TokenRole.reader, principal_id=p.id)
            assert (await stores["tokens"].verify(raw)).role == TokenRole.reader

        _run(body())


class TestListTokensCarriesOwnership:
    """The exact field whose omission emptied every ownership filter."""

    def test_list_tokens_reports_principal_id(self, stores):
        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            await stores["tokens"].create("joe.laptop", TokenRole.publisher, principal_id=p.id)
            await stores["tokens"].create("builders", TokenRole.publisher)

            rows = {t.name: t for t in await stores["tokens"].list_tokens()}
            assert rows["joe.laptop"].principal_id == p.id
            assert rows["builders"].principal_id is None

        _run(body())

    def test_tokens_for_principal_filters_in_sql(self, stores):
        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            q, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s2", {"preferred_username": "kim", "sub": "s2"}, "publisher"
            )
            await stores["tokens"].create("joe.laptop", TokenRole.publisher, principal_id=p.id)
            await stores["tokens"].create("kim.laptop", TokenRole.publisher, principal_id=q.id)
            await stores["tokens"].create("builders", TokenRole.publisher)

            mine = await stores["tokens"].tokens_for_principal(p.id)
            assert [t.name for t in mine] == ["joe.laptop"]

        _run(body())

    def test_a_revoked_token_is_not_listed_as_mine(self, stores):
        async def body():
            p, _ = await stores["principals"].upsert_from_claims(
                "https://idp.test", "s1", {"preferred_username": "joe", "sub": "s1"}, "publisher"
            )
            await stores["tokens"].create("joe.laptop", TokenRole.publisher, principal_id=p.id)
            await stores["tokens"].revoke("joe.laptop")
            assert await stores["tokens"].tokens_for_principal(p.id) == []

        _run(body())
