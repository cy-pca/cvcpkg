"""Integration tests for the cvcpkg-server REST API.

These tests exercise the full lifecycle — publish, catalog retrieval,
client-side install, list, verify, yank/unyank, delete, token
management, and audit trail — against a real FastAPI TestClient
backed by a temporary on-disk state directory.

Unlike the unit tests in test_server.py (which test individual
endpoints in isolation), these tests verify multi-step workflows
that cross multiple endpoints and match what a real CI pipeline
or developer would do.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from unittest import mock

import pytest
import yaml

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pydantic = pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole

# ── Module-wide env cleanup ─────────────────────────────────────
# These tests use in-process TestClient with file-based TokenStore.
# When running inside the Docker test container, CVCPKG_DATABASE_URL
# is set, which causes create_app() to use DbTokenStore instead —
# creating a mismatch.  Clear it for this entire module.


@pytest.fixture(autouse=True)
def _clear_database_url(monkeypatch):
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)


# ── Helpers ─────────────────────────────────────────────────────


def _make_tar_archive(files: dict[str, bytes]) -> bytes:
    """Build an in-memory .tar.gz archive from a {name: content} mapping."""
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


@pytest.fixture()
def env(tmp_path):
    """Set up a full server + client environment.

    Returns (client, admin_token, publisher_token, reader_token, tmp_path).
    """
    store = TokenStore(tmp_path)
    admin_token = store.create("admin", TokenRole.admin)
    pub_token = store.create("ci-publisher", TokenRole.publisher)
    reader_token = store.create("readonly", TokenRole.reader)

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, reader_token, tmp_path


def _publish(
    client: TestClient,
    token: str,
    name: str,
    version: str,
    *,
    platform: str = "linux",
    arch: str = "x86_64",
    build_type: str = "release",
    link: str = "shared",
    content: bytes | None = None,
) -> dict:
    """Publish a package and return the response JSON."""
    if content is None:
        content = _make_tar_archive(
            {
                f"lib/lib{name}.so": b"\x7fELF" + b"\x00" * 64,
                f"include/{name}.h": b"#pragma once\n",
                f"share/libcvc-deps/{name}/manifest.yaml": yaml.dump(
                    {
                        "schema_version": 3,
                        "bundle": {
                            "name": name,
                            "version": version,
                            "upstream_version": version.split("+")[0],
                            "cvc_revision": 1,
                            "platform": platform,
                            "arch": arch,
                            "build_type": build_type,
                            "link": link,
                        },
                        "contents": {"files": [f"lib/lib{name}.so", f"include/{name}.h"]},
                    }
                ).encode(),
            }
        )

    resp = client.post(
        "/v1/publish",
        params={
            "name": name,
            "version": version,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
        },
        files={"file": (f"{name}-{version}.tar.gz", io.BytesIO(content))},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"publish failed: {resp.text}"
    return resp.json()


# ── Full lifecycle test ─────────────────────────────────────────


class TestPublishAndInstallLifecycle:
    """End-to-end: publish packages → fetch catalog → install → verify."""

    def test_full_lifecycle(self, env):
        client, admin_tok, pub_tok, reader_tok, tmp_path = env

        # ── Step 1: Health check ────────────────────────────
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["packages_count"] == 0

        # ── Step 2: Publish multiple packages ───────────────
        _publish(client, pub_tok, "zlib", "1.3.1+cvc.1")
        _publish(client, pub_tok, "boost", "1.86.0+cvc.1")
        _publish(client, pub_tok, "yaml", "0.2.5+cvc.1")

        # Health should now show 3 packages
        resp = client.get("/healthz")
        assert resp.json()["packages_count"] == 3

        # ── Step 3: Fetch catalog ───────────────────────────
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        catalog = resp.json()
        assert catalog["revision"] == 3  # incremented per publish
        assert len(catalog["bundles"]) == 3
        names = {b["name"] for b in catalog["bundles"]}
        assert names == {"zlib", "boost", "yaml"}

        # All bundles have SHA-256 and download URLs
        for b in catalog["bundles"]:
            assert len(b["sha256"]) == 64
            assert b["archive_url"].startswith("/v1/download/")
            assert b["size_bytes"] > 0

        # ── Step 4: Download archives and verify integrity ──
        for b in catalog["bundles"]:
            resp = client.get(b["archive_url"])
            assert resp.status_code == 200
            assert _sha256(resp.content) == b["sha256"]
            assert len(resp.content) == b["size_bytes"]

        # ── Step 5: Filter packages ─────────────────────────
        resp = client.get("/v1/packages", params={"name": "zlib"})
        assert resp.json()["total"] == 1
        assert resp.json()["packages"][0]["name"] == "zlib"

        resp = client.get("/v1/packages/boost")
        assert resp.json()["total"] == 1

        # Non-existent component
        resp = client.get("/v1/packages/nonexistent")
        assert resp.json()["total"] == 0

        # ── Step 6: Yank a package ──────────────────────────
        resp = client.post(
            "/v1/packages/yaml/0.2.5+cvc.1/yank",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200

        # Default listing hides yanked packages
        resp = client.get("/v1/packages/yaml")
        assert resp.json()["total"] == 0

        # But visible with include_yanked=true
        resp = client.get("/v1/packages/yaml?include_yanked=true")
        assert resp.json()["packages"][0]["yanked"] is True

        # ── Step 7: Unyank (admin only) ─────────────────────
        resp = client.post(
            "/v1/packages/yaml/0.2.5+cvc.1/unyank",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        resp = client.get("/v1/packages/yaml")
        assert resp.json()["packages"][0]["yanked"] is False

        # ── Step 8: Delete a package (admin only) ───────────
        resp = client.delete(
            "/v1/packages/yaml/0.2.5+cvc.1",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        resp = client.get("/v1/catalog")
        assert len(resp.json()["bundles"]) == 2

        # ── Step 9: Audit trail ─────────────────────────────
        resp = client.get(
            "/v1/audit",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        audit = resp.json()
        actions = [e["action"] for e in audit["entries"]]
        # We should have: 3 publishes + 1 yank + 1 unyank + 1 delete
        assert actions.count("publish") == 3
        assert "yank" in actions
        assert "unyank" in actions
        assert "delete" in actions

        # Chain integrity should be intact
        resp = client.get(
            "/v1/audit/verify",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.json()["ok"] is True


# ── Client-side install against server catalog ──────────────────


class TestClientInstallFromServer:
    """Simulate `cvcpkg install` fetching from the server's catalog."""

    def test_install_from_server_catalog(self, env):
        """Publish, write a local catalog file from server, and run CLI install."""
        client, admin_tok, pub_tok, _, tmp_path = env

        # Publish two components
        archive1 = _make_tar_archive(
            {
                "lib/libzlib.so": b"\x7fELF" + b"\x00" * 64,
                "include/zlib.h": b"#pragma once\n",
            }
        )
        archive2 = _make_tar_archive(
            {
                "lib/libyaml.so": b"\x7fELF" + b"\x00" * 64,
                "include/yaml.h": b"#pragma once\n",
            }
        )
        pub1 = _publish(client, pub_tok, "zlib", "1.3.1+cvc.1", content=archive1)
        pub2 = _publish(client, pub_tok, "yaml", "0.2.5+cvc.1", content=archive2)

        # Download archives to a local cache (simulating what install does)
        for pub in [pub1, pub2]:
            resp = client.get(pub["archive_url"])
            assert resp.status_code == 200

        # Fetch the catalog from the server and write it as a local file
        # (the CLI can accept local catalog files via --catalog)
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        catalog_data = resp.json()

        # Rewrite archive_url to be full server URLs
        # (in a real setup, these would be absolute URLs)
        for b in catalog_data["bundles"]:
            b["archive_url"] = f"http://testserver{b['archive_url']}"

        catalog_file = tmp_path / "server-catalog.yaml"
        catalog_file.write_text(yaml.dump(catalog_data, default_flow_style=False))

        # Verify the catalog file can be loaded by cvcpkg
        from cvcpkg.catalog import catalog_entries, load_catalog_from_file

        cat = load_catalog_from_file(str(catalog_file))
        entries = catalog_entries(cat, platform="linux", arch="x86_64")
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"zlib", "yaml"}

        # Each entry has correct metadata
        for e in entries:
            assert e.sha256
            assert e.size_bytes > 0
            assert e.archive_url.startswith("http://testserver/v1/download/")

    def test_install_cli_with_local_server_catalog(self, env):
        """Full CLI install via a local catalog written from the server."""
        client, admin_tok, pub_tok, _, tmp_path = env

        # Publish zlib
        archive = _make_tar_archive(
            {
                "lib/libz.so": b"\x7fELF" + b"\x00" * 42,
                "include/zlib.h": b"header\n",
            }
        )
        _publish(client, pub_tok, "zlib", "1.3.1+cvc.1", content=archive)

        # Get catalog and save locally
        catalog = client.get("/v1/catalog").json()
        catalog_file = tmp_path / "catalog.yaml"
        catalog_file.write_text(yaml.dump(catalog, default_flow_style=False))

        # Run cvcpkg install with the local catalog
        from cvcpkg.cli import main

        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "zlib",
                    "--catalog",
                    str(catalog_file),
                    "--prefix",
                    str(prefix),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                ]
            )
        assert ret == 0


# ── Multi-platform publishing ───────────────────────────────────


class TestMultiPlatformPublish:
    """Publish the same component for multiple platforms."""

    def test_same_version_different_platforms(self, env):
        client, _, pub_tok, _, _ = env

        for plat in ("linux", "macos", "windows"):
            for arch in ("x86_64", "arm64"):
                _publish(
                    client,
                    pub_tok,
                    "zlib",
                    "1.3.1+cvc.1",
                    platform=plat,
                    arch=arch,
                )

        # Should have 6 entries
        resp = client.get("/v1/packages/zlib")
        assert resp.json()["total"] == 6

        # Filter by platform
        resp = client.get("/v1/packages", params={"name": "zlib", "platform": "linux"})
        assert resp.json()["total"] == 2  # x86_64 + arm64

    def test_same_version_different_configs(self, env):
        client, _, pub_tok, _, _ = env

        for config in ("release", "debug"):
            for link in ("shared", "static"):
                _publish(
                    client,
                    pub_tok,
                    "boost",
                    "1.86.0+cvc.1",
                    build_type=config,
                    link=link,
                )

        resp = client.get("/v1/packages/boost")
        assert resp.json()["total"] == 4


# ── RBAC enforcement ────────────────────────────────────────────


class TestRBACEnforcement:
    """Verify role-based access control across all endpoint groups."""

    def test_reader_cannot_publish(self, env):
        client, _, _, reader_tok, _ = env
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.gz", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {reader_tok}"},
        )
        assert resp.status_code == 403

    def test_reader_cannot_yank(self, env):
        client, _, pub_tok, reader_tok, _ = env
        _publish(client, pub_tok, "pkg", "1.0")
        resp = client.post(
            "/v1/packages/pkg/1.0/yank",
            headers={"Authorization": f"Bearer {reader_tok}"},
        )
        assert resp.status_code == 403

    def test_publisher_cannot_delete(self, env):
        client, _, pub_tok, _, _ = env
        _publish(client, pub_tok, "pkg", "1.0")
        resp = client.delete(
            "/v1/packages/pkg/1.0",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_publisher_cannot_manage_tokens(self, env):
        client, _, pub_tok, _, _ = env
        resp = client.post(
            "/v1/tokens",
            json={"name": "sneaky", "role": "admin"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_publisher_cannot_view_audit(self, env):
        client, _, pub_tok, _, _ = env
        resp = client.get("/v1/audit", headers={"Authorization": f"Bearer {pub_tok}"})
        assert resp.status_code == 403

    def test_no_auth_on_reads(self, env):
        """Read endpoints work without any auth by default."""
        client, _, pub_tok, _, _ = env
        _publish(client, pub_tok, "pub", "1.0")

        for url in ["/v1/catalog", "/v1/packages", "/v1/packages/pub"]:
            resp = client.get(url)
            assert resp.status_code == 200, f"GET {url} failed: {resp.status_code}"

    def test_expired_token_rejected(self, env):
        client, _, _, _, tmp_path = env
        store = TokenStore(tmp_path)
        expired = store.create("expired-bot", TokenRole.publisher, expires_in_days=-1)
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.gz", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401

    def test_revoked_token_rejected(self, env):
        client, admin_tok, _, _, tmp_path = env
        store = TokenStore(tmp_path)
        tok = store.create("soon-gone", TokenRole.publisher)
        store.revoke("soon-gone")
        resp = client.post(
            "/v1/publish",
            params={"name": "x", "version": "1.0"},
            files={"file": ("x.tar.gz", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 401


# ── Token lifecycle via API ─────────────────────────────────────


class TestTokenLifecycle:
    """Create → use → revoke → verify rejected."""

    def test_full_token_lifecycle(self, env):
        client, admin_tok, _, _, _ = env

        # Create a publisher token via API
        resp = client.post(
            "/v1/tokens",
            json={"name": "ci-bot", "role": "publisher", "expires_in_days": 30},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        new_token = resp.json()["token"]
        assert new_token.startswith("cvctok_")
        assert resp.json()["expires_at"] is not None

        # Use it to publish
        _publish(client, new_token, "pkg", "1.0")

        # List tokens — admin should see it
        resp = client.get("/v1/tokens", headers={"Authorization": f"Bearer {admin_tok}"})
        names = {t["name"] for t in resp.json()["tokens"]}
        assert "ci-bot" in names

        # Revoke it
        resp = client.delete(
            "/v1/tokens/ci-bot",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200

        # Now it's rejected
        resp = client.post(
            "/v1/publish",
            params={"name": "pkg2", "version": "1.0"},
            files={"file": ("p.tar.gz", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status_code == 401

        # Audit recorded both token_create and token_revoke
        resp = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_tok}"})
        actions = [e["action"] for e in resp.json()["entries"]]
        assert "token_create" in actions
        assert "token_revoke" in actions


# ── Audit trail integrity ───────────────────────────────────────


class TestAuditIntegrity:
    """Verify the audit chain stays intact across many operations."""

    def test_chain_survives_heavy_usage(self, env):
        client, admin_tok, pub_tok, _, _ = env

        # Rapid-fire operations
        for i in range(10):
            _publish(client, pub_tok, f"pkg{i}", "1.0")

        # Yank half
        for i in range(0, 10, 2):
            client.post(
                f"/v1/packages/pkg{i}/1.0/yank",
                headers={"Authorization": f"Bearer {pub_tok}"},
            )

        # Delete two
        for i in [1, 3]:
            client.delete(
                f"/v1/packages/pkg{i}/1.0",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )

        # Create and revoke a token
        resp = client.post(
            "/v1/tokens",
            json={"name": "temp", "role": "reader"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        client.delete("/v1/tokens/temp", headers={"Authorization": f"Bearer {admin_tok}"})

        # Verify chain
        resp = client.get("/v1/audit/verify", headers={"Authorization": f"Bearer {admin_tok}"})
        data = resp.json()
        assert data["ok"] is True

        # Full log should have all events
        resp = client.get(
            "/v1/audit",
            params={"limit": 1000},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        total = resp.json()["total"]
        # 10 publish + 5 yank + 2 delete + 1 token_create + 1 token_revoke = 19
        assert total == 19

    def test_audit_filter_by_target(self, env):
        client, admin_tok, pub_tok, _, _ = env

        _publish(client, pub_tok, "libx", "1.0")
        _publish(client, pub_tok, "liby", "2.0")
        client.post(
            "/v1/packages/libx/1.0/yank",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )

        resp = client.get(
            "/v1/audit",
            params={"target": "libx==1.0"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        entries = resp.json()["entries"]
        assert all(e["target"] == "libx==1.0" for e in entries)
        assert len(entries) == 2  # publish + yank


# ── State persistence ──────────────────────────────────────────


class TestStatePersistence:
    """Verify server state survives restart (new app instance)."""

    def test_index_persists_across_restarts(self, tmp_path):
        store = TokenStore(tmp_path)
        pub_tok = store.create("bot", TokenRole.publisher)

        # First app instance
        app1 = create_app(state_dir=tmp_path)
        with TestClient(app1) as c1:
            _publish(c1, pub_tok, "zlib", "1.0")
            _publish(c1, pub_tok, "boost", "1.0")

        # Second app instance (fresh process simulated)
        app2 = create_app(state_dir=tmp_path)
        with TestClient(app2) as c2:
            resp = c2.get("/v1/catalog")
            assert len(resp.json()["bundles"]) == 2

            # Can still download
            url = resp.json()["bundles"][0]["archive_url"]
            resp = c2.get(url)
            assert resp.status_code == 200

    def test_audit_persists_across_restarts(self, tmp_path):
        store = TokenStore(tmp_path)
        admin_tok = store.create("admin", TokenRole.admin)
        pub_tok = store.create("bot", TokenRole.publisher)

        app1 = create_app(state_dir=tmp_path)
        with TestClient(app1) as c1:
            _publish(c1, pub_tok, "pkg", "1.0")

        app2 = create_app(state_dir=tmp_path)
        with TestClient(app2) as c2:
            resp = c2.get("/v1/audit", headers={"Authorization": f"Bearer {admin_tok}"})
            assert resp.json()["total"] >= 1

            resp = c2.get("/v1/audit/verify", headers={"Authorization": f"Bearer {admin_tok}"})
            assert resp.json()["ok"] is True


# ── Duplicate and conflict handling ─────────────────────────────


class TestConflictHandling:
    """Verify the server rejects exact duplicates but allows revisions."""

    def test_exact_duplicate_rejected(self, env):
        client, _, pub_tok, _, _ = env
        _publish(client, pub_tok, "zlib", "1.0")
        resp = client.post(
            "/v1/publish",
            params={
                "name": "zlib",
                "version": "1.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("z.tar.gz", io.BytesIO(b"data"))},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 409

    def test_different_revision_allowed(self, env):
        client, _, pub_tok, _, _ = env
        _publish(client, pub_tok, "zlib", "1.3.1+cvc.1")
        _publish(client, pub_tok, "zlib", "1.3.1+cvc.2")  # new revision

        resp = client.get("/v1/packages/zlib")
        assert resp.json()["total"] == 2

    def test_republish_after_delete_allowed(self, env):
        client, admin_tok, pub_tok, _, _ = env
        _publish(client, pub_tok, "zlib", "1.0")
        client.delete(
            "/v1/packages/zlib/1.0",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        # Should be able to republish
        _publish(client, pub_tok, "zlib", "1.0")
        resp = client.get("/v1/packages/zlib")
        assert resp.json()["total"] == 1


# ── CLI cvcpkg-server token commands ────────────────────────────


class TestServerCLI:
    """Test the cvcpkg-server CLI commands for token/audit management."""

    def test_token_create_and_list(self, tmp_path, capsys):
        from cvcpkg.server.cli import server_cli

        # Create a token
        server_cli(
            [
                "token",
                "create",
                "--name",
                "test-tok",
                "--role",
                "publisher",
                "--state-dir",
                str(tmp_path),
            ],
            standalone_mode=False,
        )

        captured = capsys.readouterr()
        assert "cvctok_" in captured.out
        assert "test-tok" in captured.out

        # List tokens
        server_cli(
            ["token", "list", "--state-dir", str(tmp_path)],
            standalone_mode=False,
        )
        captured = capsys.readouterr()
        assert "test-tok" in captured.out
        assert "publisher" in captured.out

    def test_token_revoke(self, tmp_path, capsys):
        from cvcpkg.server.cli import server_cli

        server_cli(
            [
                "token",
                "create",
                "--name",
                "revokeme",
                "--role",
                "reader",
                "--state-dir",
                str(tmp_path),
            ],
            standalone_mode=False,
        )
        capsys.readouterr()  # clear

        server_cli(
            ["token", "revoke", "--name", "revokeme", "--state-dir", str(tmp_path)],
            standalone_mode=False,
        )
        captured = capsys.readouterr()
        assert "revoked" in captured.out.lower()

    def test_audit_log_and_verify(self, tmp_path, capsys):
        from cvcpkg.server.audit import AuditLog
        from cvcpkg.server.cli import server_cli
        from cvcpkg.server.models import AuditAction

        # Seed some audit entries
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, "bot", "zlib==1.0")
        log.record(AuditAction.yank, "admin", "zlib==1.0")

        server_cli(
            ["audit", "log", "--state-dir", str(tmp_path)],
            standalone_mode=False,
        )
        captured = capsys.readouterr()
        assert "publish" in captured.out
        assert "yank" in captured.out

        server_cli(
            ["audit", "verify", "--state-dir", str(tmp_path)],
            standalone_mode=False,
        )
        captured = capsys.readouterr()
        assert "OK" in captured.out
