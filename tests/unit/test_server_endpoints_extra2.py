"""Second wave of endpoint coverage for ``cvcpkg.server.app``.

Companion to ``test_server_endpoints_extra.py``.  This file sweeps the route
families that only exist to serve a database-backed deployment — recipe
distribution, webhooks, the build/builder fleet, admin GC/purge, CLI-login
(``/link``) and browser-account (``/account``) surfaces, OIDC entry points, and
the admin principal console — asserting that on the in-memory backend each one
takes its documented "requires a database backend" (501) / "not configured"
(404) branch, and that role/authentication guards (401/403) fire ahead of it.

Where a route genuinely works without a database (the recipe-set bundle read,
runtime settings, logout) the happy path is asserted instead.
"""

from __future__ import annotations

import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pydantic = pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole


@pytest.fixture()
def env(tmp_path):
    store = TokenStore(tmp_path)
    admin = store.create("admin-user", TokenRole.admin)
    publisher = store.create("pub-user", TokenRole.publisher)
    reader = store.create("read-user", TokenRole.reader)
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield {"client": client, "admin": admin, "publisher": publisher, "reader": reader}


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ── Recipe distribution ─────────────────────────────────────────


class TestRecipeDistribution:
    def test_recipe_set_bundle_local(self, env):
        # The full-set bundle falls back to the vendored recipes with no DB.
        r = env["client"].get("/v1/recipes/bundle")
        assert r.status_code == 200
        assert len(r.content) > 0

    def test_recipe_store_endpoints_need_db(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        assert c.get("/v1/recipes", headers=_auth(pub)).status_code == 501
        assert c.get("/v1/recipes/zlib", headers=_auth(pub)).status_code == 501
        assert c.delete("/v1/recipes/zlib", headers=_auth(admin)).status_code == 501
        assert c.post("/v1/recipes/zlib/register", headers=_auth(admin)).status_code == 501
        # Recipe push needs a file body; the DB gate still fires (501).
        push = c.post(
            "/v1/recipes/zlib",
            files={"file": ("zlib.tar.gz", io.BytesIO(b"x"))},
            headers=_auth(pub),
        )
        assert push.status_code == 501

    def test_recipe_store_auth(self, env):
        c = env["client"]
        assert c.get("/v1/recipes").status_code == 401
        assert c.get("/v1/recipes", headers=_auth(env["reader"])).status_code == 403
        assert c.delete("/v1/recipes/zlib", headers=_auth(env["publisher"])).status_code == 403


# ── Webhooks ────────────────────────────────────────────────────


class TestWebhooks:
    def test_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert (
            c.post(
                "/v1/webhooks",
                json={"url": "https://h.example.com", "events": ["*"]},
                headers=_auth(admin),
            ).status_code
            == 501
        )
        assert c.get("/v1/webhooks", headers=_auth(admin)).status_code == 501
        assert c.get("/v1/webhooks/1", headers=_auth(admin)).status_code == 501
        assert (
            c.patch("/v1/webhooks/1", json={"active": False}, headers=_auth(admin)).status_code
            == 501
        )
        assert c.delete("/v1/webhooks/1", headers=_auth(admin)).status_code == 501
        assert c.post("/v1/webhooks/1/test", headers=_auth(admin)).status_code == 501

    def test_auth(self, env):
        c = env["client"]
        assert c.get("/v1/webhooks").status_code == 401
        assert c.get("/v1/webhooks", headers=_auth(env["publisher"])).status_code == 403


# ── Builders fleet ──────────────────────────────────────────────


class TestBuildersFleet:
    def test_reads_need_db(self, env):
        c = env["client"]
        assert c.get("/v1/builders").status_code == 501
        assert c.get("/v1/builders/1").status_code == 501

    def test_writes_need_db(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        assert c.patch(
            "/v1/builders/1", json={"status": "idle"}, headers=_auth(admin)
        ).status_code in (422, 501)
        assert c.post("/v1/builders/1/heartbeat", headers=_auth(pub)).status_code in (422, 501)
        assert c.delete("/v1/builders/1", headers=_auth(admin)).status_code == 501
        assert c.post(
            "/v1/builders/register",
            json={"hostname": "h", "platform": "linux", "arch": "x86_64"},
            headers=_auth(pub),
        ).status_code in (422, 501)

    def test_auth(self, env):
        c = env["client"]
        assert c.delete("/v1/builders/1").status_code == 401
        assert c.delete("/v1/builders/1", headers=_auth(env["reader"])).status_code == 403


# ── Build jobs ──────────────────────────────────────────────────


class TestBuildJobs:
    def test_lifecycle_endpoints_need_db(self, env):
        c, pub = env["client"], env["publisher"]
        h = _auth(pub)
        assert c.get("/v1/builds", headers=h).status_code == 501
        assert c.get("/v1/builds/next-claimable", headers=h).status_code in (422, 501)
        assert c.get("/v1/builds/1", headers=h).status_code == 501
        assert c.post("/v1/builds/1/cancel", headers=h).status_code == 501
        assert c.post("/v1/builds/1/pause", headers=h).status_code == 501
        assert c.post("/v1/builds/1/resume", headers=h).status_code == 501
        assert c.post("/v1/builds/dag/1/cancel", headers=h).status_code == 501
        assert c.post("/v1/builds/dag/1/pause", headers=h).status_code == 501
        assert c.post("/v1/builds/dag/1/resume", headers=h).status_code == 501
        assert c.post("/v1/builds/1/claim", json={"builder_id": 1}, headers=h).status_code in (
            422,
            501,
        )
        assert c.post("/v1/builds/1/complete", json={}, headers=h).status_code in (422, 501)
        assert c.post("/v1/builds/1/fail", json={}, headers=h).status_code in (422, 501)
        assert c.get("/v1/builds/1/log", headers=h).status_code == 501
        assert c.patch("/v1/builds/1/log", content=b"line", headers=h).status_code in (422, 501)
        assert c.get("/v1/builders/1/next-job", headers=h).status_code in (422, 501)

    def test_delete_log_admin_only(self, env):
        c = env["client"]
        assert c.delete("/v1/builds/1/log", headers=_auth(env["admin"])).status_code == 501
        assert c.delete("/v1/builds/1/log", headers=_auth(env["publisher"])).status_code == 403

    def test_auth(self, env):
        c = env["client"]
        assert c.get("/v1/builds").status_code == 401
        assert c.get("/v1/builds", headers=_auth(env["reader"])).status_code == 403
        assert c.post("/v1/builds/1/cancel", headers=_auth(env["reader"])).status_code == 403


# ── Admin GC / purge / quota ────────────────────────────────────


class TestAdminGc:
    def test_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert c.post("/v1/admin/gc/logs", headers=_auth(admin)).status_code == 501
        # older_than_days is ge=1; its server default may be 0, so pass it.
        assert (
            c.post(
                "/v1/admin/gc/yanked", params={"older_than_days": 30}, headers=_auth(admin)
            ).status_code
            == 501
        )
        assert c.post("/v1/admin/purge/builds", headers=_auth(admin)).status_code == 501
        assert c.get("/v1/admin/quota/logs/cvc-lab", headers=_auth(admin)).status_code == 501

    def test_auth(self, env):
        c = env["client"]
        assert c.post("/v1/admin/gc/logs").status_code == 401
        assert c.post("/v1/admin/gc/yanked", headers=_auth(env["publisher"])).status_code == 403


# ── Admin runtime settings (works locally) ──────────────────────


class TestAdminSettingsUpdate:
    def test_update_and_validation(self, env):
        c, admin = env["client"], env["admin"]
        ok = c.patch(
            "/v1/admin/settings",
            json={"global_cache_storage_limit_bytes": 1024},
            headers=_auth(admin),
        )
        assert ok.status_code == 200
        assert ok.json()["updated"]["global_cache_storage_limit_bytes"] == 1024
        # Negative and empty bodies are both rejected.
        assert (
            c.patch(
                "/v1/admin/settings", json={"org_storage_limit_bytes": -1}, headers=_auth(admin)
            ).status_code
            == 422
        )
        assert c.patch("/v1/admin/settings", json={}, headers=_auth(admin)).status_code == 422
        assert c.patch("/v1/admin/settings", json={"x": 1}).status_code == 401


# ── Organizations: mutations & logo (DB-gated) ──────────────────


class TestOrgMutations:
    def test_patch_logo_delete_member(self, env):
        c, admin = env["client"], env["admin"]
        assert (
            c.patch("/v1/orgs/x", json={"display_name": "X"}, headers=_auth(admin)).status_code
            == 501
        )
        assert c.request("DELETE", "/v1/orgs/x/members/y", headers=_auth(admin)).status_code == 501
        # Logo upload requires a multipart file; the DB gate fires regardless.
        logo = c.post(
            "/v1/orgs/x/logo",
            files={"file": ("l.png", io.BytesIO(b"\x89PNG"))},
            headers=_auth(admin),
        )
        assert logo.status_code in (422, 501)

    def test_auth(self, env):
        c = env["client"]
        assert c.patch("/v1/orgs/x", json={"display_name": "X"}).status_code == 401
        assert (
            c.patch(
                "/v1/orgs/x", json={"display_name": "X"}, headers=_auth(env["reader"])
            ).status_code
            == 403
        )


# ── OIDC entry points (dormant when unconfigured) ───────────────


class TestOidcEntryPoints:
    def test_dormant_404(self, env):
        c = env["client"]
        assert c.get("/admin/oidc/login", follow_redirects=False).status_code == 404
        assert c.get("/admin/oidc/callback", params={"code": "x", "state": "y"}).status_code == 404
        assert c.get("/auth/oidc/login", follow_redirects=False).status_code == 404
        assert c.get("/auth/oidc/callback", params={"code": "x"}).status_code == 404


# ── Logout endpoints (no session needed) ────────────────────────


class TestLogout:
    def test_admin_logout_redirects(self, env):
        r = env["client"].post("/admin/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin"

    def test_account_logout_redirects(self, env):
        r = env["client"].post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


# ── CLI-login (/link) & browser account (DB-gated) ──────────────


class TestLinkAndAccount:
    def test_link_pages_need_db(self, env):
        c = env["client"]
        assert c.get("/link", follow_redirects=False).status_code == 501
        assert c.post("/link/submit", data={"user_code": "ABCD-1234"}).status_code == 501
        assert c.post("/link/approve", data={"user_code": "ABCD-1234"}).status_code == 501
        assert c.post("/link/deny", data={"user_code": "ABCD-1234"}).status_code == 501

    def test_account_actions_need_db(self, env):
        c = env["client"]
        assert c.post("/account/tokens", data={"label": "l"}).status_code == 501
        assert c.post("/account/tokens/revoke", data={"name": "n"}).status_code == 501
        assert c.post("/account/rename", data={"name": "new"}).status_code == 501
        assert c.post("/account/sessions/5/revoke").status_code == 501
        assert c.post("/account/sessions/revoke-all").status_code == 501

    def test_org_manage_page_needs_db(self, env):
        assert env["client"].get("/org/x/manage", follow_redirects=False).status_code == 501
        assert env["client"].post("/org/x/members", data={"token_name": "y"}).status_code == 501
        assert (
            env["client"].post("/org/x/members/remove", data={"token_name": "y"}).status_code == 501
        )


# ── Admin principal console (session-guarded) ───────────────────


class TestAdminPrincipals:
    def test_actions_require_admin_session(self, env):
        c = env["client"]
        # No admin session cookie → 403 on the mutating console actions.
        assert c.post("/admin/principals/alice/disable").status_code == 403
        assert c.post("/admin/principals/alice/enable").status_code == 403
        assert c.post("/admin/principals/alice/revoke-sessions").status_code == 403
        assert (
            c.post("/admin/tokens/create", data={"name": "t", "role": "reader"}).status_code == 403
        )
        assert c.post("/admin/tokens/revoke", data={"name": "t"}).status_code == 403


# ── Mirror-mode download proxy (off outside mirror mode) ────────


class TestMirrorDownloadProxy:
    def test_only_in_mirror_mode(self, env):
        r = env["client"].get("/v1/mirror/download/anything.tar.zst")
        assert r.status_code == 404
