# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Multi-issuer OIDC login at the app level.

Two providers (``default`` + ``ringb``) configured against two stub IdPs.  The
load-bearing property is that the callback picks the issuer from the SIGNED txn
cookie, never from its own query — so a code minted by issuer A can never be
paired with provider B's client secret (an OAuth mix-up).
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")
pytest.importorskip("fastapi", reason="server extras not installed")

from cvcpkg.server import oidc as oidc_mod  # noqa: E402

_ALL = (
    "CVCPKG_OIDC_ISSUER",
    "CVCPKG_OIDC_CLIENT_ID",
    "CVCPKG_OIDC_CLIENT_SECRET",
    "CVCPKG_OIDC_REDIRECT_URL",
    "CVCPKG_OIDC_ADMIN_GROUPS",
    "CVCPKG_OIDC_DISPLAY_NAME",
    "CVCPKG_OIDC_EXTRA_PROVIDERS",
)


@pytest.fixture()
def two_idp_server(tmp_path, monkeypatch):
    """App with two OIDC providers and their IdP network calls stubbed."""
    import asyncio

    for v in _ALL:
        monkeypatch.delenv(v, raising=False)

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mi.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")

    # Provider "default" (bare vars).
    monkeypatch.setenv("CVCPKG_OIDC_ISSUER", "https://idp-a.example")
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_ID", "cid-a")
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_SECRET", "sec-a")
    monkeypatch.setenv("CVCPKG_OIDC_REDIRECT_URL", "https://cvcpkg.org/auth/oidc/callback")
    monkeypatch.setenv("CVCPKG_OIDC_ADMIN_GROUPS", "a-admins")
    monkeypatch.setenv("CVCPKG_OIDC_DISPLAY_NAME", "tx.wtf")

    # Provider "ringb" (JSON extra).
    monkeypatch.setenv(
        "CVCPKG_OIDC_EXTRA_PROVIDERS",
        json.dumps(
            [
                {
                    "id": "ringb",
                    "display_name": "Ring B",
                    "issuer": "https://idp-b.example",
                    "client_id": "cid-b",
                    "client_secret": "sec-b",
                    "redirect_url": "https://cvcpkg.org/auth/oidc/callback",
                    "admin_groups": ["b-admins"],
                }
            ]
        ),
    )

    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _seed():
        init_db(db_url)
        await create_tables()
        await dispose_engine()

    asyncio.run(_seed())

    # Per-issuer claims, keyed by issuer URL so the stub returns the identity
    # that belongs to whichever provider the callback actually chose.
    state = {
        "claims_by_issuer": {
            "https://idp-a.example": {
                "email": "a@x.io",
                "groups": ["a-admins"],
                "sub": "user-a",
            },
            "https://idp-b.example": {
                "email": "b@x.io",
                "groups": ["b-admins"],
                "sub": "user-b",
            },
        }
    }

    async def fake_discover(cfg, **kw):
        return {
            "authorization_endpoint": f"{cfg.issuer}/auth",
            "token_endpoint": f"{cfg.issuer}/token",
            "userinfo_endpoint": f"{cfg.issuer}/userinfo",
        }

    real_build = oidc_mod.build_authorize_url

    async def fake_exchange(endpoint, cfg, *, code, verifier, **kw):
        # Record which provider the callback resolved from the signed txn.
        state["exchanged"] = {"issuer": cfg.issuer, "id": cfg.id, "endpoint": endpoint}
        nonce = state.get("sent_nonce", "")
        body = base64.urlsafe_b64encode(json.dumps({"nonce": nonce}).encode())
        id_token = "hdr." + body.rstrip(b"=").decode() + ".sig"
        return {"access_token": f"at-{cfg.id}", "id_token": id_token}

    async def fake_userinfo(endpoint, access_token, **kw):
        issuer = endpoint.rsplit("/userinfo", 1)[0]
        return state["claims_by_issuer"][issuer]

    def spy_build(endpoint, cfg, **kw):
        state["sent_nonce"] = kw.get("nonce", "")
        return real_build(endpoint, cfg, **kw)

    monkeypatch.setattr(oidc_mod, "build_authorize_url", spy_build)
    monkeypatch.setattr(oidc_mod, "discover", fake_discover)
    monkeypatch.setattr(oidc_mod, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_mod, "fetch_userinfo", fake_userinfo)

    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, state


class TestProviderDiscovery:
    def test_providers_endpoint_lists_both(self, two_idp_server):
        client, _ = two_idp_server
        r = client.get("/v1/auth/providers")
        assert r.status_code == 200
        provs = {p["id"]: p["display_name"] for p in r.json()["providers"]}
        assert provs == {"default": "tx.wtf", "ringb": "Ring B"}

    def test_login_page_renders_a_picker(self, two_idp_server):
        client, _ = two_idp_server
        html = client.get("/login").text
        assert "Sign in with tx.wtf" in html
        assert "Sign in with Ring B" in html
        assert "provider=ringb" in html
        assert "provider=default" in html

    def test_admin_page_renders_a_picker(self, two_idp_server):
        client, _ = two_idp_server
        html = client.get("/admin").text
        assert "provider=ringb" in html
        assert "provider=default" in html


class TestProviderRouting:
    def test_unknown_provider_is_400(self, two_idp_server):
        client, _ = two_idp_server
        r = client.get("/admin/oidc/login?provider=nope", follow_redirects=False)
        assert r.status_code == 400
        assert "unknown OIDC provider" in r.text

    def test_ambiguous_login_shows_picker_not_redirect(self, two_idp_server):
        client, _ = two_idp_server
        r = client.get("/admin/oidc/login", follow_redirects=False)
        # No provider chosen and >1 configured -> render the chooser, not a 303.
        assert r.status_code == 200
        assert "Sign in with Ring B" in r.text

    def test_login_redirects_to_the_named_issuer(self, two_idp_server):
        client, _ = two_idp_server
        r = client.get("/admin/oidc/login?provider=ringb", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("https://idp-b.example/auth?")
        assert oidc_mod._TXN_COOKIE in r.cookies

    def test_callback_uses_provider_from_txn_not_env_default(self, two_idp_server):
        client, st = two_idp_server
        r = client.get("/admin/oidc/login?provider=ringb", follow_redirects=False)
        sent_state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]

        r = client.get(f"/auth/oidc/callback?code=zzz&state={sent_state}", follow_redirects=False)
        assert r.status_code == 303, r.text
        # The exchange ran against Ring B — the txn drove it, not the bare
        # (default) env provider.
        assert st["exchanged"]["id"] == "ringb"
        assert st["exchanged"]["issuer"] == "https://idp-b.example"

    def test_full_flow_signs_in_as_the_ring_identity(self, two_idp_server):
        client, st = two_idp_server
        r = client.get("/auth/oidc/login?provider=ringb&next=/account", follow_redirects=False)
        sent_state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/auth/oidc/callback?code=abc&state={sent_state}", follow_redirects=False)
        assert r.status_code == 303
        from cvcpkg.server import sessions as sessions_mod

        assert sessions_mod.COOKIE_NAME in r.cookies
        # The signed-in principal belongs to Ring B's issuer.
        acct = client.get("/account").text
        assert "https://idp-b.example" in acct
        assert "b@x.io" in acct


class TestSingleProviderBackCompat:
    def test_single_provider_still_goes_straight_through(self, tmp_path, monkeypatch):
        # With only the bare vars set, /admin/oidc/login redirects immediately —
        # no picker, exactly today's UX.
        import asyncio

        for v in _ALL:
            monkeypatch.delenv(v, raising=False)
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'single.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
        monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")
        monkeypatch.setenv("CVCPKG_OIDC_ISSUER", "https://idp-a.example")
        monkeypatch.setenv("CVCPKG_OIDC_CLIENT_ID", "cid-a")
        monkeypatch.setenv("CVCPKG_OIDC_CLIENT_SECRET", "sec-a")
        monkeypatch.setenv("CVCPKG_OIDC_REDIRECT_URL", "https://cvcpkg.org/auth/oidc/callback")

        async def _seed():
            from cvcpkg.server.db import create_tables, dispose_engine, init_db

            init_db(db_url)
            await create_tables()
            await dispose_engine()

        asyncio.run(_seed())

        async def fake_discover(cfg, **kw):
            return {"authorization_endpoint": f"{cfg.issuer}/auth"}

        monkeypatch.setattr(oidc_mod, "discover", fake_discover)

        from fastapi.testclient import TestClient

        from cvcpkg.server.app import create_app

        with TestClient(create_app(state_dir=tmp_path)) as client:
            r = client.get("/admin/oidc/login", follow_redirects=False)
            assert r.status_code == 303
            assert r.headers["location"].startswith("https://idp-a.example/auth?")
