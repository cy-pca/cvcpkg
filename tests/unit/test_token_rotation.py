"""Token rotation: in-place secret swap with an optional grace window.

Covers the store primitives on both backends (file-backed TokenStore and
DbTokenStore), the POST /v1/tokens/{name}/rotate endpoint's authz matrix,
and the `cvcpkg token rotate` CLI command.
"""

from __future__ import annotations

import asyncio
import datetime
from unittest import mock

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")

from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole

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
    def _self_probe(self, client, token, name):
        """A request any live token can make about itself (200 = alive)."""
        return client.patch(
            f"/v1/tokens/{name}/email",
            json={"email": "probe@example.com"},
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
