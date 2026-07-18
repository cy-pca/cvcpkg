"""Token rotation: in-place secret swap with an optional grace window.

Covers the store primitives on both backends (file-backed TokenStore and
DbTokenStore), the POST /v1/tokens/{name}/rotate endpoint's authz matrix,
and the `cvcpkg token rotate` CLI command.
"""

from __future__ import annotations

import asyncio
import datetime
import io
from unittest import mock

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")

from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRecord, TokenRole

_PAST = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)


# ── File-backed TokenStore ──────────────────────────────────────


class TestTokenStoreRotate:
    def test_rotate_swaps_secret_and_kills_old_immediately(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=0)
        assert new is not None and new != old and new.startswith("cvctok_")
        assert store.verify(new) is not None
        assert store.verify(old) is None

    def test_rotate_preserves_identity(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("bot", TokenRole.publisher, expires_in_days=30, email="a@b.c")
        new = store.rotate("bot")
        rec = store.verify(new)
        assert rec.name == "bot"
        assert rec.role == TokenRole.publisher
        assert rec.email == "a@b.c"
        assert rec.expires_at is not None

    def test_grace_window_keeps_old_secret_alive(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=5)
        assert store.verify(new) is not None
        assert store.verify(old) is not None  # still inside the window

    def test_grace_window_expiry_kills_old_secret(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=5)
        rec = store.verify(new)
        rec.previous_hash_expires_at = _PAST
        assert store.verify(old) is None
        assert store.verify(new) is not None

    def test_revoke_kills_both_secrets_during_grace(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=5)
        store.revoke("bot")
        assert store.verify(old) is None
        assert store.verify(new) is None

    def test_second_rotation_replaces_grace_secret(self, tmp_path):
        store = TokenStore(tmp_path)
        first = store.create("bot", TokenRole.publisher)
        second = store.rotate("bot", grace_minutes=5)
        third = store.rotate("bot", grace_minutes=5)
        assert store.verify(third) is not None
        assert store.verify(second) is not None  # in grace
        assert store.verify(first) is None  # two rotations ago

    def test_rotate_unknown_or_revoked_returns_none(self, tmp_path):
        store = TokenStore(tmp_path)
        assert store.rotate("ghost") is None
        store.create("bot", TokenRole.publisher)
        store.revoke("bot")
        assert store.rotate("bot") is None

    def test_rotate_expired_token_returns_none(self, tmp_path):
        store = TokenStore(tmp_path)
        new = store.create("bot", TokenRole.publisher)
        rec = store.verify(new)
        rec.expires_at = _PAST
        assert store.rotate("bot") is None

    def test_grace_window_clamped_to_token_expiry(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("bot", TokenRole.publisher, expires_in_days=1)
        new = store.rotate("bot", grace_minutes=10080)  # a week > 1 day
        rec = store.verify(new)
        assert rec.previous_hash_expires_at is not None
        assert rec.previous_hash_expires_at <= rec.expires_at

    def test_grace_verify_flags_previous_hash_and_does_not_persist_flag(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=5)
        assert store.verify(new).via_previous_hash is False
        assert store.verify(old).via_previous_hash is True
        # The transient flag must never reach tokens.yaml
        raw_yaml = (tmp_path / "tokens.yaml").read_text()
        assert "via_previous_hash" not in raw_yaml

    def test_rotation_survives_reload(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("bot", TokenRole.publisher)
        new = store.rotate("bot", grace_minutes=5)
        reloaded = TokenStore(tmp_path)
        assert reloaded.verify(new) is not None
        assert reloaded.verify(old) is not None


# ── DbTokenStore ────────────────────────────────────────────────


class TestDbTokenStoreRotate:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
        db_path = tmp_path / "rotate.db"
        self.db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", self.db_url)
        self.tmp_path = tmp_path

    def _run(self, coro_fn):
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _wrapped():
            init_db(self.db_url)
            await create_tables()
            store = DbTokenStore(self.tmp_path)
            try:
                return await coro_fn(store)
            finally:
                await dispose_engine()

        return asyncio.run(_wrapped())

    def test_rotate_swaps_secret_and_kills_old_immediately(self):
        async def scenario(store):
            old = await store.create("bot", TokenRole.publisher)
            new = await store.rotate("bot", grace_minutes=0)
            assert new is not None and new != old
            assert await store.verify(new) is not None
            assert await store.verify(old) is None

        self._run(scenario)

    def test_grace_window_keeps_old_alive_then_expires(self):
        async def scenario(store):
            from sqlalchemy import update

            from cvcpkg.server.db import TokenRow, get_session

            old = await store.create("bot", TokenRole.publisher)
            new = await store.rotate("bot", grace_minutes=5)
            assert await store.verify(old) is not None
            assert await store.verify(new) is not None
            # Force the window shut
            async with get_session() as session:
                await session.execute(
                    update(TokenRow)
                    .where(TokenRow.name == "bot")
                    .values(previous_hash_expires_at=_PAST)
                )
            assert await store.verify(old) is None
            assert await store.verify(new) is not None

        self._run(scenario)

    def test_revoke_kills_both_secrets_during_grace(self):
        async def scenario(store):
            old = await store.create("bot", TokenRole.publisher)
            new = await store.rotate("bot", grace_minutes=5)
            await store.revoke("bot")
            assert await store.verify(old) is None
            assert await store.verify(new) is None

        self._run(scenario)

    def test_rotate_unknown_or_revoked_returns_none(self):
        async def scenario(store):
            assert await store.rotate("ghost") is None
            await store.create("bot", TokenRole.publisher)
            await store.revoke("bot")
            assert await store.rotate("bot") is None

        self._run(scenario)

    def test_rotate_expired_token_returns_none(self):
        async def scenario(store):
            from sqlalchemy import update

            from cvcpkg.server.db import TokenRow, get_session

            await store.create("bot", TokenRole.publisher)
            async with get_session() as session:
                await session.execute(
                    update(TokenRow).where(TokenRow.name == "bot").values(expires_at=_PAST)
                )
            assert await store.rotate("bot") is None

        self._run(scenario)

    def test_grace_with_token_expiry_no_crash_and_clamped(self):
        # Regression: SQLite returns naive datetimes; the grace verify
        # path must not TypeError against aware `now`, and the window
        # must be clamped to the token's own expiry.
        async def scenario(store):
            old = await store.create("bot", TokenRole.publisher, expires_in_days=1)
            new = await store.rotate("bot", grace_minutes=10080)  # a week > 1 day
            rec_old = await store.verify(old)
            rec_new = await store.verify(new)
            assert rec_old is not None and rec_old.via_previous_hash is True
            assert rec_new is not None and rec_new.via_previous_hash is False
            assert rec_new.previous_hash_expires_at <= rec_new.expires_at

        self._run(scenario)


# ── REST endpoint ───────────────────────────────────────────────


@pytest.fixture()
def server_env(tmp_path):
    fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
    del fastapi
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app

    store = TokenStore(tmp_path)
    admin_token = store.create("test-admin", TokenRole.admin)
    pub_token = store.create("test-publisher", TokenRole.publisher)

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


class TestTokenRotateAPI:
    _probe_seq = 0

    def _self_probe(self, client, token, name):
        """Data-plane liveness probe (publish): 200 = the secret still works.

        Publish is deliberately chosen over a token-management call: a
        grace-window secret is *meant* to keep publishing during the swap,
        so this stays 200 for a live grace secret. 401 means the secret is
        dead. (`name` is unused; kept for call-site readability.)
        """
        del name
        TestTokenRotateAPI._probe_seq += 1
        seq = TestTokenRotateAPI._probe_seq
        return client.post(
            "/v1/publish",
            params={
                "name": f"probe{seq}",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("p.tar.zst", io.BytesIO(b"probe archive"))},
            headers={"Authorization": f"Bearer {token}"},
        ).status_code

    def test_admin_rotates_other_token(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 0},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-publisher"
        assert data["role"] == "publisher"
        assert data["token"].startswith("cvctok_")
        assert data["previous_valid_until"] is None
        # old secret is dead, new one works
        assert self._self_probe(client, pub_tok, "test-publisher") == 401
        assert self._self_probe(client, data["token"], "test-publisher") == 200

    def test_self_rotation_with_grace(self, server_env):
        client, _admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 30},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["previous_valid_until"] is not None
        # both secrets alive inside the window
        assert self._self_probe(client, pub_tok, "test-publisher") == 200
        assert self._self_probe(client, data["token"], "test-publisher") == 200

    def test_non_admin_cannot_rotate_others(self, server_env):
        client, _admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens/test-admin/rotate",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_unknown_token_404(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.post(
            "/v1/tokens/ghost/rotate",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404

    def test_missing_auth_401(self, server_env):
        client, *_ = server_env
        resp = client.post("/v1/tokens/test-publisher/rotate")
        assert resp.status_code == 401

    def test_empty_body_defaults_to_no_grace(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["previous_valid_until"] is None
        assert self._self_probe(client, pub_tok, "test-publisher") == 401

    def test_grace_out_of_bounds_422(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 999999},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 422

    def test_rotation_is_audited(self, server_env):
        client, admin_tok, *_ = server_env
        client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 10},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        actions = [e.get("action") for e in resp.json().get("entries", [])]
        assert "token_rotate" in actions

    def test_grace_secret_cannot_rotate(self, server_env):
        # A leaked pre-rotation secret must not be able to re-rotate and
        # steal the token during the grace window.
        client, _admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 30},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        new_secret = resp.json()["token"]
        # Old (grace) secret still authenticates for normal use...
        assert self._self_probe(client, pub_tok, "test-publisher") == 200
        # ...but is refused rotation.
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            json={"grace_minutes": 0},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403
        # The owner's current secret still rotates fine.
        resp = client.post(
            "/v1/tokens/test-publisher/rotate",
            headers={"Authorization": f"Bearer {new_secret}"},
        )
        assert resp.status_code == 200


class TestGraceSecretControlPlaneBoundary:
    """A grace-window secret may keep using the credential (publish) but
    must not reach any control-plane / IAM operation, or a leaked old
    secret could establish access that outlives the grace window."""

    def _grace_secret(self, client, admin_tok, name, role="publisher"):
        """Create `name`, rotate it with grace, return (old_grace, new_current)."""
        created = client.post(
            "/v1/tokens",
            json={"name": name, "role": role},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert created.status_code == 200
        old = created.json()["token"]
        rotated = client.post(
            f"/v1/tokens/{name}/rotate",
            json={"grace_minutes": 60},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert rotated.status_code == 200
        return old, rotated.json()["token"]

    def test_grace_secret_can_still_publish(self, server_env):
        client, admin_tok, _pub, _ = server_env
        old, _new = self._grace_secret(client, admin_tok, "pubrole")
        resp = client.post(
            "/v1/publish",
            params={
                "name": "graceable",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("g.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 200, resp.text

    def test_grace_admin_secret_cannot_create_token(self, server_env):
        # The critical finding: a grace admin secret minting a fresh
        # permanent admin token, outliving the grace window.
        client, admin_tok, _pub, _ = server_env
        old, _new = self._grace_secret(client, admin_tok, "adminrole", role="admin")
        resp = client.post(
            "/v1/tokens",
            json={"name": "backdoor", "role": "admin"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403

    def test_grace_secret_cannot_update_email(self, server_env):
        client, admin_tok, _pub, _ = server_env
        old, _new = self._grace_secret(client, admin_tok, "emailrole")
        resp = client.patch(
            "/v1/tokens/emailrole/email",
            json={"email": "attacker@evil.example"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403

    def test_grace_secret_cannot_update_profile(self, server_env):
        client, admin_tok, _pub, _ = server_env
        old, _new = self._grace_secret(client, admin_tok, "profrole")
        resp = client.patch(
            "/v1/tokens/profrole/profile",
            json={"description": "pwned"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403

    def test_grace_admin_secret_cannot_revoke_tokens(self, server_env):
        client, admin_tok, _pub, _ = server_env
        old, _new = self._grace_secret(client, admin_tok, "revrole", role="admin")
        resp = client.delete(
            "/v1/tokens/test-publisher",
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403

    def test_grace_secret_cannot_manage_org_members(self, server_env):
        client, admin_tok, _pub, tmp_path = server_env
        # Create an org owned by the token, then rotate the token to grace.
        created = client.post(
            "/v1/tokens",
            json={"name": "orgowner", "role": "publisher"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        owner_tok = created.json()["token"]
        org = client.post(
            "/v1/orgs",
            json={"slug": "acme", "display_name": "Acme"},
            headers={"Authorization": f"Bearer {owner_tok}"},
        )
        if org.status_code not in (200, 201):
            pytest.skip("orgs require the DB backend; not exercised on YAML env")
        rotated = client.post(
            "/v1/tokens/orgowner/rotate",
            json={"grace_minutes": 60},
            headers={"Authorization": f"Bearer {owner_tok}"},
        )
        old = rotated.json()["token"]
        resp = client.post(
            "/v1/orgs/acme/members",
            params={"token_name": "test-publisher", "role": "owner"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403


# ── CLI ─────────────────────────────────────────────────────────


class TestTokenRotateCLI:
    def test_rotate_command_output(self, capsys):
        from cvcpkg.cli import _server, main

        payload = {
            "name": "ci-bot",
            "role": "publisher",
            "token": "cvctok_NEWSECRET",
            "expires_at": None,
            "previous_valid_until": "2026-07-16T12:00:00+00:00",
        }
        with mock.patch.object(_server, "_api_request", return_value=payload) as api:
            ret = main(
                [
                    "token",
                    "rotate",
                    "--server",
                    "https://cvcpkg.example",
                    "--token",
                    "cvctok_admin",
                    "--name",
                    "ci-bot",
                    "--grace-minutes",
                    "60",
                ]
            )
        assert ret == 0
        method, url = api.call_args[0][:2]
        assert method == "post"
        assert url == "https://cvcpkg.example/v1/tokens/ci-bot/rotate"
        assert api.call_args.kwargs["json"] == {"grace_minutes": 60}
        out = capsys.readouterr().out
        assert "cvctok_NEWSECRET" in out
        assert "Old secret valid until" in out


# ── Grace-secret authentication-bypass fixes (DB backend) ───────
#
# Third security pass: three endpoints resolved the token manually
# (bypassing require_role's fail-closed grace gate) and checked only the
# role — so a pre-rotation grace secret could mint a durable admin
# session, open a builder socket, or read build logs.  These lock the
# fixes in and add coverage for control-plane endpoints whose grace
# rejection was previously only implied by require_role's default.


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server (orgs / webhooks / builds need the DB backend)."""
    pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'rot_sec.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


def _make_grace_secret(client, admin_tok, name, role="publisher", grace=60):
    """Create `name`, rotate it with grace, return (old_grace, new_current)."""
    created = client.post(
        "/v1/tokens",
        json={"name": name, "role": role},
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert created.status_code == 200, created.text
    old = created.json()["token"]
    rotated = client.post(
        f"/v1/tokens/{name}/rotate",
        json={"grace_minutes": grace},
        headers={"Authorization": f"Bearer {old}"},
    )
    assert rotated.status_code == 200, rotated.text
    return old, rotated.json()["token"]


class TestGraceSecretAuthBypass:
    def test_admin_login_rejects_grace_secret(self, db_server_env):
        # CRITICAL: a grace admin secret must not mint an admin session
        # cookie (which would outlive the grace window and grant full
        # control-plane via the dashboard).
        client, admin_tok, _pub, _ = db_server_env
        old, new = _make_grace_secret(client, admin_tok, "adminlogin", role="admin")
        # Old (grace) secret is refused a session...
        resp = client.post("/admin/login", data={"token": old}, follow_redirects=False)
        assert resp.status_code == 401
        assert "cvcpkg_admin_session" not in resp.cookies
        assert "set-cookie" not in {k.lower() for k in resp.headers}
        # ...the current secret still logs in fine.
        ok = client.post("/admin/login", data={"token": new}, follow_redirects=False)
        assert ok.status_code == 303

    def test_build_log_stream_rejects_grace_secret(self, db_server_env):
        client, admin_tok, _pub, _ = db_server_env
        old, _new = _make_grace_secret(client, admin_tok, "loguser", role="publisher")
        resp = client.get("/v1/builds/1/log/stream", headers={"Authorization": f"Bearer {old}"})
        assert resp.status_code == 403

    def test_builder_ws_rejects_grace_secret(self, db_server_env):
        from starlette.websockets import WebSocketDisconnect

        client, admin_tok, _pub, _ = db_server_env
        old, _new = _make_grace_secret(client, admin_tok, "wsuser", role="publisher")
        # The socket is closed (code 4003) before the builder handshake.
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/builders/1/ws?token={old}") as ws:
                ws.receive_text()


class TestGraceSecretControlPlaneDbEndpoints:
    """Control-plane endpoints whose grace rejection came only from
    require_role's fail-closed default — pin it so a future refactor that
    adds allow_grace=True (or a manual-verify path) can't silently reopen
    a durable-foothold hole."""

    def test_grace_secret_cannot_register_webhook(self, db_server_env):
        client, admin_tok, _pub, _ = db_server_env
        old, _new = _make_grace_secret(client, admin_tok, "hookadmin", role="admin")
        resp = client.post(
            "/v1/webhooks",
            json={"url": "https://attacker.example/exfil", "events": ["package.published"]},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403

    def test_grace_secret_cannot_create_org(self, db_server_env):
        client, admin_tok, _pub, _ = db_server_env
        old, _new = _make_grace_secret(client, admin_tok, "orgmaker", role="publisher")
        resp = client.post(
            "/v1/orgs",
            json={"slug": "graceorg", "display_name": "Grace Org"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert resp.status_code == 403


class TestRotationOrgMembershipAndDbEndpoint:
    def test_org_membership_survives_rotation(self, db_server_env):
        # The marquee value prop: membership is keyed by token NAME, which
        # rotation preserves, so the NEW secret can still publish to the org.
        client, admin_tok, _pub, _ = db_server_env
        created = client.post(
            "/v1/tokens",
            json={"name": "orgpub", "role": "publisher"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        secret = created.json()["token"]
        org = client.post(
            "/v1/orgs",
            json={"slug": "team", "display_name": "Team"},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert org.status_code in (200, 201), org.text
        # Owner is auto-member; rotate the token (grace 0 → old secret dies).
        rot = client.post(
            "/v1/tokens/orgpub/rotate",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert rot.status_code == 200
        new_secret = rot.json()["token"]
        # New secret still publishes to the org it owns.
        pub = client.post(
            "/v1/publish",
            params={
                "name": "teampkg",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "team",
            },
            files={"file": ("t.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {new_secret}"},
        )
        assert pub.status_code == 200, pub.text

    def test_rotate_endpoint_on_db_backend(self, db_server_env):
        # The endpoint rotation suite otherwise runs only on YAML; exercise
        # the DB path (rotate joins the transaction, response re-reads the
        # freshly-rotated row).
        client, admin_tok, _pub, _ = db_server_env
        client.post(
            "/v1/tokens",
            json={"name": "dbrot", "role": "publisher"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        resp = client.post(
            "/v1/tokens/dbrot/rotate",
            json={"grace_minutes": 30},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "publisher"  # not the admin actor's role
        assert data["token"].startswith("cvctok_")
        assert data["previous_valid_until"] is not None
        # New secret authenticates; a token_rotate audit row exists.
        audit = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_tok}"}).json()[
            "entries"
        ]
        assert any(e["action"] == "token_rotate" and e["target"] == "dbrot" for e in audit)


# ── Live builder-socket re-auth gate ────────────────────────────


class TestWsReauthRejection:
    """The builder-socket re-auth gate mirrors the connect-time checks so a
    revoked, expired, rotated, or demoted token cannot outlive its validity on
    an already-open WebSocket."""

    @staticmethod
    def _reject():
        pytest.importorskip("fastapi", reason="server extras not installed")
        from cvcpkg.server.app import _ws_reauth_rejection

        return _ws_reauth_rejection

    @staticmethod
    def _record(role=TokenRole.publisher, via_previous_hash=False):
        return TokenRecord(
            name="bot",
            role=role,
            token_hash="deadbeef",
            via_previous_hash=via_previous_hash,
        )

    def test_missing_record_closes_4001(self):
        # verify() returns None once the token is revoked/expired/grace-closed.
        assert self._reject()(None) == (4001, "token revoked or expired")

    def test_valid_publisher_is_kept(self):
        assert self._reject()(self._record()) is None

    def test_valid_admin_is_kept(self):
        assert self._reject()(self._record(role=TokenRole.admin)) is None

    def test_grace_secret_closes_4003(self):
        # The socket's secret is now only the pre-rotation grace hash: a
        # rotation must not leave the old secret holding a live socket.
        code, _reason = self._reject()(self._record(via_previous_hash=True))
        assert code == 4003

    def test_demoted_below_publisher_closes_4003(self):
        code, _reason = self._reject()(self._record(role=TokenRole.reader))
        assert code == 4003
