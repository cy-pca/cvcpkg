"""Authorization boundaries on the builder endpoints.

The builder endpoints authenticated a *role* (publisher or admin) and then
trusted whatever ``org_slug`` or ``builder_id`` the caller named.  Four
distinct holes came out of that, and they compose:

1. ``POST /v1/builders/register`` attached a builder to any org, with no
   membership check -- the gate ``POST /v1/builds`` already applied.
2. Re-registration is an upsert on ``(name, org_slug)`` that reassigns
   ``registered_by``, so any publisher could take over another builder's row.
   This one matters most: it is what would make an ownership check on the
   endpoints below worthless.
3. ``PATCH /v1/builders/{id}`` let any publisher rewrite any builder's
   ``served_namespaces``, which ``_choose_builder`` trusts for namespace
   isolation.
4. ``heartbeat``/``next-job``/``ws`` authenticated any publisher token against
   any ``builder_id``, so a caller could take a job dispatched to someone
   else's builder, or displace the real builder's WebSocket.

The rule now: attaching to an org needs membership; *being* a builder
(heartbeat, next-job, ws) is owner-or-admin; *administering* one additionally
allows a member of the owning org.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for builder tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


@pytest.fixture()
def authz_server(tmp_path, monkeypatch):
    """Two orgs, and a publisher who belongs to exactly one of them.

    ``alice`` is a member of org ``alpha``; ``mallory`` is a publisher with no
    org membership at all.  Both hold the same *role*, which is the point.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'authz.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbOrgStore, DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        tokens = DbTokenStore(tmp_path)
        admin = await tokens.create("admin", TokenRole.admin)
        alice = await tokens.create("alice", TokenRole.publisher)
        mallory = await tokens.create("mallory", TokenRole.publisher)
        orgs = DbOrgStore()
        await orgs.create(slug="alpha", display_name="Alpha", created_by="admin")
        await orgs.create(slug="beta", display_name="Beta", created_by="admin")
        await orgs.add_member("alpha", "alice")
        await dispose_engine()
        return admin, alice, mallory

    admin_t, alice_t, mallory_t = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, {"admin": admin_t, "alice": alice_t, "mallory": mallory_t}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, token, name, **kw):
    body = {"name": name, "platform": "linux", "arch": "x86_64"}
    body.update(kw)
    return client.post("/v1/builders/register", headers=_auth(token), json=body)


# ── 1. Org attachment ───────────────────────────────────────────


class TestRegisterOrgScoping:
    def test_member_may_register_for_their_org(self, authz_server):
        client, t = authz_server
        r = _register(client, t["alice"], "alpha-builder", org_slug="alpha")
        assert r.status_code == 200, r.text
        assert r.json()["org_slug"] == "alpha"

    def test_non_member_may_not_register_for_an_org(self, authz_server):
        """The hole: mallory is a publisher, but belongs to no org."""
        client, t = authz_server
        r = _register(client, t["mallory"], "beta-builder", org_slug="beta")
        assert r.status_code == 403, r.text
        assert "not a member" in r.json()["detail"]

    def test_member_of_one_org_may_not_register_for_another(self, authz_server):
        client, t = authz_server
        r = _register(client, t["alice"], "beta-builder", org_slug="beta")
        assert r.status_code == 403, r.text

    def test_admin_may_register_for_any_org(self, authz_server):
        client, t = authz_server
        r = _register(client, t["admin"], "admin-builder", org_slug="beta")
        assert r.status_code == 200, r.text

    def test_orgless_registration_is_unaffected(self, authz_server):
        """The entire live fleet has no org_slug; it must keep working."""
        client, t = authz_server
        assert _register(client, t["mallory"], "standalone").status_code == 200


# ── 2. Re-registration takeover ─────────────────────────────────


class TestReRegistrationOwnership:
    def test_owner_may_re_register(self, authz_server):
        """Builders re-register on every restart — this must stay cheap."""
        client, t = authz_server
        assert _register(client, t["alice"], "mine").status_code == 200
        again = _register(client, t["alice"], "mine", max_jobs=4)
        assert again.status_code == 200, again.text
        assert again.json()["max_jobs"] == 4

    def test_stranger_may_not_take_over_an_existing_builder(self, authz_server):
        """Without this, every ownership check below is worthless."""
        client, t = authz_server
        assert _register(client, t["alice"], "mine").status_code == 200
        stolen = _register(client, t["mallory"], "mine")
        assert stolen.status_code == 409, stolen.text
        assert "already registered by" in stolen.json()["detail"]

    def test_takeover_does_not_change_the_owner(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        _register(client, t["mallory"], "mine")
        info = client.get(f"/v1/builders/{bid}", headers=_auth(t["admin"])).json()
        assert info["registered_by"] == "alice"

    def test_same_name_in_a_different_org_is_a_different_builder(self, authz_server):
        """register() upserts on (name, org_slug), so this must not collide."""
        client, t = authz_server
        a = _register(client, t["alice"], "shared", org_slug="alpha")
        b = _register(client, t["admin"], "shared", org_slug="beta")
        assert a.status_code == 200 and b.status_code == 200
        assert a.json()["id"] != b.json()["id"]

    def test_admin_may_take_over(self, authz_server):
        client, t = authz_server
        _register(client, t["alice"], "mine")
        assert _register(client, t["admin"], "mine").status_code == 200


# ── 3. PATCH / served_namespaces ────────────────────────────────


class TestUpdateOwnership:
    def test_owner_may_patch(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        r = client.patch(f"/v1/builders/{bid}", headers=_auth(t["alice"]), json={"max_jobs": 3})
        assert r.status_code == 200, r.text

    def test_stranger_may_not_rewrite_served_namespaces(self, authz_server):
        """_choose_builder trusts served_namespaces for namespace isolation."""
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        r = client.patch(
            f"/v1/builders/{bid}",
            headers=_auth(t["mallory"]),
            json={"served_namespaces": ["alpha", "beta", ""]},
        )
        assert r.status_code == 403, r.text

    def test_served_namespaces_unchanged_after_a_refused_patch(self, authz_server):
        client, t = authz_server
        reg = _register(client, t["alice"], "mine", served_namespaces=["x"]).json()
        before = reg["served_namespaces"]
        client.patch(
            f"/v1/builders/{reg['id']}",
            headers=_auth(t["mallory"]),
            json={"served_namespaces": ["alpha", "beta", ""]},
        )
        after = client.get(f"/v1/builders/{reg['id']}", headers=_auth(t["admin"])).json()[
            "served_namespaces"
        ]
        assert after == before

    def test_org_co_member_may_administer_an_org_builder(self, authz_server):
        """Deliberately broader than the identity gate: same-org retuning is ok."""
        client, t = authz_server
        bid = _register(client, t["admin"], "org-builder", org_slug="alpha").json()["id"]
        r = client.patch(f"/v1/builders/{bid}", headers=_auth(t["alice"]), json={"max_jobs": 2})
        assert r.status_code == 200, r.text

    def test_outsider_may_not_administer_an_org_builder(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["admin"], "org-builder", org_slug="alpha").json()["id"]
        r = client.patch(f"/v1/builders/{bid}", headers=_auth(t["mallory"]), json={"max_jobs": 2})
        assert r.status_code == 403, r.text

    def test_patch_of_a_missing_builder_is_404_not_403(self, authz_server):
        client, t = authz_server
        r = client.patch("/v1/builders/99999", headers=_auth(t["alice"]), json={"max_jobs": 2})
        assert r.status_code == 404


# ── 4. Assuming a builder's identity ────────────────────────────


class TestIdentityEndpoints:
    def test_owner_may_heartbeat(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        r = client.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(t["alice"]),
            json={"status": "online", "current_jobs": 0},
        )
        assert r.status_code == 200, r.text

    def test_stranger_may_not_heartbeat(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        r = client.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(t["mallory"]),
            json={"status": "online", "current_jobs": 0},
        )
        assert r.status_code == 403, r.text

    def test_stranger_may_not_claim_next_job(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        r = client.get(
            f"/v1/builders/{bid}/next-job",
            headers=_auth(t["mallory"]),
            params={"timeout": 1},
        )
        assert r.status_code == 403, r.text

    def test_org_co_member_may_not_claim_next_job(self, authz_server):
        """Stricter than PATCH on purpose: taking another builder's job is never ok."""
        client, t = authz_server
        bid = _register(client, t["admin"], "org-builder", org_slug="alpha").json()["id"]
        r = client.get(
            f"/v1/builders/{bid}/next-job",
            headers=_auth(t["alice"]),
            params={"timeout": 1},
        )
        assert r.status_code == 403, r.text

    def test_org_co_member_may_not_heartbeat_someone_elses_builder(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["admin"], "org-builder", org_slug="alpha").json()["id"]
        r = client.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(t["alice"]),
            json={"status": "online", "current_jobs": 0},
        )
        assert r.status_code == 403, r.text


class TestWebSocketOwnership:
    def test_stranger_cannot_displace_the_real_builders_socket(self, authz_server):
        """The WS handler assigns _ws_builders[builder_id] unconditionally."""
        from starlette.websockets import WebSocketDisconnect

        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/v1/builders/{bid}/ws?token={t['mallory']}") as ws:
                ws.receive_json()
        assert exc.value.code == 4003

    def test_owner_can_connect(self, authz_server):
        client, t = authz_server
        bid = _register(client, t["alice"], "mine").json()["id"]
        with client.websocket_connect(f"/v1/builders/{bid}/ws?token={t['alice']}") as ws:
            assert ws is not None
