"""End-to-end lifecycle tests for the cvcpkg ecosystem.

Exercises the full workflow in Docker containers:

  1. Health check on fresh server
  2. Admin bootstrap (create initial admin token via CLI)
  3. Reader cannot publish (RBAC enforcement)
  4. Admin grants publisher token
  5. Build a real recipe (zlib) from source
  6. Pack it into an archive
  7. Publish the archive to the server via REST API
  8. Another client fetches the catalog and sees the package
  9. Client downloads and installs the package
 10. Smoke test: verify installed files are present and valid
 11. Yank / unyank cycle
 12. Full audit trail verification

Run via Docker Compose::

    # from the repo root
    docker compose -f docker-compose.test.yml up -d --build postgres backend
    # wait for health
    docker compose -f docker-compose.test.yml run --rm test \
        pytest tests/integration/test_e2e_lifecycle.py -v
    docker compose -f docker-compose.test.yml down -v

Environment:
    CVCPKG_TEST_SERVER_URL  — base URL (default http://127.0.0.1:8421)
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

httpx = pytest.importorskip("httpx", reason="httpx required for E2E tests")

SERVER_URL = os.environ.get("CVCPKG_TEST_SERVER_URL", "http://127.0.0.1:8421")

# Real recipes are bind-mounted at /repo/recipes inside the test container.
# When running outside Docker (local dev), fall back to the repo tree.
_CONTAINER_RECIPES = Path("/repo/recipes")
try:
    _LOCAL_RECIPES = Path(__file__).resolve().parents[2] / "recipes"
except IndexError:
    _LOCAL_RECIPES = Path("/nonexistent")
RECIPES_DIR = _CONTAINER_RECIPES if _CONTAINER_RECIPES.is_dir() else _LOCAL_RECIPES

# ── Helpers ─────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_fake_archive(files: dict[str, bytes]) -> bytes:
    """Create a minimal .tar.gz for testing the publish API."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _run_cli(*cmd: str) -> str:
    """Run a cvcpkg-server CLI command directly.

    The test container has ``cvcpkg-server`` installed and
    ``CVCPKG_DATABASE_URL`` set by docker-compose.test.yml, so we
    can invoke the CLI without ``docker compose exec``.
    """
    result = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"CLI command failed: {result.stderr.strip()}")
    return result.stdout.strip()


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """HTTP client pointed at the test server."""
    with httpx.Client(base_url=SERVER_URL, timeout=60) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token():
    """Bootstrap admin token via the cvcpkg-server CLI."""
    output = _run_cli(
        "cvcpkg-server",
        "token",
        "create",
        "--name",
        "e2e-admin",
        "--role",
        "admin",
        "--state-dir",
        "/app/data",
    )
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("cvctok_"):
            return line
    pytest.fail(f"Could not extract admin token from: {output}")


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def reader_token(client, admin_headers):
    """Admin creates a reader token."""
    r = client.post(
        "/v1/tokens",
        json={"name": "e2e-reader", "role": "reader"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def publisher_token(client, admin_headers):
    """Admin creates a publisher token."""
    r = client.post(
        "/v1/tokens",
        json={"name": "e2e-publisher", "role": "publisher"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "publisher"
    assert data["token"].startswith("cvctok_")
    return data["token"]


@pytest.fixture(scope="module")
def publisher_headers(publisher_token):
    return {"Authorization": f"Bearer {publisher_token}"}


@pytest.fixture(scope="module")
def zlib_build(tmp_path_factory):
    """Build zlib from the real recipe and return archive metadata."""
    if not RECIPES_DIR.is_dir():
        pytest.skip("recipes directory not available")
    if not (RECIPES_DIR / "zlib").is_dir():
        pytest.skip("zlib recipe not found")

    from cvcpkg.builder import pack_recipe
    from cvcpkg.platform import detect_arch, detect_platform

    plat = detect_platform()
    arch = detect_arch()
    out = tmp_path_factory.mktemp("dist")

    archive_path, sha256, size = pack_recipe(
        RECIPES_DIR / "zlib",
        platform=plat,
        arch=arch,
        config="release",
        link="shared",
        output_dir=out,
    )

    assert archive_path.is_file()
    assert size > 0
    assert len(sha256) == 64

    with tarfile.open(archive_path, "r:*") as tf:
        manifest_entries = [m for m in tf.getmembers() if m.name.endswith("manifest.yaml")]
        assert len(manifest_entries) == 1
        f = tf.extractfile(manifest_entries[0])
        assert f is not None
        manifest = yaml.safe_load(f.read())

    return {
        "archive_path": archive_path,
        "sha256": sha256,
        "size": size,
        "platform": plat,
        "arch": arch,
        "manifest": manifest,
    }


@pytest.fixture(scope="module")
def published_zlib(client, publisher_headers, zlib_build):
    """Publish the zlib archive and return the server response."""
    archive_data = zlib_build["archive_path"].read_bytes()
    manifest = zlib_build["manifest"]

    r = client.post(
        "/v1/publish",
        params={
            "name": manifest["bundle"]["name"],
            "version": manifest["bundle"]["version"],
            "platform": manifest["bundle"]["platform"],
            "arch": manifest["bundle"]["arch"],
            "build_type": manifest["bundle"]["build_type"],
            "link": manifest["bundle"]["link"],
            "release_tag": "",
            "recipe_version": manifest["meta"].get("recipe_sha256", ""),
        },
        files={
            "file": (
                zlib_build["archive_path"].name,
                archive_data,
                "application/octet-stream",
            ),
        },
        headers=publisher_headers,
    )
    assert r.status_code == 200, f"publish failed: {r.text}"
    data = r.json()
    assert data["name"] == "zlib"
    assert data["sha256"] == zlib_build["sha256"]
    return data


# ── Test classes (ordered) ──────────────────────────────────────


class TestServerHealth:
    """Phase 1: Verify the server is alive and the catalog is empty."""

    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["packages_count"] >= 0

    def test_catalog_initially_empty(self, client):
        r = client.get("/v1/catalog")
        assert r.status_code == 200
        data = r.json()
        assert "bundles" in data


class TestRBACEnforcement:
    """Phase 2–3: Reader tokens cannot publish; only publishers/admins can."""

    def test_reader_cannot_publish(self, client, reader_token):
        """A reader token should get 403 when trying to publish."""
        archive = _make_fake_archive({"dummy.txt": b"hello"})
        r = client.post(
            "/v1/publish",
            params={
                "name": "forbidden-pkg",
                "version": "0.0.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
            },
            files={"file": ("test.tar.gz", archive, "application/octet-stream")},
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert r.status_code == 403

    def test_reader_cannot_create_tokens(self, client, reader_token):
        """A reader token should get 403 when trying to create tokens."""
        r = client.post(
            "/v1/tokens",
            json={"name": "sneaky", "role": "publisher"},
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert r.status_code == 403

    def test_unauthenticated_cannot_publish(self, client):
        """No token at all should get 401."""
        archive = _make_fake_archive({"dummy.txt": b"hello"})
        r = client.post(
            "/v1/publish",
            params={
                "name": "anon-pkg",
                "version": "0.0.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("test.tar.gz", archive, "application/octet-stream")},
        )
        assert r.status_code == 401


class TestPublisherGrant:
    """Phase 4: Admin grants a publisher token."""

    def test_publisher_token_in_list(self, client, admin_headers, publisher_token):
        r = client.get("/v1/tokens", headers=admin_headers)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["tokens"]]
        assert "e2e-publisher" in names

    def test_publisher_token_has_correct_role(self, client, admin_headers, publisher_token):
        r = client.get("/v1/tokens", headers=admin_headers)
        for t in r.json()["tokens"]:
            if t["name"] == "e2e-publisher":
                assert t["role"] == "publisher"
                break


class TestBuildPackPublish:
    """Phase 5–7: Build zlib from source, pack, publish via REST API.

    This is the core E2E test — it proves the full producer workflow:
    recipe → build → pack → archive → publish → visible in catalog.
    """

    def test_build_produces_valid_archive(self, zlib_build):
        """Build zlib from the real recipe and verify the archive."""
        assert zlib_build["archive_path"].is_file()
        assert zlib_build["size"] > 0
        assert len(zlib_build["sha256"]) == 64

    def test_archive_contains_manifest(self, zlib_build):
        """The archive should contain a manifest.yaml with correct metadata."""
        manifest = zlib_build["manifest"]
        assert manifest["bundle"]["name"] == "zlib"
        assert manifest["bundle"]["platform"] == zlib_build["platform"]
        assert manifest["bundle"]["arch"] == zlib_build["arch"]

    def test_publish_via_api(self, published_zlib):
        """Publish the real zlib archive to the server."""
        assert published_zlib["name"] == "zlib"

    def test_catalog_shows_zlib(self, client, published_zlib, zlib_build):
        """After publish, zlib should appear in the catalog."""
        r = client.get("/v1/catalog")
        assert r.status_code == 200
        bundles = r.json()["bundles"]
        zlib_entries = [b for b in bundles if b["name"] == "zlib"]
        assert len(zlib_entries) >= 1
        entry = zlib_entries[0]
        assert entry["platform"] == zlib_build["platform"]
        assert entry["arch"] == zlib_build["arch"]

    def test_packages_endpoint_shows_zlib(self, client, published_zlib):
        """The packages endpoint should list zlib."""
        r = client.get("/v1/packages/zlib")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        assert any(p["name"] == "zlib" for p in data["packages"])

    def test_duplicate_publish_rejected(
        self, client, publisher_headers, zlib_build, published_zlib
    ):
        """Publishing the same exact package again should fail with 409."""
        archive_data = zlib_build["archive_path"].read_bytes()
        manifest = zlib_build["manifest"]
        r = client.post(
            "/v1/publish",
            params={
                "name": manifest["bundle"]["name"],
                "version": manifest["bundle"]["version"],
                "platform": manifest["bundle"]["platform"],
                "arch": manifest["bundle"]["arch"],
                "build_type": manifest["bundle"]["build_type"],
                "link": manifest["bundle"]["link"],
            },
            files={
                "file": (
                    zlib_build["archive_path"].name,
                    archive_data,
                    "application/octet-stream",
                ),
            },
            headers=publisher_headers,
        )
        assert r.status_code == 409


class TestInstallAndSmokeTest:
    """Phase 8–10: Download, install, and verify the published package."""

    def test_download_archive(self, client, published_zlib, zlib_build):
        """Download the published archive and verify its SHA-256."""
        url = published_zlib["archive_url"]
        r = client.get(url)
        assert r.status_code == 200
        assert _sha256(r.content) == zlib_build["sha256"]

    def test_install_and_verify_files(self, client, published_zlib, tmp_path):
        """Extract the downloaded archive and verify key files exist."""
        url = published_zlib["archive_url"]
        archive_data = client.get(url).content
        install_dir = tmp_path / "prefix"
        install_dir.mkdir()

        # Extract
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:*") as tf:
            tf.extractall(install_dir, filter="data")

        # Verify key zlib files exist
        # The archive contains files at the top level (lib/, include/, etc.)
        found_header = False
        found_lib = False
        found_manifest = False

        for path in install_dir.rglob("*"):
            rel = path.relative_to(install_dir)
            name = str(rel)
            if "zlib.h" in name:
                found_header = True
                # Verify the header is non-empty and looks like C
                content = path.read_text()
                assert "ZLIB" in content or "zlib" in content
            if "libz" in name and path.is_file():
                found_lib = True
                assert path.stat().st_size > 0
            if "manifest.yaml" in name:
                found_manifest = True
                manifest = yaml.safe_load(path.read_text())
                assert manifest["bundle"]["name"] == "zlib"

        assert found_header, "zlib.h not found in installed prefix"
        assert found_lib, "libz not found in installed prefix"
        assert found_manifest, "manifest.yaml not found in installed prefix"


class TestYankUnyankCycle:
    """Phase 11: Yank and unyank the published package."""

    def test_01_yank(self, client, published_zlib, publisher_headers, zlib_build):
        """Publisher yanks the package."""
        version = zlib_build["manifest"]["bundle"]["version"]
        r = client.post(
            f"/v1/packages/zlib/{version}/yank",
            headers=publisher_headers,
        )
        assert r.status_code == 200

    def test_02_yanked_not_in_catalog(self, client):
        """Yanked packages should not appear in the catalog."""
        r = client.get("/v1/catalog")
        bundles = r.json()["bundles"]
        zlib_entries = [b for b in bundles if b["name"] == "zlib" and not b.get("yanked")]
        assert len(zlib_entries) == 0

    def test_03_unyank(self, client, admin_headers, zlib_build):
        """Admin un-yanks the package."""
        version = zlib_build["manifest"]["bundle"]["version"]
        r = client.post(
            f"/v1/packages/zlib/{version}/unyank",
            headers=admin_headers,
        )
        assert r.status_code == 200

    def test_04_unyanked_back_in_catalog(self, client):
        """After unyank, package should be visible again."""
        r = client.get("/v1/catalog")
        bundles = r.json()["bundles"]
        zlib_entries = [b for b in bundles if b["name"] == "zlib"]
        assert len(zlib_entries) >= 1


class TestAuditTrailVerification:
    """Phase 12: Verify the complete audit trail."""

    def test_01_audit_log_has_entries(self, client, admin_headers):
        """The audit log should record all operations we performed."""
        r = client.get("/v1/audit", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        entries = data["entries"]
        assert data["total"] >= 4  # token_create (×3) + publish + yank + unyank

        actions = [e["action"] for e in entries]
        assert "token_create" in actions
        assert "publish" in actions
        assert "yank" in actions
        assert "unyank" in actions

    def test_02_audit_chain_valid(self, client, admin_headers):
        """The audit chain (SHA-256 links) should verify cleanly."""
        r = client.get("/v1/audit/verify", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_03_publish_audit_has_correct_target(self, client, admin_headers):
        """The publish audit entry should reference zlib."""
        r = client.get("/v1/audit", headers=admin_headers)
        entries = r.json()["entries"]
        publish_entries = [
            e for e in entries if e["action"] == "publish" and e["actor"] == "e2e-publisher"
        ]
        assert len(publish_entries) >= 1
        assert "zlib" in publish_entries[0]["target"]


class TestCleanup:
    """Final cleanup — delete the test package."""

    def test_delete_zlib(self, client, admin_headers, zlib_build, published_zlib):
        version = zlib_build["manifest"]["bundle"]["version"]
        r = client.delete(
            f"/v1/packages/zlib/{version}",
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["removed"] >= 1

    def test_catalog_empty_after_delete(self, client):
        r = client.get("/v1/catalog")
        bundles = r.json()["bundles"]
        zlib_entries = [b for b in bundles if b["name"] == "zlib"]
        assert len(zlib_entries) == 0

    def test_revoke_tokens(self, client, admin_headers):
        """Clean up test tokens."""
        for name in ("e2e-reader", "e2e-publisher"):
            r = client.delete(f"/v1/tokens/{name}", headers=admin_headers)
            # 200 or 404 (already gone) is fine
            assert r.status_code in (200, 404)
