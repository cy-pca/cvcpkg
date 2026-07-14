"""Tests for chunked upload endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and publisher tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        reader_raw = await store.create("test-reader", TokenRole.reader)
        await dispose_engine()
        return admin_raw, pub_raw, reader_raw

    admin_token, pub_token, reader_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, reader_token, tmp_path


# ── Chunked upload tests ───────────────────────────────────────


class TestChunkedUpload:
    """Tests for the chunked upload flow: init → chunk → complete."""

    def _admin_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_init_upload(self, db_server_env):
        """POST /v1/upload/init creates a session and returns upload_id."""
        client, _, pub_token, _, _ = db_server_env
        resp = client.post(
            "/v1/upload/init",
            params={
                "name": "testpkg",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "upload_id" in data
        assert data["chunk_size"] > 0
        assert data["max_size"] > 0

    def test_init_requires_auth(self, db_server_env):
        client, _, _, _, _ = db_server_env
        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0"},
        )
        assert resp.status_code == 401

    def test_init_reader_forbidden(self, db_server_env):
        client, _, _, reader_token, _ = db_server_env
        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0"},
            headers=self._admin_headers(reader_token),
        )
        assert resp.status_code == 403

    def test_upload_status(self, db_server_env):
        """GET /v1/upload/{id} returns session status."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.get(f"/v1/upload/{upload_id}", headers=hdrs)
        assert resp.status_code == 200
        data = resp.json()
        assert data["upload_id"] == upload_id
        assert data["bytes_received"] == 0
        assert data["name"] == "pkg"

    def test_upload_status_not_found(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.get(
            "/v1/upload/nonexistent",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 404

    def test_upload_chunk(self, db_server_env):
        """PATCH /v1/upload/{id} accepts chunk data."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        chunk = b"A" * 1024
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=chunk,
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["bytes_received"] == 1024

    def test_upload_chunk_with_content_range(self, db_server_env):
        """PATCH with Content-Range header verifies offset."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        chunk = b"B" * 512
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=chunk,
            headers={
                **hdrs,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes 0-511/{len(chunk)}",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["bytes_received"] == 512

    def test_upload_chunk_wrong_offset(self, db_server_env):
        """PATCH with wrong Content-Range start returns 409."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=b"data",
            headers={
                **hdrs,
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 100-103/200",
            },
        )
        assert resp.status_code == 409

    def test_upload_chunk_wrong_actor(self, db_server_env):
        """Different actor cannot append to another's upload session."""
        client, admin_token, pub_token, _, _ = db_server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=self._admin_headers(pub_token),
        )
        upload_id = resp.json()["upload_id"]

        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=b"data",
            headers={
                **self._admin_headers(admin_token),
                "Content-Type": "application/octet-stream",
            },
        )
        assert resp.status_code == 403

    def test_complete_upload(self, db_server_env):
        """Full flow: init → chunk → complete."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        # Init
        resp = client.post(
            "/v1/upload/init",
            params={"name": "mypkg", "version": "2.0.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        assert resp.status_code == 201
        upload_id = resp.json()["upload_id"]

        # Upload a chunk
        content = b"fake archive content for chunked upload test"
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=content,
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200

        # Complete
        sha256 = hashlib.sha256(content).hexdigest()
        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": sha256},
            headers=hdrs,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "mypkg"
        assert data["version"] == "2.0.0"
        assert data["sha256"] == sha256
        assert data["archive_url"].startswith("/v1/download/")

    def test_complete_sha256_mismatch(self, db_server_env):
        """Complete with wrong SHA-256 returns 422."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        client.patch(
            f"/v1/upload/{upload_id}",
            content=b"data",
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )

        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={
                "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
            },
            headers=hdrs,
        )
        assert resp.status_code == 422

    def test_complete_no_data(self, db_server_env):
        """Complete without uploading data returns 400."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.post(f"/v1/upload/{upload_id}/complete", headers=hdrs)
        assert resp.status_code == 400

    def test_complete_not_found(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.post(
            "/v1/upload/nonexistent/complete",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 404

    def test_complete_wrong_actor(self, db_server_env):
        """Different actor cannot complete another's upload."""
        client, admin_token, pub_token, _, _ = db_server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=self._admin_headers(pub_token),
        )
        upload_id = resp.json()["upload_id"]

        client.patch(
            f"/v1/upload/{upload_id}",
            content=b"data",
            headers={**self._admin_headers(pub_token), "Content-Type": "application/octet-stream"},
        )

        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 403

    def test_cancel_upload(self, db_server_env):
        """DELETE /v1/upload/{id} cancels an upload session."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.delete(f"/v1/upload/{upload_id}", headers=hdrs)
        assert resp.status_code == 204

        # Session should be gone
        resp = client.get(f"/v1/upload/{upload_id}", headers=hdrs)
        assert resp.status_code == 404

    def test_cancel_not_found(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.delete(
            "/v1/upload/nonexistent",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 404

    def test_cancel_wrong_actor(self, db_server_env):
        """Different actor cannot cancel another's upload."""
        client, admin_token, pub_token, _, _ = db_server_env

        resp = client.post(
            "/v1/upload/init",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=self._admin_headers(pub_token),
        )
        upload_id = resp.json()["upload_id"]

        resp = client.delete(
            f"/v1/upload/{upload_id}",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 403

    def test_duplicate_rejected(self, db_server_env):
        """Init rejects duplicate name/version/platform/arch combo."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)
        params = {"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"}

        # First publish via regular endpoint
        client.post(
            "/v1/publish",
            params=params,
            files={"file": ("pkg.tar.zst", io.BytesIO(b"content"))},
            headers=hdrs,
        )

        # Chunked init for same should fail
        resp = client.post("/v1/upload/init", params=params, headers=hdrs)
        assert resp.status_code == 409

    def test_multi_chunk_upload(self, db_server_env):
        """Multiple chunks accumulate correctly."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={"name": "big", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        chunk1 = b"A" * 100
        chunk2 = b"B" * 200
        chunk3 = b"C" * 300

        for chunk in [chunk1, chunk2, chunk3]:
            resp = client.patch(
                f"/v1/upload/{upload_id}",
                content=chunk,
                headers={**hdrs, "Content-Type": "application/octet-stream"},
            )
            assert resp.status_code == 200

        assert resp.json()["bytes_received"] == 600

        # Complete
        full = chunk1 + chunk2 + chunk3
        sha256 = hashlib.sha256(full).hexdigest()
        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": sha256},
            headers=hdrs,
        )
        assert resp.status_code == 200
        assert resp.json()["sha256"] == sha256

    def test_completed_upload_appears_in_packages(self, db_server_env):
        """After completing upload, the package appears in listings."""
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)

        resp = client.post(
            "/v1/upload/init",
            params={
                "name": "chunked-pkg",
                "version": "3.0.0",
                "platform": "linux",
                "arch": "x86_64",
            },
            headers=hdrs,
        )
        upload_id = resp.json()["upload_id"]

        content = b"archive data"
        client.patch(
            f"/v1/upload/{upload_id}",
            content=content,
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )

        sha256 = hashlib.sha256(content).hexdigest()
        client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": sha256},
            headers=hdrs,
        )

        # Verify in package list
        resp = client.get("/v1/packages", headers=hdrs)
        assert resp.status_code == 200
        pkgs = resp.json()
        names = [p["name"] for p in pkgs.get("packages", pkgs.get("bundles", []))]
        assert "chunked-pkg" in names

    def test_publish_race_loser_leaves_winner_archive_intact(self, db_server_env, monkeypatch):
        """Regression: a /v1/publish that loses the insert race must not
        touch the winner's archive.

        The July 2026 incident (7 corrupted catalog variants: bzip2,
        fontconfig, graphene, libpq, lua, nasm, wayland) came through the
        direct-publish path: two builders published the same variant
        concurrently, both passed the advisory duplicate pre-check, and
        the loser renamed its temp over the winner's archive before its
        add_package() 409'd — old code then even unlinked the destination.

        The race window is simulated by disabling the advisory pre-check
        (as if both requests passed it before either row existed). The
        loser must 409, and the winner's row AND bytes must survive.
        """
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)
        params = {
            "name": "raced-direct",
            "version": "2.0.0",
            "platform": "linux",
            "arch": "x86_64",
        }

        winner_content = b"winner via direct publish - must survive"
        winner_sha = hashlib.sha256(winner_content).hexdigest()
        resp = client.post(
            "/v1/publish",
            params=params,
            files={"file": ("raced-direct.tar.zst", io.BytesIO(winner_content))},
            headers=hdrs,
        )
        assert resp.status_code in (200, 201), resp.text

        # Simulate the race: the loser's advisory pre-check saw no row.
        from cvcpkg.server import app as app_mod

        async def _no_dup(*a, **k):
            return False

        monkeypatch.setattr(app_mod._db_packages, "check_duplicate", _no_dup)

        loser_content = b"loser via direct publish - must be discarded!!"
        loser_sha = hashlib.sha256(loser_content).hexdigest()
        assert loser_sha != winner_sha
        resp = client.post(
            "/v1/publish",
            params=params,
            files={"file": ("raced-direct.tar.zst", io.BytesIO(loser_content))},
            headers=hdrs,
        )
        assert resp.status_code == 409, resp.text

        # Winner's row is intact and the served bytes still match it.
        resp = client.get("/v1/packages", params={"name": "raced-direct"}, headers=hdrs)
        rows = resp.json().get("packages", [])
        row = next(p for p in rows if p["version"] == "2.0.0")
        assert row["sha256"] == winner_sha
        resp = client.get(row["archive_url"], headers=hdrs)
        assert resp.status_code == 200, "winner's archive was deleted by the losing publish"
        assert (
            hashlib.sha256(resp.content).hexdigest() == winner_sha
        ), "winner's archive bytes were clobbered by the losing publish"

    def test_complete_does_not_clobber_existing_archive(self, db_server_env):
        """Regression: /v1/upload/{id}/complete must not clobber an on-disk
        archive when another publish races in between init and complete.

        Reproduces the drift bug that left 16 production bundles with an
        on-disk sha256/size that disagreed with the DB metadata. Sequence:

          1. Client A calls /v1/upload/init for (name, version, plat, arch).
             The dup check passes because no row exists yet.
          2. Client B publishes the same NVR via /v1/publish. A row +
             archive with sha=B are now present.
          3. Client A finishes chunking and calls /v1/upload/{id}/complete
             with different content (sha=A).

        Before the fix, step 3 renamed the temp file over B's archive
        (clobber) and then failed on the unique index in add_package,
        leaving DB.sha=B but disk.sha=A. Now step 3 must reject with 409
        and leave B's archive on disk unchanged.
        """
        client, _, pub_token, _, _ = db_server_env
        hdrs = self._admin_headers(pub_token)
        params = {
            "name": "raced",
            "version": "1.0.0",
            "platform": "linux",
            "arch": "x86_64",
        }

        # Step 1: init chunked upload — no dup yet, so this succeeds.
        resp = client.post("/v1/upload/init", params=params, headers=hdrs)
        assert resp.status_code == 201
        upload_id = resp.json()["upload_id"]

        # Step 2: another publisher wins the race via /v1/publish.
        winner_content = b"winner archive content - should stay on disk"
        winner_sha = hashlib.sha256(winner_content).hexdigest()
        resp = client.post(
            "/v1/publish",
            params=params,
            files={"file": ("raced.tar.zst", io.BytesIO(winner_content))},
            headers=hdrs,
        )
        assert resp.status_code in (200, 201), resp.text

        # Step 3: client A finishes chunking with *different* content and
        # tries to complete. This must be rejected as a duplicate rather
        # than clobbering the winner's archive.
        loser_content = b"loser archive content - MUST NOT overwrite winner"
        loser_sha = hashlib.sha256(loser_content).hexdigest()
        assert loser_sha != winner_sha
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=loser_content,
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": loser_sha},
            headers=hdrs,
        )
        assert resp.status_code == 409, resp.text

        # On-disk archive must still be the winner's content, and the
        # catalog sha256 must still match what's on disk (no drift).
        resp = client.get("/v1/packages", headers=hdrs)
        assert resp.status_code == 200
        pkgs_payload = resp.json()
        rows = pkgs_payload.get("packages", pkgs_payload.get("bundles", []))
        row = next(p for p in rows if p["name"] == "raced" and p["version"] == "1.0.0")
        assert (
            row["sha256"] == winner_sha
        ), f"catalog sha256 drifted: DB={row['sha256']} winner={winner_sha}"

        archive_url = row.get("archive_url") or f"/v1/download/{row.get('filename', '')}"
        resp = client.get(archive_url, headers=hdrs)
        assert resp.status_code == 200
        on_disk_sha = hashlib.sha256(resp.content).hexdigest()
        assert on_disk_sha == winner_sha, (
            f"on-disk archive was clobbered: got sha={on_disk_sha}, "
            f"expected winner sha={winner_sha}"
        )
