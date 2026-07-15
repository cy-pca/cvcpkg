"""Tests for opt-in client telemetry (Phase 2).

Covers the payload builder (anonymity + opt-in gating), the
telemetry_events store, and the POST /v1/telemetry +
GET /v1/analytics/telemetry endpoints.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from cvcpkg.cli._telemetry import (
    build_payload,
    maybe_send_telemetry,
    telemetry_enabled,
)

# ── Payload / gating (no server needed) ─────────────────────────


class TestPayload:
    def test_payload_is_anonymous(self):
        payload = build_payload()
        assert set(payload) == {
            "platform",
            "arch",
            "python_version",
            "cvcpkg_version",
            "ci",
            "tools",
        }
        blob = json.dumps(payload).lower()
        import getpass
        import socket

        # Never leak host/user identifiers.
        for secret in (socket.gethostname().lower(), getpass.getuser().lower()):
            if len(secret) >= 4:  # avoid trivial substrings
                assert secret not in blob

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_TELEMETRY", raising=False)
        assert telemetry_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("CVCPKG_TELEMETRY", val)
        assert telemetry_enabled() is True

    def test_maybe_send_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_TELEMETRY", raising=False)
        called = []
        monkeypatch.setattr(
            "cvcpkg.cli._telemetry.send_payload",
            lambda *a, **k: called.append(a) or True,
        )
        maybe_send_telemetry("http://example.invalid")
        assert called == []

    def test_maybe_send_fires_when_enabled(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TELEMETRY", "1")
        called = []
        monkeypatch.setattr(
            "cvcpkg.cli._telemetry.send_payload",
            lambda server, payload, **k: called.append(server) or True,
        )
        maybe_send_telemetry("http://example.invalid")
        assert called == ["http://example.invalid"]


# ── Store + endpoints (server extras required) ──────────────────

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.server.app import create_app  # noqa: E402
from cvcpkg.server.models import TokenRole  # noqa: E402


class TestTelemetryStore:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'telemetry.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield
        asyncio.run(dispose_engine())

    def test_record_and_summary(self):
        from cvcpkg.server.db_stores import DbTelemetryStore

        store = DbTelemetryStore()

        async def _do():
            await store.record(
                platform="linux",
                arch="x86_64",
                python_version="3.12.1",
                cvcpkg_version="2.0.0",
                ci=True,
                tools={"cmake": "3.31.0", "ninja": "1.12"},
            )
            await store.record(
                platform="windows",
                arch="x86_64",
                python_version="3.13.0",
                cvcpkg_version="2.0.0",
                ci=False,
            )
            return await store.get_summary(days=7)

        summary = asyncio.run(_do())
        assert summary["total"] == 2
        plats = {(p["platform"], p["arch"]): p["count"] for p in summary["platforms"]}
        assert plats[("linux", "x86_64")] == 1
        assert plats[("windows", "x86_64")] == 1
        vers = {v["version"]: v["count"] for v in summary["cvcpkg_versions"]}
        assert vers["2.0.0"] == 2
        ci = {c["ci"]: c["count"] for c in summary["ci"]}
        assert ci[True] == 1 and ci[False] == 1


@pytest.fixture()
def telemetry_server(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("test-admin", TokenRole.admin)
        pub = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin, pub

    admin_token, pub_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token


class TestTelemetryEndpoints:
    PAYLOAD = {
        "platform": "linux",
        "arch": "x86_64",
        "python_version": "3.12.1",
        "cvcpkg_version": "2.0.0",
        "ci": False,
        "tools": {"cmake": "3.31.0"},
    }

    def test_submit_is_public_and_204(self, telemetry_server):
        client, admin, _pub = telemetry_server
        r = client.post("/v1/telemetry", json=self.PAYLOAD)
        assert r.status_code == 204

        r = client.get(
            "/v1/analytics/telemetry",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["platforms"][0]["platform"] == "linux"
        assert {"version": "2.0.0", "count": 1} in data["cvcpkg_versions"]

    def test_submit_rejects_tool_flood(self, telemetry_server):
        client, _admin, _pub = telemetry_server
        payload = dict(self.PAYLOAD, tools={f"t{i}": "1" for i in range(20)})
        r = client.post("/v1/telemetry", json=payload)
        assert r.status_code == 422

    def test_submit_rejects_oversized_fields(self, telemetry_server):
        client, _admin, _pub = telemetry_server
        payload = dict(self.PAYLOAD, platform="x" * 100)
        r = client.post("/v1/telemetry", json=payload)
        assert r.status_code == 422

    def test_summary_requires_admin(self, telemetry_server):
        client, _admin, pub = telemetry_server
        r = client.get("/v1/analytics/telemetry", headers={"Authorization": f"Bearer {pub}"})
        assert r.status_code == 403
        r = client.get("/v1/analytics/telemetry")
        assert r.status_code in (401, 403)
