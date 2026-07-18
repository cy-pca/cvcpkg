"""Tests for OIDC identity & access (roadmap Phase 13).

Covers the pure core (config, claim→role mapping, PKCE, the signed login
transaction) and the full login flow against a stubbed identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from cvcpkg.server import oidc as oidc_mod
from cvcpkg.server.oidc import (
    OidcConfig,
    build_authorize_url,
    claims_subject,
    map_claims_to_role,
    new_pkce_pair,
    sign_txn,
    verify_txn,
)

KEY = b"test-hmac-key"

# ── Config ──────────────────────────────────────────────────────


class TestConfig:
    @pytest.fixture(autouse=True)
    def _clear(self, monkeypatch):
        for v in (
            "CVCPKG_OIDC_ISSUER",
            "CVCPKG_OIDC_CLIENT_ID",
            "CVCPKG_OIDC_CLIENT_SECRET",
            "CVCPKG_OIDC_REDIRECT_URL",
            "CVCPKG_OIDC_SCOPES",
            "CVCPKG_OIDC_GROUPS_CLAIM",
            "CVCPKG_OIDC_ADMIN_GROUPS",
            "CVCPKG_OIDC_PUBLISHER_GROUPS",
            "CVCPKG_OIDC_ADMIN_EMAILS",
        ):
            monkeypatch.delenv(v, raising=False)

    def test_disabled_by_default(self):
        assert OidcConfig.from_env().is_enabled() is False

    def test_requires_all_four_fields(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_ISSUER", "https://idp.example")
        monkeypatch.setenv("CVCPKG_OIDC_CLIENT_ID", "cid")
        assert OidcConfig.from_env().is_enabled() is False  # secret+redirect missing
        monkeypatch.setenv("CVCPKG_OIDC_CLIENT_SECRET", "sec")
        monkeypatch.setenv("CVCPKG_OIDC_REDIRECT_URL", "https://cvcpkg.org/admin/oidc/callback")
        assert OidcConfig.from_env().is_enabled() is True

    def test_parses_lists_and_strips_issuer_slash(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_ISSUER", "https://idp.example/")
        monkeypatch.setenv("CVCPKG_OIDC_ADMIN_GROUPS", "cvc-admins, ops ")
        monkeypatch.setenv("CVCPKG_OIDC_PUBLISHER_GROUPS", "devs")
        monkeypatch.setenv("CVCPKG_OIDC_ADMIN_EMAILS", "a@x.io,b@x.io")
        c = OidcConfig.from_env()
        assert c.issuer == "https://idp.example"  # trailing slash stripped
        assert c.discovery_url == "https://idp.example/.well-known/openid-configuration"
        assert c.admin_groups == frozenset({"cvc-admins", "ops"})
        assert c.publisher_groups == frozenset({"devs"})
        assert c.admin_emails == frozenset({"a@x.io", "b@x.io"})


# ── Claim -> role mapping ───────────────────────────────────────


class TestClaimMapping:
    CFG = OidcConfig(
        groups_claim="groups",
        admin_groups=frozenset({"cvc-admins"}),
        publisher_groups=frozenset({"cvc-devs"}),
        admin_emails=frozenset({"boss@x.io"}),
    )

    def test_admin_group(self):
        assert map_claims_to_role({"groups": ["cvc-admins", "other"]}, self.CFG) == "admin"

    def test_publisher_group(self):
        assert map_claims_to_role({"groups": ["cvc-devs"]}, self.CFG) == "publisher"

    def test_admin_email_fallback(self):
        # IdPs that emit no groups can still map an admin by email.
        assert map_claims_to_role({"email": "BOSS@x.io"}, self.CFG) == "admin"  # case-insensitive

    def test_admin_group_beats_publisher(self):
        assert map_claims_to_role({"groups": ["cvc-devs", "cvc-admins"]}, self.CFG) == "admin"

    def test_unmapped_user_is_refused(self):
        # Authenticated at the IdP but no entitlement -> None (refused),
        # never silently downgraded to a usable role.
        assert map_claims_to_role({"groups": ["randos"], "email": "x@y.io"}, self.CFG) is None
        assert map_claims_to_role({}, self.CFG) is None

    def test_scalar_groups_claim(self):
        assert map_claims_to_role({"groups": "cvc-admins"}, self.CFG) == "admin"

    def test_custom_groups_claim(self):
        cfg = OidcConfig(groups_claim="roles", admin_groups=frozenset({"a"}))
        assert map_claims_to_role({"roles": ["a"]}, cfg) == "admin"
        assert map_claims_to_role({"groups": ["a"]}, cfg) is None  # wrong claim

    def test_no_config_means_no_entitlement(self):
        assert map_claims_to_role({"groups": ["anything"]}, OidcConfig()) is None

    def test_claims_subject_precedence(self):
        assert claims_subject({"email": "e@x", "sub": "123"}) == "e@x"
        assert claims_subject({"preferred_username": "bob", "sub": "123"}) == "bob"
        assert claims_subject({"sub": "123"}) == "123"
        assert claims_subject({}) == ""


# ── PKCE + signed transaction ───────────────────────────────────


class TestPkceAndTxn:
    def test_pkce_pair_is_s256(self):
        verifier, challenge = new_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected
        assert verifier != challenge
        assert new_pkce_pair()[0] != verifier  # fresh each call

    def test_txn_roundtrip(self):
        v = sign_txn(KEY, {"state": "s1", "verifier": "v1", "nonce": "n1"})
        body = verify_txn(KEY, v)
        assert body["state"] == "s1" and body["verifier"] == "v1" and body["nonce"] == "n1"

    def test_txn_wrong_key_rejected(self):
        assert verify_txn(b"other", sign_txn(KEY, {"state": "s"})) is None

    def test_txn_expired_rejected(self):
        assert verify_txn(KEY, sign_txn(KEY, {"state": "s"}, now=0)) is None

    def test_txn_tampered_rejected(self):
        v = sign_txn(KEY, {"state": "s1", "verifier": "v1"})
        raw, sig = v.split(".", 1)
        forged = json.dumps({"state": "evil", "verifier": "v1", "exp": 9999999999})
        raw2 = base64.urlsafe_b64encode(forged.encode()).rstrip(b"=").decode()
        assert verify_txn(KEY, f"{raw2}.{sig}") is None

    def test_txn_garbage_rejected(self):
        for junk in ("", "x", "a.b", "...."):
            assert verify_txn(KEY, junk) is None

    def test_authorize_url_has_pkce_and_state(self):
        cfg = OidcConfig(
            client_id="cid", redirect_url="https://cvcpkg.org/cb", scopes="openid email"
        )
        url = build_authorize_url(
            "https://idp.example/auth", cfg, state="st", nonce="nc", challenge="ch"
        )
        for frag in (
            "response_type=code",
            "client_id=cid",
            "state=st",
            "nonce=nc",
            "code_challenge=ch",
            "code_challenge_method=S256",
        ):
            assert frag in url, frag
        # The secret verifier must never appear in the redirect.
        assert "code_verifier" not in url

    def test_authorize_url_preserves_existing_query(self):
        cfg = OidcConfig(client_id="cid", redirect_url="https://cvcpkg.org/cb")
        url = build_authorize_url(
            "https://idp.example/auth?foo=1", cfg, state="s", nonce="n", challenge="c"
        )
        assert "?foo=1&" in url


# ── Flow against a stubbed IdP ──────────────────────────────────

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.server.admin_ui import _SESSION_COOKIE  # noqa: E402
from cvcpkg.server.app import create_app  # noqa: E402


@pytest.fixture()
def oidc_server(tmp_path, monkeypatch):
    """Server with OIDC configured and the IdP network calls stubbed."""
    import asyncio

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'oidc.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setenv("CVCPKG_OIDC_ISSUER", "https://idp.example")
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_SECRET", "sec")
    monkeypatch.setenv("CVCPKG_OIDC_REDIRECT_URL", "https://cvcpkg.org/admin/oidc/callback")
    monkeypatch.setenv("CVCPKG_OIDC_ADMIN_GROUPS", "cvc-admins")

    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _seed():
        init_db(db_url)
        await create_tables()
        await dispose_engine()

    asyncio.run(_seed())

    state = {"claims": {"email": "boss@x.io", "groups": ["cvc-admins"]}}

    async def fake_discover(cfg, **kw):
        return {
            "authorization_endpoint": "https://idp.example/auth",
            "token_endpoint": "https://idp.example/token",
            "userinfo_endpoint": "https://idp.example/userinfo",
        }

    async def fake_exchange(endpoint, cfg, *, code, verifier, **kw):
        state["exchanged"] = {"code": code, "verifier": verifier}
        return {"access_token": "at-123"}

    async def fake_userinfo(endpoint, access_token, **kw):
        state["userinfo_token"] = access_token
        return state["claims"]

    monkeypatch.setattr(oidc_mod, "discover", fake_discover)
    monkeypatch.setattr(oidc_mod, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_mod, "fetch_userinfo", fake_userinfo)

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, state


class TestOidcFlow:
    def test_login_page_offers_sso_when_enabled(self, oidc_server):
        client, _ = oidc_server
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Sign in with SSO" in r.text
        assert "/admin/oidc/login" in r.text

    def test_login_redirects_to_idp_with_pkce(self, oidc_server):
        client, _ = oidc_server
        r = client.get("/admin/oidc/login", follow_redirects=False)
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://idp.example/auth?")
        assert "code_challenge_method=S256" in loc
        assert "client_id=cid" in loc
        assert oidc_mod._TXN_COOKIE in r.cookies  # verifier kept server-side

    def test_full_flow_grants_admin_session(self, oidc_server):
        client, st = oidc_server
        r = client.get("/admin/oidc/login", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse

        sent_state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]

        r = client.get(f"/admin/oidc/callback?code=abc&state={sent_state}", follow_redirects=False)
        assert r.status_code == 303, r.text
        assert r.headers["location"] == "/admin"
        assert _SESSION_COOKIE in r.cookies
        # The PKCE verifier from the txn cookie was used in the exchange.
        assert st["exchanged"]["code"] == "abc"
        assert st["exchanged"]["verifier"]
        assert st["userinfo_token"] == "at-123"
        # Session works.
        assert "Overview" in client.get("/admin").text

    def test_state_mismatch_refused(self, oidc_server):
        client, _ = oidc_server
        client.get("/admin/oidc/login", follow_redirects=False)
        r = client.get("/admin/oidc/callback?code=abc&state=WRONG", follow_redirects=False)
        assert r.status_code == 400
        assert "invalid OIDC state" in r.text
        assert _SESSION_COOKIE not in r.cookies

    def test_callback_without_txn_cookie_refused(self, oidc_server):
        client, _ = oidc_server
        anon = TestClient(client.app)  # no txn cookie
        r = anon.get("/admin/oidc/callback?code=abc&state=s", follow_redirects=False)
        assert r.status_code == 400
        assert "expired" in r.text

    def test_idp_error_surfaces(self, oidc_server):
        client, _ = oidc_server
        r = client.get("/admin/oidc/callback?error=access_denied", follow_redirects=False)
        assert r.status_code == 401
        assert "access_denied" in r.text

    def test_unentitled_user_refused(self, oidc_server):
        client, st = oidc_server
        st["claims"] = {"email": "rando@x.io", "groups": ["nobody"]}
        r = client.get("/admin/oidc/login", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse

        sent_state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/admin/oidc/callback?code=abc&state={sent_state}", follow_redirects=False)
        assert r.status_code == 403
        assert "not authorized" in r.text
        assert _SESSION_COOKIE not in r.cookies


class TestOidcDisabled:
    def test_endpoints_404_when_unconfigured(self, tmp_path, monkeypatch):
        for v in (
            "CVCPKG_OIDC_ISSUER",
            "CVCPKG_OIDC_CLIENT_ID",
            "CVCPKG_OIDC_CLIENT_SECRET",
            "CVCPKG_OIDC_REDIRECT_URL",
        ):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            assert client.get("/admin/oidc/login", follow_redirects=False).status_code == 404
            assert client.get("/admin/oidc/callback?code=x&state=y").status_code == 404
            # and the login page does not advertise SSO
            assert "Sign in with SSO" not in client.get("/admin").text
