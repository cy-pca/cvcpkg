# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Browser (session + CSRF) org member management at /org/{slug}/manage.

An org owner can manage members from the browser with no bearer token: the
cookie session carries their principal, and the forms are CSRF-protected the
same way /account is.  These tests drive the real routes end to end through a
token->session login (the break-glass path), which is enough to exercise the
session/ownership/CSRF plumbing without a live identity provider.
"""

from __future__ import annotations

import asyncio
import re

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

_CSRF_RE = re.compile(r'name="_csrf" value="([0-9a-f]+)"')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'manage.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    # TestClient speaks http; without this the session cookie is minted Secure
    # and silently discarded, so every authenticated page falls back to /login.
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.identities import DbPrincipalStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        tokens = DbTokenStore(tmp_path)
        principals = DbPrincipalStore()
        admin = await tokens.create("test-admin", TokenRole.admin)
        await tokens.create("ci-token", TokenRole.publisher)
        await principals.upsert_from_claims(
            "https://idp.test",
            "sub-alice",
            {"preferred_username": "alice", "sub": "sub-alice"},
            "publisher",
        )
        await dispose_engine()
        return admin

    admin_tok = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_tok


def _browser_login(client, token):
    resp = client.post("/login", data={"token": token, "next": "/account"})
    assert resp.status_code in (200, 303), resp.text
    # The session cookie is now in the client's jar.
    assert any("session" in c.lower() for c in client.cookies), client.cookies


def _manage_page(client, slug):
    resp = client.get(f"/org/{slug}/manage")
    return resp


class TestBrowserManage:
    def test_owner_can_open_manage_and_add_and_remove(self, env):
        client, admin = env
        # Admin creates the org (via API) → becomes owner as principal "test-admin".
        assert (
            client.post(
                "/v1/orgs",
                json={"slug": "acme", "display_name": "Acme"},
                headers={"Authorization": f"Bearer {admin}"},
            ).status_code
            == 200
        )

        _browser_login(client, admin)

        page = _manage_page(client, "acme")
        assert page.status_code == 200, page.text
        assert "Add a member" in page.text
        assert "alice" in page.text  # principal appears in the datalist

        tokens = _CSRF_RE.findall(page.text)
        assert tokens, "no CSRF token rendered"
        csrf_add = tokens[0]

        # Add alice as owner via the browser form.
        resp = client.post(
            "/org/acme/members",
            data={
                "_csrf": csrf_add,
                "token_name": "alice",
                "principal_kind": "user",
                "role": "owner",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200, resp.text
        assert "alice" in resp.text

        # alice is now a member with kind=user via the API view.
        detail = client.get("/v1/orgs/acme", headers={"Authorization": f"Bearer {admin}"}).json()
        members = {m["token_name"]: m for m in detail["members"]}
        assert members["alice"]["role"] == "owner"
        assert members["alice"]["kind"] == "user"

        # Remove alice via the browser form (grab a remove CSRF token).
        page2 = _manage_page(client, "acme")
        toks = _CSRF_RE.findall(page2.text)
        csrf_remove = toks[-1]
        resp = client.post(
            "/org/acme/members/remove",
            data={"_csrf": csrf_remove, "token_name": "alice"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        detail = client.get("/v1/orgs/acme", headers={"Authorization": f"Bearer {admin}"}).json()
        assert "alice" not in {m["token_name"] for m in detail["members"]}

    def test_add_without_csrf_is_refused(self, env):
        client, admin = env
        client.post(
            "/v1/orgs",
            json={"slug": "acme", "display_name": "Acme"},
            headers={"Authorization": f"Bearer {admin}"},
        )
        _browser_login(client, admin)
        resp = client.post(
            "/org/acme/members",
            data={"token_name": "ci-token", "principal_kind": "token", "role": "member"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_anonymous_manage_redirects_to_login(self, env):
        client, admin = env
        client.post(
            "/v1/orgs",
            json={"slug": "acme", "display_name": "Acme"},
            headers={"Authorization": f"Bearer {admin}"},
        )
        resp = client.get("/org/acme/manage", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")
