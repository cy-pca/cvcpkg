"""Org storage-limit gating + private-org list visibility.

Storage figures (limit/usage) are member/super-admin-only; the org list hides
private orgs from non-members but shows a member their own private orgs.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture()
def org_server(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'orgs.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_POPULATE_UPSTREAM", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("admin", TokenRole.admin)
        owner = await store.create("owner", TokenRole.publisher)
        reader = await store.create("outsider", TokenRole.reader)
        await dispose_engine()
        return admin, owner, reader

    admin, owner, reader = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        # 'owner' creates a public org + a private org (becomes owner/member of both).
        for slug, priv in (("acme", False), ("shell", True)):
            r = client.post(
                "/v1/orgs",
                json={"slug": slug, "display_name": slug.title(), "is_private": priv},
                headers=_hdr(owner),
            )
            assert r.status_code in (200, 201), r.text
        yield client, admin, owner, reader


class TestStorageGating:
    def test_get_org_storage_hidden_from_nonmember(self, org_server):
        client, admin, owner, reader = org_server
        # public org: visible to everyone, but storage only to members/admins.
        anon = client.get("/v1/orgs/acme").json()["org"]
        assert anon["storage_limit_bytes"] is None and anon["storage_used_bytes"] is None
        out = client.get("/v1/orgs/acme", headers=_hdr(reader)).json()["org"]
        assert out["storage_limit_bytes"] is None
        mem = client.get("/v1/orgs/acme", headers=_hdr(owner)).json()["org"]
        assert mem["storage_limit_bytes"] is not None
        adm = client.get("/v1/orgs/acme", headers=_hdr(admin)).json()["org"]
        assert adm["storage_limit_bytes"] is not None

    def test_private_org_404_to_nonmember(self, org_server):
        client, admin, owner, reader = org_server
        assert client.get("/v1/orgs/shell").status_code == 404  # anonymous
        assert client.get("/v1/orgs/shell", headers=_hdr(reader)).status_code == 404
        assert client.get("/v1/orgs/shell", headers=_hdr(owner)).status_code == 200
        assert client.get("/v1/orgs/shell", headers=_hdr(admin)).status_code == 200


class TestListVisibility:
    def _slugs(self, resp):
        return {o["slug"] for o in resp["organizations"]}

    def test_anonymous_sees_only_public_without_storage(self, org_server):
        client, *_ = org_server
        data = client.get("/v1/orgs").json()
        assert self._slugs(data) == {"acme"}  # private 'shell' hidden
        acme = next(o for o in data["organizations"] if o["slug"] == "acme")
        assert acme["storage_limit_bytes"] is None

    def test_member_sees_their_private_org_with_storage(self, org_server):
        client, _, owner, _ = org_server
        data = client.get("/v1/orgs", headers=_hdr(owner)).json()
        assert self._slugs(data) == {"acme", "shell"}
        for o in data["organizations"]:
            assert o["storage_limit_bytes"] is not None  # member of both

    def test_admin_sees_all_with_storage(self, org_server):
        client, admin, *_ = org_server
        data = client.get("/v1/orgs", headers=_hdr(admin)).json()
        assert self._slugs(data) == {"acme", "shell"}
        assert all(o["storage_limit_bytes"] is not None for o in data["organizations"])

    def test_nonmember_reader_no_storage(self, org_server):
        client, _, _, reader = org_server
        data = client.get("/v1/orgs", headers=_hdr(reader)).json()
        assert self._slugs(data) == {"acme"}
        acme = next(o for o in data["organizations"] if o["slug"] == "acme")
        assert acme["storage_limit_bytes"] is None
