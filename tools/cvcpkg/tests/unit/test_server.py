"""Tests for cvcpkg.server — auth, audit, and REST API endpoints."""

from __future__ import annotations

import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pydantic = pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.audit import AuditLog
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import AuditAction, TokenRole

# ── Auth / TokenStore ───────────────────────────────────────────


class TestTokenStore:
    def test_create_and_verify(self, tmp_path):
        store = TokenStore(tmp_path)
        raw = store.create("ci-bot", TokenRole.publisher)
        assert raw.startswith("cvctok_")
        record = store.verify(raw)
        assert record is not None
        assert record.name == "ci-bot"
        assert record.role == TokenRole.publisher

    def test_verify_invalid(self, tmp_path):
        store = TokenStore(tmp_path)
        assert store.verify("bogus-token") is None

    def test_revoke(self, tmp_path):
        store = TokenStore(tmp_path)
        raw = store.create("temp", TokenRole.reader)
        assert store.verify(raw) is not None
        store.revoke("temp")
        assert store.verify(raw) is None

    def test_duplicate_name_rejected(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("dup", TokenRole.reader)
        with pytest.raises(ValueError, match="already exists"):
            store.create("dup", TokenRole.reader)

    def test_persistence(self, tmp_path):
        store1 = TokenStore(tmp_path)
        raw = store1.create("persist-test", TokenRole.admin)
        # New instance should load from disk
        store2 = TokenStore(tmp_path)
        assert store2.verify(raw) is not None

    def test_list_tokens(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("a", TokenRole.reader)
        store.create("b", TokenRole.publisher)
        tokens = store.list_tokens()
        names = {t.name for t in tokens}
        assert names == {"a", "b"}

    def test_expiry(self, tmp_path):
        store = TokenStore(tmp_path)
        raw = store.create("expiring", TokenRole.reader, expires_in_days=-1)
        # Already expired
        assert store.verify(raw) is None


# ── Audit log ───────────────────────────────────────────────────


class TestAuditLog:
    def test_record_and_read(self, tmp_path):
        log = AuditLog(tmp_path)
        entry = log.record(AuditAction.publish, "ci-bot", "zlib==1.3.1")
        assert entry.id == 1
        assert entry.action == AuditAction.publish
        assert entry.prev_sha256 == ""

    def test_chain_integrity(self, tmp_path):
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, "bot", "zlib==1.0")
        log.record(AuditAction.publish, "bot", "boost==1.0")
        log.record(AuditAction.yank, "admin", "zlib==1.0")
        ok, msg = log.verify_chain()
        assert ok
        assert "3 entries" in msg

    def test_chain_detects_tampering(self, tmp_path):
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, "bot", "zlib==1.0")
        log.record(AuditAction.publish, "bot", "boost==1.0")
        # Tamper with the log
        log._entries[0].actor = "tampered"
        ok, msg = log.verify_chain()
        assert not ok
        assert "chain broken" in msg

    def test_filtering(self, tmp_path):
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, "bot", "zlib==1.0")
        log.record(AuditAction.yank, "admin", "zlib==1.0")
        log.record(AuditAction.publish, "bot", "boost==1.0")

        entries, total = log.entries(action=AuditAction.publish)
        assert total == 2

        entries, total = log.entries(target="zlib==1.0")
        assert total == 2

    def test_persistence(self, tmp_path):
        log1 = AuditLog(tmp_path)
        log1.record(AuditAction.publish, "bot", "zlib==1.0")

        log2 = AuditLog(tmp_path)
        entries, total = log2.entries()
        assert total == 1
        assert entries[0].target == "zlib==1.0"


# ── REST API ────────────────────────────────────────────────────


@pytest.fixture()
def server_env(tmp_path):
    """Create a FastAPI test client with a tmp state dir and admin token."""
    # Bootstrap tokens before starting the app so the lifespan loads them
    store = TokenStore(tmp_path)
    admin_token = store.create("test-admin", TokenRole.admin)
    pub_token = store.create("test-publisher", TokenRole.publisher)

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


class TestHealthEndpoint:
    def test_healthz(self, server_env):
        client, *_ = server_env
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestCatalogEndpoint:
    def test_empty_catalog(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bundles"] == []
        assert data["revision"] == 0


class TestPublishFlow:
    def test_publish_and_list(self, server_env):
        client, admin_tok, pub_tok, tmp_path = server_env
        archive = b"fake archive content for test"
        resp = client.post(
            "/v1/publish",
            params={
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("zlib-1.3.1.tar.zst", io.BytesIO(archive))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "zlib"
        assert data["sha256"]
        assert data["archive_url"].startswith("/v1/download/")

        # List packages
        resp = client.get("/v1/packages")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # Catalog shows it
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        assert len(resp.json()["bundles"]) == 1

    def test_publish_duplicate_rejected(self, server_env):
        client, _, pub_tok, _ = server_env
        archive = b"content"
        params = {
            "name": "zlib",
            "version": "1.0",
            "platform": "linux",
            "arch": "x86_64",
        }
        client.post(
            "/v1/publish",
            params=params,
            files={"file": ("z.tar.zst", io.BytesIO(archive))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.post(
            "/v1/publish",
            params=params,
            files={"file": ("z.tar.zst", io.BytesIO(archive))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 409

    def test_publish_requires_auth(self, server_env):
        client, *_ = server_env
        resp = client.post(
            "/v1/publish",
            params={"name": "zlib", "version": "1.0"},
            files={"file": ("z.tar.zst", io.BytesIO(b"data"))},
        )
        assert resp.status_code == 401

    def test_download(self, server_env):
        client, _, pub_tok, _ = server_env
        content = b"real archive bytes" * 100
        client.post(
            "/v1/publish",
            params={
                "name": "testpkg",
                "version": "0.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("testpkg.tar.zst", io.BytesIO(content))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        # Get the archive URL from the catalog
        catalog = client.get("/v1/catalog").json()
        url = catalog["bundles"][0]["archive_url"]
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.content == content

    def test_download_not_found(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/download/nonexistent.tar.zst")
        assert resp.status_code == 404


class TestYankFlow:
    def _publish(self, client, pub_tok):
        client.post(
            "/v1/publish",
            params={
                "name": "boost",
                "version": "1.86.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("b.tar.zst", io.BytesIO(b"boost data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )

    def test_yank_and_unyank(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        self._publish(client, pub_tok)

        # Yank
        resp = client.post(
            "/v1/packages/boost/1.86.0+cvc.1/yank",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200

        # Check yanked
        pkgs = client.get("/v1/packages/boost").json()
        assert pkgs["packages"][0]["yanked"] is True

        # Unyank (admin only)
        resp = client.post(
            "/v1/packages/boost/1.86.0+cvc.1/unyank",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        pkgs = client.get("/v1/packages/boost").json()
        assert pkgs["packages"][0]["yanked"] is False

    def test_unyank_requires_admin(self, server_env):
        client, _, pub_tok, _ = server_env
        self._publish(client, pub_tok)
        resp = client.post(
            "/v1/packages/boost/1.86.0+cvc.1/unyank",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403


class TestDeleteFlow:
    def test_delete_package(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        client.post(
            "/v1/publish",
            params={
                "name": "delpkg",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("d.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.delete(
            "/v1/packages/delpkg/1.0",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] == 1

        # Verify gone
        pkgs = client.get("/v1/packages/delpkg").json()
        assert pkgs["total"] == 0

    def test_delete_requires_admin(self, server_env):
        client, _, pub_tok, _ = server_env
        resp = client.delete(
            "/v1/packages/foo/1.0",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_delete_not_found(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.delete(
            "/v1/packages/nonexistent/1.0",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404


class TestTokenAPI:
    def test_create_token_via_api(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.post(
            "/v1/tokens",
            json={"name": "new-bot", "role": "reader"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "new-bot"
        assert data["token"].startswith("cvctok_")

    def test_list_tokens(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.get(
            "/v1/tokens",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()["tokens"]}
        assert "test-admin" in names
        assert "test-publisher" in names

    def test_revoke_token_via_api(self, server_env):
        client, admin_tok, *_ = server_env
        # Create a token to revoke
        resp = client.post(
            "/v1/tokens",
            json={"name": "disposable", "role": "reader"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        new_token = resp.json()["token"]

        # Revoke it
        resp = client.delete(
            "/v1/tokens/disposable",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        # Can't use it anymore
        resp = client.get(
            "/v1/tokens",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status_code in (401, 403)

    def test_create_token_requires_admin(self, server_env):
        client, _, pub_tok, _ = server_env
        resp = client.post(
            "/v1/tokens",
            json={"name": "sneaky", "role": "admin"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403


class TestAuditAPI:
    def test_audit_after_publish(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        # Publish something
        client.post(
            "/v1/publish",
            params={"name": "audit-test", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            files={"file": ("a.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        # Check audit
        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        actions = [e["action"] for e in data["entries"]]
        assert "publish" in actions

    def test_audit_verify(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        # Some operations
        client.post(
            "/v1/publish",
            params={"name": "v1", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            files={"file": ("v.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        client.post(
            "/v1/packages/v1/1.0/yank",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.get(
            "/v1/audit/verify",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_audit_requires_admin(self, server_env):
        client, _, pub_tok, _ = server_env
        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403


class TestPathTraversal:
    """Ensure archive download is safe against path traversal."""

    def test_path_traversal_blocked(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/download/../../etc/passwd")
        assert resp.status_code == 404

    def test_dotdot_in_filename(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/download/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code == 404


class TestUploadSizeLimit:
    """Ensure uploads exceeding MAX_UPLOAD_BYTES are rejected."""

    def test_oversized_upload_rejected(self, tmp_path, monkeypatch):
        import cvcpkg.server.app as app_mod

        monkeypatch.setattr(app_mod, "MAX_UPLOAD_BYTES", 100)

        store = TokenStore(tmp_path)
        pub_token = store.create("pub", TokenRole.publisher)
        test_app = create_app(state_dir=tmp_path)
        with TestClient(test_app) as client:
            big_content = b"x" * 200
            resp = client.post(
                "/v1/publish",
                params={"name": "toobig", "version": "1.0"},
                files={"file": ("toobig.tar.zst", io.BytesIO(big_content))},
                headers={"Authorization": f"Bearer {pub_token}"},
            )
            assert resp.status_code == 413
            assert "maximum size" in resp.json()["detail"]


class TestRateLimit:
    """Ensure rate limiting rejects excess requests."""

    def test_rate_limit_enforced(self, tmp_path, monkeypatch):
        import cvcpkg.server.app as app_mod

        monkeypatch.setattr(app_mod, "RATE_LIMIT_RPM", 2)

        store = TokenStore(tmp_path)
        pub_token = store.create("pub", TokenRole.publisher)
        test_app = create_app(state_dir=tmp_path)
        with TestClient(test_app) as client:
            content = b"archive data"
            for i in range(3):
                resp = client.post(
                    "/v1/publish",
                    params={"name": f"pkg{i}", "version": "1.0"},
                    files={"file": (f"pkg{i}.tar.zst", io.BytesIO(content))},
                    headers={"Authorization": f"Bearer {pub_token}"},
                )
                if i < 2:
                    assert resp.status_code == 200, f"request {i} should succeed"
                else:
                    assert resp.status_code == 429, "third request should be rate limited"


class TestMetricsEndpoint:
    """Verify Prometheus /metrics endpoint."""

    def test_metrics_returns_prometheus_text(self, server_env):
        client, *_ = server_env
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "cvcpkg_up 1" in body
        assert "cvcpkg_uptime_seconds" in body
        assert "cvcpkg_packages_total" in body
        assert "cvcpkg_requests_total" in body

    def test_metrics_increments_after_requests(self, server_env):
        client, *_ = server_env
        # Make a few requests
        client.get("/healthz")
        client.get("/v1/catalog")
        resp = client.get("/metrics")
        body = resp.text
        # requests_total should be > 0 (at least the prior calls + /metrics itself)
        for line in body.splitlines():
            if line.startswith("cvcpkg_requests_total "):
                count = int(line.split()[-1])
                assert count >= 3


class TestSQLInjectionPrevention:
    """Verify parameterised queries prevent SQL injection."""

    def test_sql_injection_in_package_name(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        # Attempt SQL injection via package name query parameter
        resp = client.get("/v1/packages", params={"name": "'; DROP TABLE packages; --"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_sql_injection_in_path_param(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/packages/%27%3B%20DROP%20TABLE%20packages%3B%20--")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_sql_injection_in_publish_name(self, server_env):
        client, admin_tok, pub_tok, _ = server_env
        resp = client.post(
            "/v1/publish",
            params={
                "name": "x'; DROP TABLE packages; --",
                "version": "1.0",
            },
            files={"file": ("test.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        # Should succeed (name is just a string held in YAML) or 200 for yaml backend
        assert resp.status_code == 200


class TestMalformedTokens:
    """Verify malformed Authorization headers are safely rejected."""

    def test_empty_bearer(self, server_env):
        client, *_ = server_env
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    def test_no_bearer_prefix(self, server_env):
        client, *_ = server_env
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": "Token some-value"},
        )
        assert resp.status_code == 401

    def test_bearer_with_extra_parts(self, server_env):
        client, *_ = server_env
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": "Bearer tok1 tok2 tok3"},
        )
        assert resp.status_code == 401

    def test_garbage_token(self, server_env):
        client, *_ = server_env
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": "Bearer !!!invalid-garbage-$$$"},
        )
        assert resp.status_code == 401


class TestConcurrentPublish:
    """Verify concurrent publishes to different packages don't conflict."""

    def test_concurrent_publishes(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        store = TokenStore(tmp_path)
        pub_token = store.create("pub", TokenRole.publisher)
        test_app = create_app(state_dir=tmp_path)

        with TestClient(test_app) as client:

            def publish_one(idx):
                resp = client.post(
                    "/v1/publish",
                    params={"name": f"concurrent-{idx}", "version": "1.0"},
                    files={"file": (f"c{idx}.tar.zst", io.BytesIO(b"archive" * (idx + 1)))},
                    headers={"Authorization": f"Bearer {pub_token}"},
                )
                return resp.status_code

            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(publish_one, i) for i in range(4)]
                results = [f.result() for f in futures]

        assert all(s == 200 for s in results), f"Some publishes failed: {results}"


class TestAuditChainTamperDetection:
    """Verify audit chain detects tampering."""

    def test_tamper_detected(self, tmp_path):
        from cvcpkg.server.audit import AuditLog
        from cvcpkg.server.models import AuditAction

        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, "bot", "zlib==1.0")
        log.record(AuditAction.publish, "bot", "boost==1.0")

        # Verify chain is valid before tampering
        ok, _ = log.verify_chain()
        assert ok

        # Tamper with the audit log file
        audit_file = tmp_path / "audit.yaml"
        content = audit_file.read_text()
        content = content.replace("zlib==1.0", "TAMPERED==0.0")
        audit_file.write_text(content)

        # Reload and verify — should detect tampering
        log2 = AuditLog(tmp_path)
        ok, msg = log2.verify_chain()
        assert not ok
        assert "chain broken" in msg.lower() or "mismatch" in msg.lower()
