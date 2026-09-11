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
    # TestClient speaks plain http, and a browser never returns a Secure cookie
    # over http.  Production is https and keeps the default.
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")

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

    real_build_authorize_url = oidc_mod.build_authorize_url

    async def fake_exchange(endpoint, cfg, *, code, verifier, **kw):
        state["exchanged"] = {"code": code, "verifier": verifier}
        # A conforming IdP echoes the nonce it was handed on the authorize
        # request; the callback binds the response to the login attempt with it.
        nonce = state.get("nonce_override", state.get("sent_nonce", ""))
        body = base64.urlsafe_b64encode(json.dumps({"nonce": nonce}).encode())
        id_token = "hdr." + body.rstrip(b"=").decode() + ".sig"
        return {"access_token": "at-123", "id_token": id_token}

    async def fake_userinfo(endpoint, access_token, **kw):
        state["userinfo_token"] = access_token
        return state["claims"]

    def spy_build_authorize_url(endpoint, cfg, **kw):
        state["sent_nonce"] = kw.get("nonce", "")
        return real_build_authorize_url(endpoint, cfg, **kw)

    monkeypatch.setattr(oidc_mod, "build_authorize_url", spy_build_authorize_url)
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


class TestNonceBinding:
    """The nonce was minted and sent but never checked until 2026-09."""

    def test_idp_echoing_a_different_nonce_is_refused(self, oidc_server):
        client, st = oidc_server
        st["nonce_override"] = "attacker-supplied-nonce"
        r = client.get("/admin/oidc/login", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse

        sent = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/admin/oidc/callback?code=abc&state={sent}", follow_redirects=False)
        assert r.status_code == 400
        assert "nonce mismatch" in r.text
        assert _SESSION_COOKIE not in r.cookies

    def test_idp_omitting_the_id_token_is_refused(self, oidc_server, monkeypatch):
        client, st = oidc_server

        async def no_id_token(endpoint, cfg, *, code, verifier, **kw):
            return {"access_token": "at-123"}

        monkeypatch.setattr(oidc_mod, "exchange_code", no_id_token)
        r = client.get("/admin/oidc/login", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse

        sent = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = client.get(f"/admin/oidc/callback?code=abc&state={sent}", follow_redirects=False)
        assert r.status_code == 400
        assert _SESSION_COOKIE not in r.cookies


class TestCookieHardening:
    def test_session_cookie_is_secure_by_default(self, oidc_server, monkeypatch):
        """Production is https; Secure must not depend on remembering a flag."""
        client, _ = oidc_server
        monkeypatch.delenv("CVCPKG_COOKIE_SECURE", raising=False)
        r = client.get("/admin/oidc/login", follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()

    def test_secure_can_be_disabled_for_plain_http_deployments(self, oidc_server):
        client, _ = oidc_server  # fixture sets CVCPKG_COOKIE_SECURE=0
        r = client.get("/admin/oidc/login", follow_redirects=False)
        assert "secure" not in r.headers["set-cookie"].lower()


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


# ── Reader tier + default role (2026-09 SSO work) ───────────────


_TIERED = OidcConfig(
    admin_groups=frozenset({"cvcpkg-admin"}),
    publisher_groups=frozenset({"cvcpkg-publisher"}),
    reader_groups=frozenset({"cvcpkg-reader"}),
)


def test_reader_group_maps_to_reader():
    assert map_claims_to_role({"groups": ["cvcpkg-reader"]}, _TIERED) == "reader"


def test_reader_group_ranks_below_publisher():
    """A user in both gets the higher role, not the last one matched."""
    claims = {"groups": ["cvcpkg-reader", "cvcpkg-publisher"]}
    assert map_claims_to_role(claims, _TIERED) == "publisher"


def test_no_group_match_still_refuses_without_a_default():
    assert map_claims_to_role({"groups": ["unrelated"]}, _TIERED) is None


def test_default_role_applies_when_no_group_matches():
    cfg = OidcConfig(admin_groups=frozenset({"cvcpkg-admin"}), default_role="reader")
    assert map_claims_to_role({"groups": ["unrelated"]}, cfg) == "reader"
    assert map_claims_to_role({}, cfg) == "reader"


def test_default_role_never_overrides_a_real_group_match():
    cfg = OidcConfig(admin_groups=frozenset({"cvcpkg-admin"}), default_role="reader")
    assert map_claims_to_role({"groups": ["cvcpkg-admin"]}, cfg) == "admin"


# ── Startup validation ──────────────────────────────────────────


@pytest.mark.parametrize("universal", ["user", "users", "everyone", "authenticated", "USER"])
def test_universal_group_in_admin_map_is_refused(universal):
    """Every self-registered IdP account holds these, so mapping one to a role
    silently makes 'can sign up' equivalent to that role."""
    problems = oidc_mod.validate_config(OidcConfig(admin_groups=frozenset({universal})))
    assert problems and "CVCPKG_OIDC_ADMIN_GROUPS" in problems[0]


def test_universal_group_in_publisher_map_is_refused():
    problems = oidc_mod.validate_config(OidcConfig(publisher_groups=frozenset({"user"})))
    assert problems and "CVCPKG_OIDC_PUBLISHER_GROUPS" in problems[0]


def test_dedicated_group_names_are_accepted():
    assert oidc_mod.validate_config(_TIERED) == []


def test_default_role_admin_is_refused():
    problems = oidc_mod.validate_config(OidcConfig(default_role="admin"))
    assert problems and "every authenticated user an admin" in problems[0]


def test_unknown_default_role_is_refused():
    problems = oidc_mod.validate_config(OidcConfig(default_role="wizard"))
    assert problems and "not one of" in problems[0]


def test_reader_default_role_is_accepted():
    assert oidc_mod.validate_config(OidcConfig(default_role="reader")) == []


# ── id_token nonce binding ──────────────────────────────────────


def _id_token(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


def test_nonce_matches_when_idp_echoes_it():
    assert oidc_mod.nonce_matches(_id_token({"nonce": "abc123"}), "abc123")


def test_nonce_mismatch_is_refused():
    assert not oidc_mod.nonce_matches(_id_token({"nonce": "abc123"}), "different")


def test_missing_nonce_claim_is_refused():
    """Absence must not be a pass — the IdP is the party this guards against."""
    assert not oidc_mod.nonce_matches(_id_token({"sub": "joe"}), "abc123")


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.!!!.c"])
def test_malformed_id_token_is_refused(token):
    assert not oidc_mod.nonce_matches(token, "abc123")


def test_empty_expected_nonce_is_refused():
    assert not oidc_mod.nonce_matches(_id_token({"nonce": ""}), "")


def test_id_token_claims_decodes_payload_without_verifying():
    claims = oidc_mod.id_token_claims(_id_token({"sub": "joe", "nonce": "n"}))
    assert claims == {"sub": "joe", "nonce": "n"}


# ── Discovery caching ───────────────────────────────────────────


@pytest.mark.anyio
async def test_discovery_is_cached_within_its_ttl(monkeypatch):
    """Both legs of a login call discover(); it should cost one round trip."""
    cfg = OidcConfig(issuer="https://idp.test.invalid")
    oidc_mod._discovery_cache.clear()
    calls = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"authorization_endpoint": "https://idp.test.invalid/auth"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls.append(url)
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    await oidc_mod.discover(cfg, now=1000.0)
    await oidc_mod.discover(cfg, now=1100.0)
    assert len(calls) == 1
    # Past the TTL it refetches rather than serving a stale document forever.
    await oidc_mod.discover(cfg, now=1000.0 + oidc_mod._DISCOVERY_TTL_SECONDS + 1)
    assert len(calls) == 2
    oidc_mod._discovery_cache.clear()


# ── Session lifetime knob ───────────────────────────────────────


def test_session_ttl_defaults_to_one_working_day(monkeypatch):
    from cvcpkg.server import admin_ui

    monkeypatch.delenv("CVCPKG_SESSION_TTL_SECONDS", raising=False)
    assert admin_ui._session_ttl_seconds() == 8 * 3600


def test_session_ttl_is_overridable(monkeypatch):
    from cvcpkg.server import admin_ui

    monkeypatch.setenv("CVCPKG_SESSION_TTL_SECONDS", "900")
    assert admin_ui._session_ttl_seconds() == 900


@pytest.mark.parametrize("bad", ["", "not-a-number", "0", "-5"])
def test_session_ttl_rejects_nonsense_without_crashing(monkeypatch, bad):
    """A malformed value must not make every session immortal or instantly dead."""
    from cvcpkg.server import admin_ui

    monkeypatch.setenv("CVCPKG_SESSION_TTL_SECONDS", bad)
    ttl = admin_ui._session_ttl_seconds()
    assert ttl >= 60


def test_session_value_uses_the_configured_ttl(monkeypatch):
    from cvcpkg.server import admin_ui

    monkeypatch.setenv("CVCPKG_SESSION_TTL_SECONDS", "900")
    value = admin_ui.make_session_value(b"k", now=1000.0)
    assert int(value.split(".", 1)[0]) == 1900


# ── Secure-cookie / proxy detection ─────────────────────────────


def _fake_request(scheme: str, headers: dict | None = None):
    from types import SimpleNamespace

    return SimpleNamespace(url=SimpleNamespace(scheme=scheme), headers=headers or {})


@pytest.mark.parametrize(
    "scheme,headers,expected",
    [
        # Nothing says TLS: a Secure cookie here is silently dropped.
        ("http", {}, True),
        # cvcpkg.org's shape — Apache terminates TLS, uvicorn sees http.
        ("http", {"x-forwarded-proto": "https"}, False),
        # A proxy chain: the left-most hop is what the client spoke.
        ("http", {"x-forwarded-proto": "https, http"}, False),
        ("https", {}, False),
        ("http", {"x-forwarded-proto": "http"}, True),
    ],
)
def test_plain_http_detection(scheme, headers, expected):
    """The warning must not fire for a correctly proxied production server."""
    from cvcpkg.server import admin_ui

    assert admin_ui._looks_like_plain_http(_fake_request(scheme, headers)) is expected


def test_cookie_kwargs_are_secure_and_httponly_by_default(monkeypatch):
    from cvcpkg.server import admin_ui

    monkeypatch.delenv("CVCPKG_COOKIE_SECURE", raising=False)
    kw = admin_ui.session_cookie_kwargs()
    assert kw["secure"] is True
    assert kw["httponly"] is True
    assert kw["samesite"] == "lax"


def test_cookie_secure_opt_out(monkeypatch):
    from cvcpkg.server import admin_ui

    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")
    assert admin_ui.session_cookie_kwargs()["secure"] is False


def test_plain_http_warning_fires_once(monkeypatch, caplog):
    """Once per process, not once per login — this is advice, not an alarm."""
    import logging

    from cvcpkg.server import admin_ui

    monkeypatch.delenv("CVCPKG_COOKIE_SECURE", raising=False)
    monkeypatch.setattr(admin_ui, "_warned_insecure_scheme", False)
    req = _fake_request("http", {})
    with caplog.at_level(logging.WARNING, logger="cvcpkg.server"):
        admin_ui.session_cookie_kwargs(request=req)
        admin_ui.session_cookie_kwargs(request=req)
    hits = [r for r in caplog.records if "CVCPKG_COOKIE_SECURE" in r.message]
    assert len(hits) == 1


def test_no_warning_when_proxied(monkeypatch, caplog):
    import logging

    from cvcpkg.server import admin_ui

    monkeypatch.delenv("CVCPKG_COOKIE_SECURE", raising=False)
    monkeypatch.setattr(admin_ui, "_warned_insecure_scheme", False)
    req = _fake_request("http", {"x-forwarded-proto": "https"})
    with caplog.at_level(logging.WARNING, logger="cvcpkg.server"):
        admin_ui.session_cookie_kwargs(request=req)
    assert not [r for r in caplog.records if "CVCPKG_COOKIE_SECURE" in r.message]
