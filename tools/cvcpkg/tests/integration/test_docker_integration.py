"""Docker integration tests for cvcpkg-server with PostgreSQL.

These tests run against a live cvcpkg-server instance backed by
PostgreSQL.  They exercise the full lifecycle: token creation,
package publish, catalog retrieval, download, yank/unyank, delete,
and audit trail verification.

Run locally with Docker Compose::

    cd tools/cvcpkg
    docker compose -f docker-compose.test.yml up -d postgres backend
    # Wait for health
    until curl -sf http://127.0.0.1:8421/healthz; do sleep 2; done
    CVCPKG_TEST_SERVER_URL=http://127.0.0.1:8421 pytest tests/integration/test_docker_integration.py -v
    docker compose -f docker-compose.test.yml down -v

Or run everything via the test service::

    docker compose -f docker-compose.test.yml run --rm test
    docker compose -f docker-compose.test.yml down -v

Environment variables:
    CVCPKG_TEST_SERVER_URL  — base URL of the running server
                              (default: http://127.0.0.1:8421)
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import time

import pytest

httpx = pytest.importorskip("httpx", reason="httpx not installed")

SERVER_URL = os.environ.get("CVCPKG_TEST_SERVER_URL", "http://127.0.0.1:8421")

# ── Helpers ─────────────────────────────────────────────────────


def _make_tar_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """HTTP client pointed at the test server."""
    with httpx.Client(base_url=SERVER_URL, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def bootstrap_admin_token():
    """Create a bootstrap admin token directly via the DB.

    The server has no tokens at first boot. For Docker integration
    tests we create one by exec'ing into the backend container or
    directly via the CLI. This fixture uses the CLI approach which
    writes to the shared state-dir volume.
    """
    import subprocess

    result = subprocess.run(
        [
            "docker", "compose", "-f", "docker-compose.test.yml",
            "exec", "-T", "backend",
            "cvcpkg-server", "token", "create",
            "--name", "test-admin",
            "--role", "admin",
            "--state-dir", "/app/data",
        ],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)) or ".",
    )
    if result.returncode != 0:
        pytest.skip(f"Cannot create bootstrap token: {result.stderr}")
    # Extract token from output like "  cvctok_..."
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("cvctok_"):
            return line
    pytest.fail(f"Could not parse token from output: {result.stdout}")


@pytest.fixture(scope="module")
def admin_headers(bootstrap_admin_token):
    return {"Authorization": f"Bearer {bootstrap_admin_token}"}


# ── Tests ───────────────────────────────────────────────────────


class TestHealthAndCatalog:
    """Basic health and catalog endpoints."""

    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert data["packages_count"] >= 0

    def test_catalog_empty(self, client):
        r = client.get("/v1/catalog")
        assert r.status_code == 200
        data = r.json()
        assert "bundles" in data
        assert "revision" in data

    def test_packages_empty(self, client):
        r = client.get("/v1/packages")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 0


class TestTokenManagement:
    """Token CRUD via the API."""

    def test_create_publisher_token(self, client, admin_headers):
        r = client.post(
            "/v1/tokens",
            json={"name": "ci-publisher", "role": "publisher"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "ci-publisher"
        assert data["role"] == "publisher"
        assert data["token"].startswith("cvctok_")

    def test_list_tokens(self, client, admin_headers):
        r = client.get("/v1/tokens", headers=admin_headers)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["tokens"]]
        assert "test-admin" in names

    def test_unauthenticated_token_list_fails(self, client):
        r = client.get("/v1/tokens")
        assert r.status_code == 401


class TestPublishAndInstallLifecycle:
    """End-to-end publish → catalog → download → yank → delete."""

    @pytest.fixture(autouse=True, scope="class")
    def publisher_token(self, client, admin_headers):
        """Create a publisher token for this test class."""
        r = client.post(
            "/v1/tokens",
            json={"name": "lifecycle-publisher", "role": "publisher"},
            headers=admin_headers,
        )
        if r.status_code == 200:
            TestPublishAndInstallLifecycle._pub_token = r.json()["token"]
        else:
            # Token might already exist from a previous run
            r2 = client.get("/v1/tokens", headers=admin_headers)
            for t in r2.json().get("tokens", []):
                if t["name"] == "lifecycle-publisher":
                    pytest.skip("lifecycle-publisher already exists, cannot get raw token")
            pytest.fail(f"Cannot create publisher token: {r.text}")

    @pytest.fixture()
    def pub_headers(self):
        return {"Authorization": f"Bearer {self._pub_token}"}

    def test_01_publish_package(self, client, pub_headers):
        archive = _make_tar_archive({
            "lib/libzlib.so": b"fake-zlib-library-content",
            "include/zlib.h": b"/* zlib header */",
        })
        r = client.post(
            "/v1/publish",
            params={
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
            },
            files={"file": ("zlib.tar.gz", archive, "application/octet-stream")},
            headers=pub_headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "zlib"
        assert data["sha256"] == _sha256(archive)
        TestPublishAndInstallLifecycle._archive_url = data["archive_url"]
        TestPublishAndInstallLifecycle._archive_sha = data["sha256"]

    def test_02_catalog_shows_package(self, client):
        r = client.get("/v1/catalog")
        assert r.status_code == 200
        bundles = r.json()["bundles"]
        zlib_bundles = [b for b in bundles if b["name"] == "zlib"]
        assert len(zlib_bundles) >= 1
        assert zlib_bundles[0]["version"] == "1.3.1+cvc.1"

    def test_03_packages_endpoint(self, client):
        r = client.get("/v1/packages/zlib")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        assert any(p["name"] == "zlib" for p in data["packages"])

    def test_04_download_archive(self, client):
        r = client.get(self._archive_url)
        assert r.status_code == 200
        assert _sha256(r.content) == self._archive_sha

    def test_05_duplicate_publish_rejected(self, client, pub_headers):
        archive = _make_tar_archive({"dummy": b"x"})
        r = client.post(
            "/v1/publish",
            params={
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
            },
            files={"file": ("zlib.tar.gz", archive, "application/octet-stream")},
            headers=pub_headers,
        )
        assert r.status_code == 409

    def test_06_yank_package(self, client, pub_headers):
        r = client.post(
            "/v1/packages/zlib/1.3.1+cvc.1/yank",
            headers=pub_headers,
        )
        assert r.status_code == 200

        # Yanked packages should NOT appear in catalog
        r2 = client.get("/v1/catalog")
        zlib_bundles = [b for b in r2.json()["bundles"] if b["name"] == "zlib"]
        assert all(b.get("yanked", False) is False for b in zlib_bundles) or len(zlib_bundles) == 0

    def test_07_unyank_package(self, client, admin_headers):
        r = client.post(
            "/v1/packages/zlib/1.3.1+cvc.1/unyank",
            headers=admin_headers,
        )
        assert r.status_code == 200

    def test_08_delete_package(self, client, admin_headers):
        r = client.delete(
            "/v1/packages/zlib/1.3.1+cvc.1",
            headers=admin_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["removed"] >= 1


class TestAuditTrail:
    """Audit log endpoints."""

    def test_audit_log(self, client, admin_headers):
        r = client.get("/v1/audit", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert "entries" in data
        assert "total" in data

    def test_audit_verify_chain(self, client, admin_headers):
        r = client.get("/v1/audit/verify", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_audit_unauthenticated_fails(self, client):
        r = client.get("/v1/audit")
        assert r.status_code == 401


class TestMultiPlatformPublish:
    """Publish the same component for multiple platforms."""

    @pytest.fixture(autouse=True, scope="class")
    def multi_publisher_token(self, client, admin_headers):
        r = client.post(
            "/v1/tokens",
            json={"name": "multi-plat-publisher", "role": "publisher"},
            headers=admin_headers,
        )
        if r.status_code == 200:
            TestMultiPlatformPublish._token = r.json()["token"]
        else:
            pytest.skip("Cannot create token for multi-platform test")

    @pytest.fixture()
    def headers(self):
        return {"Authorization": f"Bearer {self._token}"}

    def test_publish_linux_and_macos(self, client, headers):
        for plat in ("linux", "macos"):
            archive = _make_tar_archive({f"lib/libboost.{plat}": b"fake"})
            r = client.post(
                "/v1/publish",
                params={
                    "name": "boost",
                    "version": "1.85.0+cvc.1",
                    "platform": plat,
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                },
                files={"file": ("boost.tar.gz", archive, "application/octet-stream")},
                headers=headers,
            )
            assert r.status_code == 200, f"publish failed for {plat}: {r.text}"

    def test_filter_by_platform(self, client):
        r = client.get("/v1/packages", params={"platform": "linux"})
        assert r.status_code == 200
        for p in r.json()["packages"]:
            assert p["platform"] == "linux"

    def test_cleanup(self, client, admin_headers):
        for plat in ("linux", "macos"):
            client.delete(
                "/v1/packages/boost/1.85.0+cvc.1",
                headers=admin_headers,
            )
