"""Integration tests for public builder read access.

Verifies that:
  - Builders with no org (org_slug="") are visible to unauthenticated clients
  - Builders in public orgs are visible to unauthenticated clients
  - Builders in private orgs are hidden from unauthenticated clients
  - Builders in private orgs are visible to org members
  - Admins can see all builders regardless of org privacy
  - Individual builder info follows the same access rules
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """DB-backed test server with admin, publisher, and reader tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("admin", TokenRole.admin)
        publisher = await store.create("publisher", TokenRole.publisher)
        reader = await store.create("reader", TokenRole.reader)
        member = await store.create("org-member", TokenRole.publisher)
        outsider = await store.create("outsider", TokenRole.publisher)
        await dispose_engine()
        return admin, publisher, reader, member, outsider

    admin_tok, pub_tok, reader_tok, member_tok, outsider_tok = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield {
            "client": client,
            "admin_token": admin_tok,
            "pub_token": pub_tok,
            "reader_token": reader_tok,
            "member_token": member_tok,
            "outsider_token": outsider_tok,
        }


class TestBuilderPublicAccess:
    """Test public/private builder visibility."""

    def _create_org(self, c, token, slug, *, is_private=False):
        resp = c.post(
            "/v1/orgs",
            json={
                "slug": slug,
                "display_name": slug.replace("-", " ").title(),
                "is_private": is_private,
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _add_member(self, c, token, slug, member_name, role="member"):
        resp = c.post(
            f"/v1/orgs/{slug}/members",
            params={"token_name": member_name, "role": role},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text

    def _register_builder(self, c, token, name, *, org="", platform="linux", arch="x86_64"):
        resp = c.post(
            "/v1/builders/register",
            json={
                "name": name,
                "platform": platform,
                "arch": arch,
                "max_jobs": 2,
                "org_slug": org,
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _setup_builders(self, server):
        """Create orgs and builders for testing."""
        c = server["client"]
        admin_tok = server["admin_token"]
        pub_tok = server["pub_token"]
        member_tok = server["member_token"]

        # Create a public org and a private org
        self._create_org(c, admin_tok, "public-org", is_private=False)
        self._create_org(c, admin_tok, "private-org", is_private=True)

        # Add org-member to the private org
        self._add_member(c, admin_tok, "private-org", "org-member")

        # Register builders:
        # 1. Global builder (no org) — should be public
        b1 = self._register_builder(c, pub_tok, "global-builder")
        # 2. Public org builder — should be public
        b2 = self._register_builder(c, pub_tok, "public-org-builder", org="public-org")
        # 3. Private org builder — should be hidden from non-members
        b3 = self._register_builder(c, pub_tok, "private-org-builder", org="private-org")

        return b1, b2, b3

    def test_unauthenticated_sees_public_builders(self, server):
        """Unauthenticated client can see global and public-org builders."""
        b1, b2, b3 = self._setup_builders(server)
        c = server["client"]

        # No auth header
        resp = c.get("/v1/builders")
        assert resp.status_code == 200
        names = {b["name"] for b in resp.json()["builders"]}
        assert "global-builder" in names
        assert "public-org-builder" in names
        assert "private-org-builder" not in names

    def test_unauthenticated_gets_public_builder_info(self, server):
        """Unauthenticated client can get info for a public builder."""
        b1, b2, b3 = self._setup_builders(server)
        c = server["client"]

        # Global builder
        resp = c.get(f"/v1/builders/{b1['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "global-builder"

        # Public org builder
        resp = c.get(f"/v1/builders/{b2['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "public-org-builder"

    def test_unauthenticated_cannot_see_private_builder(self, server):
        """Unauthenticated client gets 404 for private-org builder."""
        _, _, b3 = self._setup_builders(server)
        c = server["client"]

        resp = c.get(f"/v1/builders/{b3['id']}")
        assert resp.status_code == 404

    def test_outsider_cannot_see_private_builder(self, server):
        """Authenticated non-member cannot see private-org builder."""
        _, _, b3 = self._setup_builders(server)
        c = server["client"]
        outsider_tok = server["outsider_token"]

        # List should hide private builder
        resp = c.get("/v1/builders", headers=_auth(outsider_tok))
        assert resp.status_code == 200
        names = {b["name"] for b in resp.json()["builders"]}
        assert "private-org-builder" not in names

        # Direct access should 404
        resp = c.get(f"/v1/builders/{b3['id']}", headers=_auth(outsider_tok))
        assert resp.status_code == 404

    def test_member_sees_private_builder(self, server):
        """Org member can see private-org builder."""
        _, _, b3 = self._setup_builders(server)
        c = server["client"]
        member_tok = server["member_token"]

        # List should include private builder
        resp = c.get("/v1/builders", headers=_auth(member_tok))
        assert resp.status_code == 200
        names = {b["name"] for b in resp.json()["builders"]}
        assert "private-org-builder" in names

        # Direct access should work
        resp = c.get(f"/v1/builders/{b3['id']}", headers=_auth(member_tok))
        assert resp.status_code == 200
        assert resp.json()["name"] == "private-org-builder"

    def test_admin_sees_all_builders(self, server):
        """Admin can see all builders regardless of org privacy."""
        self._setup_builders(server)
        c = server["client"]
        admin_tok = server["admin_token"]

        resp = c.get("/v1/builders", headers=_auth(admin_tok))
        assert resp.status_code == 200
        names = {b["name"] for b in resp.json()["builders"]}
        assert "global-builder" in names
        assert "public-org-builder" in names
        assert "private-org-builder" in names

    def test_reader_sees_public_builders(self, server):
        """Reader token can see public builders."""
        self._setup_builders(server)
        c = server["client"]
        reader_tok = server["reader_token"]

        resp = c.get("/v1/builders", headers=_auth(reader_tok))
        assert resp.status_code == 200
        names = {b["name"] for b in resp.json()["builders"]}
        assert "global-builder" in names
        assert "public-org-builder" in names
        assert "private-org-builder" not in names
