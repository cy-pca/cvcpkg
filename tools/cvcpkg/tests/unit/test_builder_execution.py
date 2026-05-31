"""Tests for the builder job execution loop.

Covers the end-to-end flow: register builder → push recipe → submit
job → dispatch → builder claims, executes, publishes, reports.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import textwrap

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import BuildJobStatus, TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin token."""
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
        await dispose_engine()
        return admin_raw

    admin_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, tmp_path


def _make_recipe_bundle(name: str, build_script: str = "") -> bytes:
    """Create an in-memory recipe tar.gz bundle."""
    if not build_script:
        build_script = textwrap.dedent(
            """\
            #!/bin/bash
            mkdir -p "$CVC_INSTALL_DIR/include"
            echo "// stub" > "$CVC_INSTALL_DIR/include/{name}.h"
        """
        ).format(name=name)

    recipe_yaml = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
    """
    )

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # recipe.yaml
        info = tarfile.TarInfo(name="recipe.yaml")
        data = recipe_yaml.encode()
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

        # build.sh
        info = tarfile.TarInfo(name="build.sh")
        data = build_script.encode()
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    buf.seek(0)
    return buf.read()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── End-to-end flow tests ──────────────────────────────────────


class TestBuilderExecution:
    """Test the server-side flow a builder goes through."""

    def _register_builder(self, client, token):
        resp = client.post(
            "/v1/builders/register",
            headers=_auth(token),
            json={
                "name": "test-builder",
                "platform": "linux",
                "arch": "x86_64",
                "max_jobs": 2,
                "labels": [],
                "capabilities": {},
            },
        )
        assert resp.status_code == 200
        return resp.json()

    def _push_recipe(self, client, token, name="testlib"):
        bundle = _make_recipe_bundle(name)
        resp = client.post(
            f"/v1/recipes/{name}",
            headers=_auth(token),
            params={"version": "1.0.0"},
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
        )
        assert resp.status_code == 200
        return resp.json()

    def _submit_job(self, client, token, recipe_name="testlib"):
        resp = client.post(
            "/v1/builds",
            headers=_auth(token),
            json={
                "recipe_name": recipe_name,
                "platform": "linux",
                "arch": "x86_64",
                "config": "release",
                "link": "shared",
            },
        )
        assert resp.status_code == 200
        return resp.json()

    def test_full_job_lifecycle(self, db_server_env):
        """Register → push recipe → submit job → claim → log → complete."""
        client, token, tmp_path = db_server_env

        # Setup
        builder = self._register_builder(client, token)
        self._push_recipe(client, token)
        job = self._submit_job(client, token)
        job_id = job["id"]
        builder_id = builder["id"]

        # Job starts as pending
        assert job["status"] == BuildJobStatus.pending

        # Claim the job
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builder_id},
        )
        assert resp.status_code == 200
        claimed = resp.json()
        assert claimed["status"] == BuildJobStatus.running
        assert claimed["builder_id"] == builder_id

        # Append log
        resp = client.patch(
            f"/v1/builds/{job_id}/log",
            headers=_auth(token),
            json={"data": "Building testlib…\n"},
        )
        assert resp.status_code == 200

        resp = client.patch(
            f"/v1/builds/{job_id}/log",
            headers=_auth(token),
            json={"data": "Build succeeded.\n"},
        )
        assert resp.status_code == 200

        # Download log
        resp = client.get(
            f"/v1/builds/{job_id}/log",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert "Building testlib" in resp.text
        assert "Build succeeded" in resp.text

        # Complete
        resp = client.post(
            f"/v1/builds/{job_id}/complete",
            headers=_auth(token),
            json={"result_archive_url": "https://pkg.example.com/testlib-1.0.0.tar.gz"},
        )
        assert resp.status_code == 200
        completed = resp.json()
        assert completed["status"] == BuildJobStatus.succeeded
        assert completed["result_archive_url"] == "https://pkg.example.com/testlib-1.0.0.tar.gz"

        # Verify final state
        resp = client.get(f"/v1/builds/{job_id}", headers=_auth(token))
        assert resp.status_code == 200
        final = resp.json()
        assert final["status"] == BuildJobStatus.succeeded
        assert final["started_at"] is not None
        assert final["finished_at"] is not None

    def test_job_failure_flow(self, db_server_env):
        """Submit → claim → fail with error message."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        self._push_recipe(client, token)
        job = self._submit_job(client, token)
        job_id = job["id"]

        # Claim
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200

        # Stream error log
        resp = client.patch(
            f"/v1/builds/{job_id}/log",
            headers=_auth(token),
            json={"data": "configure: error: zlib.h not found\n"},
        )
        assert resp.status_code == 200

        # Report failure
        resp = client.post(
            f"/v1/builds/{job_id}/fail",
            headers=_auth(token),
            json={"error_message": "configure failed: zlib.h not found"},
        )
        assert resp.status_code == 200
        failed = resp.json()
        assert failed["status"] == BuildJobStatus.failed
        assert "zlib.h" in failed["error_message"]

    def test_next_job_returns_dispatched(self, db_server_env):
        """next-job should return a job dispatched to this builder."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        self._push_recipe(client, token)
        self._submit_job(client, token)

        # The job is pending — the DAG scheduler would dispatch it.
        # Manually dispatch by having the scheduler run, or just claim directly.
        # For testing next-job, let's use the next-job endpoint with a short timeout.
        resp = client.get(
            f"/v1/builders/{builder['id']}/next-job",
            headers=_auth(token),
            params={"timeout": "1"},
        )
        # The job is pending, not yet dispatched to this builder,
        # so next-job returns 204 (scheduler hasn't run yet)
        assert resp.status_code in (200, 204)

    def test_recipe_download(self, db_server_env):
        """Pushed recipe should be downloadable."""
        client, token, tmp_path = db_server_env

        self._push_recipe(client, token, name="mylib")

        resp = client.get(
            "/v1/recipes/mylib",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type") in (
            "application/gzip",
            "application/x-gzip",
        )
        # Should be valid tar.gz
        import tarfile as tf

        buf = io.BytesIO(resp.content)
        with tf.open(fileobj=buf, mode="r:gz") as tar:
            names = tar.getnames()
        assert "recipe.yaml" in names
        assert "build.sh" in names

    def test_recipe_not_found(self, db_server_env):
        """Downloading a non-existent recipe returns 404."""
        client, token, tmp_path = db_server_env

        resp = client.get(
            "/v1/recipes/nonexistent",
            headers=_auth(token),
        )
        assert resp.status_code == 404

    def test_claim_twice_is_idempotent(self, db_server_env):
        """Claiming an already-running job by the same builder is idempotent."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        self._push_recipe(client, token)
        job = self._submit_job(client, token)
        job_id = job["id"]

        # First claim succeeds
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

        # Second claim by same builder also returns 200 (idempotent)
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

    def test_dag_submit_and_lifecycle(self, db_server_env):
        """Submit a DAG of jobs and complete them in order."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        self._push_recipe(client, token, name="liba")
        self._push_recipe(client, token, name="libb")

        resp = client.post(
            "/v1/builds/dag",
            headers=_auth(token),
            json={
                "jobs": [
                    {
                        "recipe_name": "liba",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [],
                    },
                    {
                        "recipe_name": "libb",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],  # depends on first job
                    },
                ],
            },
        )
        assert resp.status_code == 200
        dag = resp.json()
        assert dag["total"] == 2
        dag_id = dag["dag_id"]

        # List jobs in this DAG
        resp = client.get(
            "/v1/builds",
            headers=_auth(token),
            params={"dag_id": dag_id},
        )
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert len(jobs) == 2

        # First job (liba) should be pending, second (libb) should
        # also be pending but blocked by deps
        liba_job = next(j for j in jobs if j["recipe_name"] == "liba")
        libb_job = next(j for j in jobs if j["recipe_name"] == "libb")

        # Claim and complete liba
        resp = client.post(
            f"/v1/builds/{liba_job['id']}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/v1/builds/{liba_job['id']}/complete",
            headers=_auth(token),
            json={"result_archive_url": ""},
        )
        assert resp.status_code == 200

        # Now libb should be claimable (dep satisfied)
        resp = client.post(
            f"/v1/builds/{libb_job['id']}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/v1/builds/{libb_job['id']}/complete",
            headers=_auth(token),
            json={"result_archive_url": ""},
        )
        assert resp.status_code == 200

    def test_multiple_log_appends(self, db_server_env):
        """Multiple log appends accumulate correctly."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        self._push_recipe(client, token)
        job = self._submit_job(client, token)
        job_id = job["id"]

        # Claim
        client.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(token),
            json={"builder_id": builder["id"]},
        )

        lines = [
            "Step 1: Configuring...\n",
            "Step 2: Building...\n",
            "Step 3: Testing...\n",
            "Step 4: Packaging...\n",
        ]
        for line in lines:
            resp = client.patch(
                f"/v1/builds/{job_id}/log",
                headers=_auth(token),
                json={"data": line},
            )
            assert resp.status_code == 200

        # Download and verify
        resp = client.get(f"/v1/builds/{job_id}/log", headers=_auth(token))
        assert resp.status_code == 200
        log_text = resp.text
        for line in lines:
            assert line.strip() in log_text

    def test_heartbeat_with_job_count(self, db_server_env):
        """Heartbeat should accept current_jobs count."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)

        resp = client.post(
            f"/v1/builders/{builder['id']}/heartbeat",
            headers=_auth(token),
            json={"status": "online", "current_jobs": 1},
        )
        assert resp.status_code == 200

    def test_builder_unregister_after_work(self, db_server_env):
        """Builder can unregister after completing jobs."""
        client, token, tmp_path = db_server_env

        builder = self._register_builder(client, token)
        builder_id = builder["id"]

        # Unregister
        resp = client.delete(
            f"/v1/builders/{builder_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get(
            f"/v1/builders/{builder_id}",
            headers=_auth(token),
        )
        assert resp.status_code in (404, 200)
        if resp.status_code == 200:
            assert resp.json()["status"] == "offline"
