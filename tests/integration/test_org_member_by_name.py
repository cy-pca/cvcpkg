# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Adding org members by SSO username or machine-token name (must-exist-first).

The org member surface was the one place not wired to principals: it wrote a
bare string with no existence check, so a mistyped name became an orphan grant
on a claimable handle.  These tests pin the corrected contract:

* a **person** is added by their principal name (``--user``) and must have
  signed in once;
* a **machine token** is added by its own name (``--token``) and must be live;
* a delegated token's row name (``alice.laptop``) is NOT addable — it presents
  as its principal, so the grant would match nothing;
* an org owner may manage members even when their *global* role is only reader
  (org ownership is the authority);
* every member is annotated with its kind (user / token / orphan) for display.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A DB-backed app with seeded tokens and principals.

    Returns the client plus the raw secrets and names the tests need.
    """
    db_path = tmp_path / "members.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.identities import DbPrincipalStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        tokens = DbTokenStore(tmp_path)
        principals = DbPrincipalStore()
        admin = await tokens.create("test-admin", TokenRole.admin)
        # A bare machine token (principal_id IS NULL).
        await tokens.create("ci-token", TokenRole.publisher)
        # A reader token we will make an org OWNER, to prove ownership — not a
        # global publisher/admin role — is what authorizes member management.
        reader_owner = await tokens.create("reader-owner", TokenRole.reader)
        # A person who has signed in (a principal).
        alice, _ = await principals.upsert_from_claims(
            "https://idp.test",
            "sub-alice",
            {"preferred_username": "alice", "sub": "sub-alice"},
            "publisher",
        )
        # A delegated token owned by alice — its row name must NOT be addable.
        await tokens.create("alice.laptop", TokenRole.publisher, principal_id=alice.id)
        # A disabled principal.
        banned, _ = await principals.upsert_from_claims(
            "https://idp.test",
            "sub-banned",
            {"preferred_username": "banned", "sub": "sub-banned"},
            "reader",
        )
        await principals.set_disabled("banned", True)
        await dispose_engine()
        return admin, reader_owner

    admin_tok, reader_owner_tok = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_tok, reader_owner_tok


def _create_org(client, token, slug):
    resp = client.post(
        "/v1/orgs",
        json={"slug": slug, "display_name": slug.title()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def _add(client, token, slug, name, *, kind="auto", role="member"):
    return client.post(
        f"/v1/orgs/{slug}/members",
        params={"token_name": name, "role": role, "principal_kind": kind},
        headers={"Authorization": f"Bearer {token}"},
    )


def _members(client, token, slug):
    resp = client.get(f"/v1/orgs/{slug}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return {m["token_name"]: m for m in resp.json()["members"]}


def _remove(client, token, slug, name):
    return client.delete(
        f"/v1/orgs/{slug}/members/{name}", headers={"Authorization": f"Bearer {token}"}
    )


class TestLastOwnerGuard:
    def test_cannot_remove_the_last_owner(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")  # admin ("test-admin") is auto-owner
        # Removing the only owner is refused (409), keeping the org manageable.
        assert _remove(client, admin, "acme", "test-admin").status_code == 409
        # With a second owner, removing one is fine; the last is still guarded.
        assert _add(client, admin, "acme", "reader-owner", role="owner").status_code == 200
        assert _remove(client, admin, "acme", "test-admin").status_code == 200
        assert _remove(client, admin, "acme", "reader-owner").status_code == 409


class TestAddByName:
    def test_add_bare_token_auto(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "ci-token").status_code == 200
        assert _members(client, admin, "acme")["ci-token"]["kind"] == "token"

    def test_add_user_principal_as_owner(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "alice", kind="user", role="owner").status_code == 200
        m = _members(client, admin, "acme")["alice"]
        assert m["kind"] == "user"
        assert m["role"] == "owner"

    def test_unknown_name_auto_is_404(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "ghost").status_code == 404

    def test_unknown_user_is_404(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "ghost", kind="user").status_code == 404

    def test_disabled_principal_is_409(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "banned", kind="user").status_code == 409

    def test_delegated_row_name_not_addable_as_token(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        # alice.laptop is a delegated token; it presents as `alice`, not itself.
        assert _add(client, admin, "acme", "alice.laptop", kind="token").status_code == 404
        assert _add(client, admin, "acme", "alice.laptop").status_code == 404

    def test_already_a_member_is_409(self, env):
        client, admin, _ = env
        _create_org(client, admin, "acme")
        assert _add(client, admin, "acme", "ci-token").status_code == 200
        assert _add(client, admin, "acme", "ci-token").status_code == 409


class TestOwnershipAuthorizes:
    def test_owner_who_is_only_a_reader_can_manage(self, env):
        client, admin, reader_owner = env
        _create_org(client, admin, "acme")
        # Make the reader token an OWNER of the org.
        assert _add(client, admin, "acme", "reader-owner", role="owner").status_code == 200
        # Now that reader-owner token — global role 'reader' — adds a member.
        assert _add(client, reader_owner, "acme", "ci-token").status_code == 200

    def test_non_owner_is_forbidden(self, env):
        client, admin, reader_owner = env
        _create_org(client, admin, "acme")
        # reader-owner is not (yet) a member of acme → cannot manage it.
        resp = _add(client, reader_owner, "acme", "ci-token")
        assert resp.status_code == 403
