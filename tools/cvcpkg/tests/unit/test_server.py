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

        # Yanked packages hidden from default listing
        pkgs = client.get("/v1/packages/boost").json()
        assert pkgs["total"] == 0

        # But visible with include_yanked=true
        pkgs = client.get("/v1/packages/boost?include_yanked=true").json()
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


# ── Chunked upload ──────────────────────────────────────────────


class TestChunkedUpload:
    """Test the chunked / resumable upload flow."""

    def test_chunked_upload_full_flow(self, server_env):
        """Init → chunks → complete → download verifies content."""
        client, admin_token, pub_token, tmp_path = server_env
        content = b"A" * 1024 * 50  # 50 KiB in total

        # Init session
        resp = client.post(
            "/v1/upload/init",
            params={
                "name": "chunked-test",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "total_size": len(content),
            },
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        upload_id = data["upload_id"]
        assert "chunk_size" in data

        # Upload in two chunks
        half = len(content) // 2
        chunk1 = content[:half]
        chunk2 = content[half:]

        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=chunk1,
            headers={
                "Authorization": f"Bearer {pub_token}",
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes 0-{half - 1}/{len(content)}",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["bytes_received"] == half

        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=chunk2,
            headers={
                "Authorization": f"Bearer {pub_token}",
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {half}-{len(content) - 1}/{len(content)}",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["bytes_received"] == len(content)

        # Complete
        import hashlib

        expected_sha256 = hashlib.sha256(content).hexdigest()
        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": expected_sha256},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["sha256"] == expected_sha256
        assert result["name"] == "chunked-test"

        # Verify download
        resp = client.get(result["archive_url"])
        assert resp.status_code == 200
        assert resp.content == content

    def test_upload_status(self, server_env):
        """GET /v1/upload/{id} returns bytes received."""
        client, admin_token, pub_token, tmp_path = server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "status-test", "version": "1.0"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 201
        upload_id = resp.json()["upload_id"]

        resp = client.get(
            f"/v1/upload/{upload_id}",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["bytes_received"] == 0

    def test_upload_sha256_mismatch(self, server_env):
        """Complete with wrong SHA-256 returns 422."""
        client, admin_token, pub_token, tmp_path = server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "sha-test", "version": "1.0"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        upload_id = resp.json()["upload_id"]

        client.patch(
            f"/v1/upload/{upload_id}",
            content=b"hello",
            headers={
                "Authorization": f"Bearer {pub_token}",
                "Content-Type": "application/octet-stream",
            },
        )

        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={
                "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
            },
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 422
        assert "mismatch" in resp.json()["detail"].lower()

    def test_upload_not_found(self, server_env):
        """Operations on nonexistent upload_id return 404."""
        client, admin_token, pub_token, tmp_path = server_env
        headers = {"Authorization": f"Bearer {pub_token}"}

        assert client.get("/v1/upload/nonexistent", headers=headers).status_code == 404
        assert (
            client.patch("/v1/upload/nonexistent", content=b"x", headers=headers).status_code == 404
        )
        assert client.post("/v1/upload/nonexistent/complete", headers=headers).status_code == 404
        assert client.delete("/v1/upload/nonexistent", headers=headers).status_code == 404

    def test_upload_cancel(self, server_env):
        """DELETE /v1/upload/{id} discards the session."""
        client, admin_token, pub_token, tmp_path = server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "cancel-test", "version": "1.0"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        upload_id = resp.json()["upload_id"]

        resp = client.delete(
            f"/v1/upload/{upload_id}",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 204

        # Session should be gone
        resp = client.get(
            f"/v1/upload/{upload_id}",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 404

    def test_upload_duplicate_rejected(self, server_env):
        """Init rejects duplicate component (409)."""
        client, admin_token, pub_token, tmp_path = server_env

        # Publish something first via simple upload
        client.post(
            "/v1/publish",
            params={"name": "dup-chunked", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            files={"file": ("dup.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_token}"},
        )

        # Try chunked init for same coordinates
        resp = client.post(
            "/v1/upload/init",
            params={"name": "dup-chunked", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 409

    def test_upload_offset_mismatch(self, server_env):
        """Chunk with wrong Content-Range offset returns 409."""
        client, admin_token, pub_token, tmp_path = server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "offset-test", "version": "1.0"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        upload_id = resp.json()["upload_id"]

        # First chunk at offset 0 succeeds
        client.patch(
            f"/v1/upload/{upload_id}",
            content=b"A" * 100,
            headers={
                "Authorization": f"Bearer {pub_token}",
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 0-99/1000",
            },
        )

        # Second chunk with wrong offset (should be 100, not 0)
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=b"B" * 100,
            headers={
                "Authorization": f"Bearer {pub_token}",
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 0-99/1000",
            },
        )
        assert resp.status_code == 409
        assert "offset mismatch" in resp.json()["detail"].lower()

    def test_upload_empty_complete_rejected(self, server_env):
        """Complete with zero bytes uploaded returns 400."""
        client, admin_token, pub_token, tmp_path = server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "empty-test", "version": "1.0"},
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        upload_id = resp.json()["upload_id"]

        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 400


# ── Organization endpoints (file-backend, no DB) ───────────────


class TestOrgEndpointsNoDB:
    """Org endpoints should gracefully degrade when no DB backend is configured."""

    def test_create_org_returns_501(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.post(
            "/v1/orgs",
            json={
                "slug": "my-org",
                "display_name": "My Org",
            },
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 501

    def test_list_orgs_empty_without_db(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/orgs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["organizations"] == []

    def test_get_org_returns_404_without_db(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/orgs/nonexistent")
        assert resp.status_code == 404

    def test_update_org_returns_501(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.patch(
            "/v1/orgs/some-org",
            json={"display_name": "Updated"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 501

    def test_upload_logo_returns_501(self, server_env):
        client, admin_tok, *_ = server_env
        resp = client.post(
            "/v1/orgs/some-org/logo",
            files={"file": ("logo.png", io.BytesIO(b"\x89PNG"), "image/png")},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 501

    def test_serve_logo_returns_404(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/orgs/nonexistent/logo")
        assert resp.status_code == 404


class TestOrgLogoServe:
    """Test logo serving from disk (no DB required for the serve endpoint)."""

    def test_serve_logo_png(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("admin", TokenRole.admin)
        # Place a logo file directly
        logos_dir = tmp_path / "logos"
        logos_dir.mkdir()
        (logos_dir / "test-org.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/v1/orgs/test-org/logo")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/png"

    def test_serve_logo_not_found(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("admin", TokenRole.admin)
        # Ensure logos dir exists but no logo for this slug
        (tmp_path / "logos").mkdir()

        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            resp = client.get("/v1/orgs/no-org/logo")
            assert resp.status_code == 404


class TestOrgHTMLPages:
    """Test that org HTML pages render without errors."""

    def test_orgs_listing_page(self, server_env):
        client, *_ = server_env
        resp = client.get("/orgs")
        assert resp.status_code == 200
        assert "Organizations" in resp.text

    def test_org_detail_page(self, server_env):
        client, *_ = server_env
        resp = client.get("/org/test-org")
        assert resp.status_code == 200


class TestOptionalToken:
    """Test that the optional_token dependency works correctly."""

    def test_get_org_without_auth(self, server_env):
        """GET /v1/orgs/{slug} should work without auth (for public orgs or 404)."""
        client, *_ = server_env
        resp = client.get("/v1/orgs/nonexistent")
        # Should get 404 not 401 — auth is optional
        assert resp.status_code == 404

    def test_orgs_list_without_auth(self, server_env):
        client, *_ = server_env
        resp = client.get("/v1/orgs")
        assert resp.status_code == 200


class TestPublishWithOrg:
    """Test that the publish endpoint accepts the org query parameter."""

    def test_publish_with_org_param(self, server_env):
        client, _, pub_tok, _ = server_env
        archive = b"fake archive content"
        resp = client.post(
            "/v1/publish",
            params={
                "name": "org-test-pkg",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "cvc-lab",
            },
            files={"file": ("org-test-pkg.tar.zst", io.BytesIO(archive))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "org-test-pkg"


# ── validate_org_slug ───────────────────────────────────────────


class TestValidateOrgSlug:
    """GitHub-style org slug validation."""

    def test_valid_slugs(self):
        from cvcpkg.server.models import validate_org_slug

        for slug in ["a", "ab", "cvc-lab", "my123", "a1b2c3", "x" * 39]:
            assert validate_org_slug(slug) is None, f"should accept: {slug}"

    def test_empty(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("") is not None

    def test_too_long(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("x" * 40) is not None

    def test_consecutive_hyphens(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("my--org") is not None

    def test_leading_hyphen(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("-org") is not None

    def test_trailing_hyphen(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("org-") is not None

    def test_uppercase_rejected(self):
        from cvcpkg.server.models import validate_org_slug

        assert validate_org_slug("MyOrg") is not None

    def test_special_chars_rejected(self):
        from cvcpkg.server.models import validate_org_slug

        for bad in ["my_org", "my.org", "my org", "org@name"]:
            assert validate_org_slug(bad) is not None, f"should reject: {bad}"

    def test_publish_rejects_invalid_org(self, server_env):
        """The publish endpoint rejects invalid org slugs."""
        client, _, pub_tok, _ = server_env
        archive = b"fake archive content"
        resp = client.post(
            "/v1/publish",
            params={
                "name": "test-pkg",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "INVALID--ORG",
            },
            files={"file": ("test-pkg.tar.zst", io.BytesIO(archive))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422


# ── PackageInfo.qualified_name ──────────────────────────────────


class TestPackageInfoQualifiedName:
    def test_base_package(self):
        from cvcpkg.server.models import PackageInfo

        p = PackageInfo(
            name="zlib",
            version="1.3.1",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="a" * 64,
            size_bytes=100,
            archive_url="/v1/download/zlib.tar.zst",
            published_at="2024-01-01T00:00:00+00:00",
        )
        assert p.qualified_name == "zlib"

    def test_org_package(self):
        from cvcpkg.server.models import PackageInfo

        p = PackageInfo(
            name="custom-lib",
            version="2.0.0",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="b" * 64,
            size_bytes=200,
            archive_url="/v1/download/custom-lib.tar.zst",
            published_at="2024-01-01T00:00:00+00:00",
            org="cvc-lab",
        )
        assert p.qualified_name == "cvc-lab/custom-lib"


# ── RSS Feed ────────────────────────────────────────────────────


class TestRSSFeed:
    def test_rss_empty_feed(self, server_env):
        """RSS feed returns valid XML with no items when no packages exist."""
        client, *_ = server_env
        resp = client.get("/v1/feed.xml")
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.headers["content-type"]
        assert "<?xml version" in resp.text
        assert "<rss" in resp.text
        assert "<channel>" in resp.text
        assert "<item>" not in resp.text

    def test_rss_with_packages(self, server_env):
        """RSS feed contains items after publishing packages."""
        client, _, pub_tok, _ = server_env
        # Publish two packages
        for name, version in [("zlib", "1.3.1"), ("boost", "1.86.0")]:
            client.post(
                "/v1/publish",
                params={
                    "name": name,
                    "version": version,
                    "platform": "linux",
                    "arch": "x86_64",
                },
                files={"file": (f"{name}.tar.zst", io.BytesIO(b"content"))},
                headers={"Authorization": f"Bearer {pub_tok}"},
            )
        resp = client.get("/v1/feed.xml")
        assert resp.status_code == 200
        assert "<item>" in resp.text
        assert "zlib 1.3.1" in resp.text
        assert "boost 1.86.0" in resp.text
        assert "<guid" in resp.text
        assert "<pubDate>" in resp.text

    def test_rss_limit_parameter(self, server_env):
        """RSS feed respects limit parameter."""
        client, _, pub_tok, _ = server_env
        for i in range(5):
            client.post(
                "/v1/publish",
                params={
                    "name": f"pkg{i}",
                    "version": "1.0",
                    "platform": "linux",
                    "arch": "x86_64",
                },
                files={"file": (f"pkg{i}.tar.zst", io.BytesIO(b"data"))},
                headers={"Authorization": f"Bearer {pub_tok}"},
            )
        resp = client.get("/v1/feed.xml?limit=2")
        assert resp.status_code == 200
        # Only 2 items should be in the feed
        assert resp.text.count("<item>") == 2

    def test_rss_valid_xml(self, server_env):
        """RSS feed is well-formed XML."""
        import xml.etree.ElementTree as ET

        client, _, pub_tok, _ = server_env
        client.post(
            "/v1/publish",
            params={
                "name": "xmltest",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
                "description": "Test <special> & chars",
            },
            files={"file": ("xmltest.tar.zst", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.get("/v1/feed.xml")
        assert resp.status_code == 200
        # Should parse without error (XML-safe escaping)
        root = ET.fromstring(resp.text)
        assert root.tag == "rss"
        items = root.findall(".//item")
        assert len(items) == 1


# ── Download Stats ──────────────────────────────────────────────


class TestDownloadStats:
    def test_download_stats_empty(self, server_env):
        """Download stats returns empty data when using YAML backend."""
        client, *_ = server_env
        resp = client.get("/v1/downloads/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["daily"] == []
        assert "config" in data
        assert "color" in data["config"]
        assert "height" in data["config"]

    def test_download_stats_with_name_filter(self, server_env):
        """Download stats accepts name parameter."""
        client, *_ = server_env
        resp = client.get("/v1/downloads/stats?name=zlib")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_download_stats_days_parameter(self, server_env):
        """Download stats respects days parameter."""
        client, *_ = server_env
        resp = client.get("/v1/downloads/stats?days=7")
        assert resp.status_code == 200

    def test_download_stats_config_values(self, server_env):
        """Download stats returns configuration values."""
        client, *_ = server_env
        resp = client.get("/v1/downloads/stats")
        data = resp.json()
        config = data["config"]
        assert isinstance(config["days"], int)
        assert isinstance(config["color"], str)
        assert isinstance(config["fill_color"], str)
        assert isinstance(config["height"], int)


# ── Download tracking ───────────────────────────────────────────


class TestDownloadTracking:
    def test_download_records_event(self, server_env):
        """Download endpoint still works (event recording is YAML-backend no-op)."""
        client, _, pub_tok, _ = server_env
        content = b"real archive bytes" * 10
        client.post(
            "/v1/publish",
            params={
                "name": "trackpkg",
                "version": "0.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("trackpkg.tar.zst", io.BytesIO(content))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        catalog = client.get("/v1/catalog").json()
        url = catalog["bundles"][0]["archive_url"]
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.content == content
