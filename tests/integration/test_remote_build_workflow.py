"""Advanced integration tests for the remote build workflow.

Exercises the full remote-builder lifecycle end-to-end:

  1. Boot a DB-backed cvcpkg-server (in-process, SQLite)
  2. Create an organisation and two users (admin + publisher)
  3. Push dummy recipes (base + org-scoped, with dependencies)
  4. Register two builders (linux/x86_64 + linux/aarch64)
  5. Verify builder status via list/info
  6. Submit single builds and a multi-recipe DAG
  7. Simulate builder execution: claim → log → complete/fail
  8. Verify build status transitions, log content, DAG completion
  9. Test build log download / stream / delete
 10. Verify the web UI package detail page has build-jobs section
 11. Test WebSocket heartbeat + job operations
"""

from __future__ import annotations

import asyncio
import datetime
import io
import sqlite3
import tarfile
import textwrap

import pytest
import yaml

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import BuildJobStatus, TokenRole

# ── Helpers ─────────────────────────────────────────────────────


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_recipe_bundle(
    name: str,
    *,
    version: str = "1.0.0",
    deps: list[str] | None = None,
    build_script: str = "",
) -> bytes:
    """Create an in-memory recipe tar.gz bundle with optional deps."""
    if not build_script:
        build_script = textwrap.dedent(
            f"""\
            #!/bin/bash
            mkdir -p "$CVC_INSTALL_DIR/include"
            echo "// {name} stub header" > "$CVC_INSTALL_DIR/include/{name}.h"
        """
        )

    recipe_dict: dict = {
        "recipe": {
            "name": name,
            "upstream_version": version,
            "cvc_revision": 1,
        },
        "source": {"type": "none"},
    }
    if deps:
        recipe_dict["dependencies"] = {d: "*" for d in deps}

    recipe_yaml = yaml.dump(recipe_dict, default_flow_style=False)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname, data in [
            ("recipe.yaml", recipe_yaml.encode()),
            ("build.sh", build_script.encode()),
        ]:
            info = tarfile.TarInfo(name=fname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    buf.seek(0)
    return buf.read()


def _make_archive(name: str, version: str, platform: str, arch: str) -> bytes:
    """Create a minimal publishable package archive."""
    manifest = yaml.dump(
        {
            "schema_version": 3,
            "bundle": {
                "name": name,
                "version": f"{version}+cvc.1",
                "upstream_version": version,
                "cvc_revision": 1,
                "platform": platform,
                "arch": arch,
                "build_type": "release",
                "link": "shared",
            },
            "contents": {
                "files": [f"lib/lib{name}.so", f"include/{name}.h"],
            },
        }
    ).encode()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for fname, data in [
            (f"lib/lib{name}.so", b"\x7fELF" + b"\x00" * 64),
            (f"include/{name}.h", f"// {name}\n".encode()),
            (
                f"share/libcvc-deps/{name}/manifest.yaml",
                manifest,
            ),
        ]:
            info = tarfile.TarInfo(name=fname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """DB-backed test server with admin + publisher tokens and an org.

    Yields a dict with keys:
        client, admin_token, pub_token, tmp_path
    """
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
        admin = await store.create("admin-ci", TokenRole.admin)
        pub = await store.create("builder-ci", TokenRole.publisher)
        await dispose_engine()
        return admin, pub

    admin_tok, pub_tok = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield {
            "client": client,
            "admin_token": admin_tok,
            "pub_token": pub_tok,
            "tmp_path": tmp_path,
        }


# ── Test class ──────────────────────────────────────────────────


class TestRemoteBuildWorkflow:
    """Full end-to-end remote build workflow."""

    # ── setup helpers ───────────────────────────────────────

    def _create_org(self, c, token, slug):
        resp = c.post(
            "/v1/orgs",
            json={"slug": slug, "display_name": slug.replace("-", " ").title()},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _add_member(self, c, token, slug, member_name, role="member"):
        resp = c.post(
            f"/v1/orgs/{slug}/members",
            params={"token_name": member_name, "role": role},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text

    def _push_recipe(self, c, token, name, *, org="", **kw):
        bundle = _make_recipe_bundle(name, **kw)
        params = {"version": kw.get("version", "1.0.0")}
        if org:
            params["org_slug"] = org
        resp = c.post(
            f"/v1/recipes/{name}",
            headers=_auth(token),
            params=params,
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _register_builder(
        self,
        c,
        token,
        name,
        *,
        platform="linux",
        arch="x86_64",
        org="",
        max_jobs=2,
        capabilities=None,
    ):
        resp = c.post(
            "/v1/builders/register",
            headers=_auth(token),
            json={
                "name": name,
                "platform": platform,
                "arch": arch,
                "org_slug": org,
                "max_jobs": max_jobs,
                "labels": ["ci"],
                "capabilities": capabilities or {},
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _submit_job(
        self,
        c,
        token,
        recipe,
        *,
        platform="linux",
        arch="x86_64",
        config="release",
        link="shared",
        org="",
    ):
        resp = c.post(
            "/v1/builds",
            headers=_auth(token),
            json={
                "recipe_name": recipe,
                "platform": platform,
                "arch": arch,
                "config": config,
                "link": link,
                "org_slug": org,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    # ── test: recipe setup ──────────────────────────────────

    def test_01_push_base_and_org_recipes(self, server):
        """Push base recipes (zlib, libpng) and org-scoped recipe (mylib)."""
        c, tok = server["client"], server["admin_token"]

        # Base recipes
        r1 = self._push_recipe(c, tok, "zlib")
        assert r1["name"] == "zlib"

        r2 = self._push_recipe(c, tok, "libpng", deps=["zlib"])
        assert r2["name"] == "libpng"

        # Org-scoped recipe
        self._create_org(c, tok, "cvc-lab")
        self._add_member(c, tok, "cvc-lab", "builder-ci")
        r3 = self._push_recipe(c, tok, "mylib", org="cvc-lab", deps=["zlib", "libpng"])
        assert r3["name"] == "mylib"

        # List recipes
        resp = c.get("/v1/recipes", headers=_auth(tok))
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()["recipes"]}
        assert {"zlib", "libpng", "mylib"} <= names

    # ── test: builder registration & status ─────────────────

    def test_02_register_builders_and_check_status(self, server):
        """Register two builders and verify their status."""
        c, tok = server["client"], server["admin_token"]

        b1 = self._register_builder(c, tok, "builder-x86", platform="linux", arch="x86_64")
        b2 = self._register_builder(c, tok, "builder-arm", platform="linux", arch="aarch64")
        assert b1["platform"] == "linux"
        assert b1["arch"] == "x86_64"
        assert b2["arch"] == "aarch64"

        # List builders
        resp = c.get("/v1/builders", headers=_auth(tok))
        assert resp.status_code == 200
        builders = resp.json()["builders"]
        names = {b["name"] for b in builders}
        assert {"builder-x86", "builder-arm"} <= names

        # Info
        resp = c.get(f"/v1/builders/{b1['id']}", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["status"] == "online"

        # Heartbeat
        resp = c.post(
            f"/v1/builders/{b1['id']}/heartbeat",
            headers=_auth(tok),
            json={"status": "online", "current_jobs": 0},
        )
        assert resp.status_code == 200

    # ── test: single build lifecycle ────────────────────────

    def test_03_single_build_claim_log_complete(self, server):
        """Submit a build, claim it, stream logs, mark complete."""
        c, tok = server["client"], server["admin_token"]

        # Setup
        self._push_recipe(c, tok, "zlib-single")
        builder = self._register_builder(c, tok, "single-builder")
        builder_id = builder["id"]
        job = self._submit_job(c, tok, "zlib-single")
        job_id = job["id"]
        assert job["status"] == BuildJobStatus.pending

        # Claim
        resp = c.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(tok),
            json={"builder_id": builder_id},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

        # Stream log chunks
        for line in [
            "Downloading recipe 'zlib-single'…\n",
            "Configuring…\n",
            "Building…\n",
            "Build succeeded.\n",
        ]:
            resp = c.patch(
                f"/v1/builds/{job_id}/log",
                headers=_auth(tok),
                json={"data": line},
            )
            assert resp.status_code == 200

        # Download log
        resp = c.get(f"/v1/builds/{job_id}/log", headers=_auth(tok))
        assert resp.status_code == 200
        log_text = resp.text
        assert "Configuring" in log_text
        assert "Build succeeded" in log_text

        # Complete
        resp = c.post(
            f"/v1/builds/{job_id}/complete",
            headers=_auth(tok),
            json={"result_archive_url": "/v1/packages/zlib-single"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.succeeded

        # Verify final status
        resp = c.get(f"/v1/builds/{job_id}", headers=_auth(tok))
        assert resp.status_code == 200
        info = resp.json()
        assert info["status"] == BuildJobStatus.succeeded
        assert info["result_archive_url"] == "/v1/packages/zlib-single"
        assert info["started_at"] is not None
        assert info["finished_at"] is not None

    # ── test: build failure flow ────────────────────────────

    def test_04_build_failure_with_error_message(self, server):
        """Submit a build, claim it, fail with an error message."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "badlib")
        builder = self._register_builder(c, tok, "fail-builder")
        job = self._submit_job(c, tok, "badlib")
        job_id = job["id"]

        # Claim
        c.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )

        # Log the error
        c.patch(
            f"/v1/builds/{job_id}/log",
            headers=_auth(tok),
            json={"data": "error: missing header <zlib.h>\n"},
        )

        # Fail
        resp = c.post(
            f"/v1/builds/{job_id}/fail",
            headers=_auth(tok),
            json={"error_message": "compilation failed: missing zlib.h"},
        )
        assert resp.status_code == 200
        info = resp.json()
        assert info["status"] == BuildJobStatus.failed
        assert "missing zlib.h" in info["error_message"]

    # ── test: DAG submission and multi-job lifecycle ─────────

    def test_05_dag_submit_and_complete_all(self, server):
        """Submit a DAG of 3 builds, complete them all, verify dag completion."""
        c, tok = server["client"], server["admin_token"]

        # Push recipes
        self._push_recipe(c, tok, "dag-zlib")
        self._push_recipe(c, tok, "dag-libpng", deps=["dag-zlib"])
        self._push_recipe(c, tok, "dag-mylib", deps=["dag-zlib", "dag-libpng"])

        builder = self._register_builder(c, tok, "dag-builder")
        builder_id = builder["id"]

        # Submit DAG (depends_on uses 0-based indices into the jobs array)
        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "jobs": [
                    {
                        "recipe_name": "dag-zlib",
                        "platform": "linux",
                        "arch": "x86_64",
                    },
                    {
                        "recipe_name": "dag-libpng",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                    {
                        "recipe_name": "dag-mylib",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0, 1],
                    },
                ]
            },
        )
        assert resp.status_code == 200
        dag = resp.json()
        dag_id = dag["dag_id"]
        jobs = dag["jobs"]
        assert len(jobs) == 3

        # Map recipe names to job IDs
        jmap = {j["recipe_name"]: j["id"] for j in jobs}

        # Complete each job in dependency order
        for recipe in ["dag-zlib", "dag-libpng", "dag-mylib"]:
            jid = jmap[recipe]

            # Claim
            resp = c.post(
                f"/v1/builds/{jid}/claim",
                headers=_auth(tok),
                json={"builder_id": builder_id},
            )
            assert resp.status_code == 200

            # Log
            c.patch(
                f"/v1/builds/{jid}/log",
                headers=_auth(tok),
                json={"data": f"Building {recipe}…\nDone.\n"},
            )

            # Complete
            resp = c.post(
                f"/v1/builds/{jid}/complete",
                headers=_auth(tok),
                json={"result_archive_url": f"/v1/packages/{recipe}"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == BuildJobStatus.succeeded

        # List jobs by dag_id — all should be succeeded
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"dag_id": dag_id},
        )
        assert resp.status_code == 200
        dag_jobs = resp.json()["jobs"]
        assert all(j["status"] == BuildJobStatus.succeeded for j in dag_jobs)

    # ── test: DAG with partial failure ──────────────────────

    def test_06_dag_partial_failure(self, server):
        """If a DAG job fails, downstream dependents should be cancelled."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "pfail-base")
        self._push_recipe(c, tok, "pfail-dep", deps=["pfail-base"])

        builder = self._register_builder(c, tok, "pfail-builder")

        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "jobs": [
                    {
                        "recipe_name": "pfail-base",
                        "platform": "linux",
                        "arch": "x86_64",
                    },
                    {
                        "recipe_name": "pfail-dep",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ],
            },
        )
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        base_id = next(j["id"] for j in jobs if j["recipe_name"] == "pfail-base")

        # Claim and fail the base job
        c.post(
            f"/v1/builds/{base_id}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )
        resp = c.post(
            f"/v1/builds/{base_id}/fail",
            headers=_auth(tok),
            json={"error_message": "segfault during build"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.failed

    # ── test: org-scoped builds ─────────────────────────────

    def test_07_org_scoped_recipe_and_build(self, server):
        """Push an org-scoped recipe, submit and complete a build for it."""
        c, tok = server["client"], server["admin_token"]

        self._create_org(c, tok, "org-builds")
        self._add_member(c, tok, "org-builds", "builder-ci")

        self._push_recipe(c, tok, "org-pkg", org="org-builds")
        builder = self._register_builder(c, tok, "org-builder")
        job = self._submit_job(c, tok, "org-pkg", org="org-builds")
        job_id = job["id"]
        assert job["org_slug"] == "org-builds"

        # Claim + complete
        c.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )
        resp = c.post(
            f"/v1/builds/{job_id}/complete",
            headers=_auth(tok),
            json={"result_archive_url": "/v1/packages/org-pkg"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.succeeded

    # ── test: builder list with filters ─────────────────────

    def test_08_builds_list_with_filters(self, server):
        """List builds filtered by recipe_name and status."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "filter-lib")
        builder = self._register_builder(c, tok, "filter-builder")

        # Submit two jobs
        j1 = self._submit_job(c, tok, "filter-lib")
        self._submit_job(c, tok, "filter-lib", config="debug")

        # Complete j1
        c.post(
            f"/v1/builds/{j1['id']}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )
        c.post(
            f"/v1/builds/{j1['id']}/complete",
            headers=_auth(tok),
            json={"result_archive_url": ""},
        )

        # Filter by recipe name
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"recipe_name": "filter-lib"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

        # Filter by status
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"recipe_name": "filter-lib", "status": "succeeded"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    # ── test: log operations ────────────────────────────────

    def test_09_log_append_download_delete(self, server):
        """Append log data, download it, then delete it."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "logtest")
        builder = self._register_builder(c, tok, "log-builder")
        job = self._submit_job(c, tok, "logtest")
        job_id = job["id"]

        # Claim
        c.post(
            f"/v1/builds/{job_id}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )

        # Append multiple log chunks
        log_lines = [
            "Step 1: configure\n",
            "Step 2: compile\n",
            "Step 3: install\n",
        ]
        for line in log_lines:
            resp = c.patch(
                f"/v1/builds/{job_id}/log",
                headers=_auth(tok),
                json={"data": line},
            )
            assert resp.status_code == 200

        # Download full log
        resp = c.get(f"/v1/builds/{job_id}/log", headers=_auth(tok))
        assert resp.status_code == 200
        full_log = resp.text
        for line in log_lines:
            assert line.strip() in full_log

        # Complete (so log persists)
        c.post(
            f"/v1/builds/{job_id}/complete",
            headers=_auth(tok),
            json={"result_archive_url": ""},
        )

        # Delete log
        resp = c.delete(f"/v1/builds/{job_id}/log", headers=_auth(tok))
        assert resp.status_code == 200

    # ── test: cancel build ──────────────────────────────────

    def test_10_cancel_pending_build(self, server):
        """Cancel a pending build job."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "cancel-lib")
        self._register_builder(c, tok, "cancel-builder")
        job = self._submit_job(c, tok, "cancel-lib")

        resp = c.post(
            f"/v1/builds/{job['id']}/cancel",
            headers=_auth(tok),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.cancelled

    # ── test: multi-platform builds ─────────────────────────

    def test_11_multiplatform_builds(self, server):
        """Submit builds for two architectures, verify independent."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "cross-lib")
        b_x86 = self._register_builder(c, tok, "cross-x86", platform="linux", arch="x86_64")
        b_arm = self._register_builder(c, tok, "cross-arm", platform="linux", arch="aarch64")

        j_x86 = self._submit_job(c, tok, "cross-lib", platform="linux", arch="x86_64")
        j_arm = self._submit_job(c, tok, "cross-lib", platform="linux", arch="aarch64")

        # Claim each with matching builder
        for jid, bid in [(j_x86["id"], b_x86["id"]), (j_arm["id"], b_arm["id"])]:
            resp = c.post(
                f"/v1/builds/{jid}/claim",
                headers=_auth(tok),
                json={"builder_id": bid},
            )
            assert resp.status_code == 200

        # Complete both
        for jid in [j_x86["id"], j_arm["id"]]:
            resp = c.post(
                f"/v1/builds/{jid}/complete",
                headers=_auth(tok),
                json={"result_archive_url": ""},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == BuildJobStatus.succeeded

        # Verify both are independent
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"recipe_name": "cross-lib"},
        )
        assert resp.json()["total"] == 2
        arches = {j["arch"] for j in resp.json()["jobs"]}
        assert arches == {"x86_64", "aarch64"}

    # ── test: WebSocket heartbeat and job ops ───────────────

    def test_12_websocket_heartbeat_and_complete(self, server):
        """Use WebSocket to heartbeat, claim, log, and complete a job."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "ws-full")
        builder = self._register_builder(c, tok, "ws-full-builder")
        builder_id = builder["id"]
        job = self._submit_job(c, tok, "ws-full")
        job_id = job["id"]

        with c.websocket_connect(f"/v1/builders/{builder_id}/ws?token={tok}") as ws:
            # Heartbeat
            ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
            ack = ws.receive_json()
            assert ack["type"] == "heartbeat_ack"

            # Claim via WebSocket
            ws.send_json({"type": "job.claim", "job_id": job_id})
            ack = ws.receive_json()
            assert ack["type"] == "job.claim_ack"
            assert ack["status"] == BuildJobStatus.running

            # Stream log via WebSocket
            ws.send_json({"type": "job.log", "job_id": job_id, "data": "WS build log\n"})

            # Complete via WebSocket
            ws.send_json(
                {
                    "type": "job.complete",
                    "job_id": job_id,
                    "archive_url": "/v1/packages/ws-full",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "job.complete_ack"
            assert ack["status"] == BuildJobStatus.succeeded

        # Verify via REST
        resp = c.get(f"/v1/builds/{job_id}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.succeeded

        # Verify log was written
        resp = c.get(f"/v1/builds/{job_id}/log", headers=_auth(tok))
        assert resp.status_code == 200
        assert "WS build log" in resp.text

    # ── test: WebSocket fail via WS ─────────────────────────

    def test_13_websocket_fail_job(self, server):
        """Fail a job entirely via WebSocket."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "ws-fail")
        builder = self._register_builder(c, tok, "ws-fail-builder")
        job = self._submit_job(c, tok, "ws-fail")

        with c.websocket_connect(f"/v1/builders/{builder['id']}/ws?token={tok}") as ws:
            ws.send_json({"type": "job.claim", "job_id": job["id"]})
            ws.receive_json()

            ws.send_json(
                {
                    "type": "job.fail",
                    "job_id": job["id"],
                    "error": "OOM killed",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "job.fail_ack"
            assert ack["status"] == BuildJobStatus.failed

        resp = c.get(f"/v1/builds/{job['id']}", headers=_auth(tok))
        assert resp.json()["error_message"] == "OOM killed"

    # ── test: web UI ────────────────────────────────────────

    def test_14_package_detail_page_has_build_jobs(self, server):
        """The package detail page should contain the build-jobs section."""
        c = server["client"]

        resp = c.get("/package/some-package")
        assert resp.status_code == 200
        html = resp.text
        assert "build-jobs-section" in html
        assert "Recent Build Jobs" in html
        assert "loadBuildJobs" in html

    # ── test: recipe download ───────────────────────────────

    def test_15_recipe_download_and_contents(self, server):
        """Download a pushed recipe bundle and verify contents."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "dl-recipe", version="2.5.0")

        resp = c.get("/v1/recipes/dl-recipe", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.headers["content-type"] in (
            "application/gzip",
            "application/x-gzip",
        )

        # Verify it's a valid tar.gz with recipe.yaml
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            names = tar.getnames()
            assert "recipe.yaml" in names
            assert "build.sh" in names

    # ── test: builds purge ──────────────────────────────────

    def test_16_builds_purge_old_jobs(self, server):
        """The purge endpoint should work without errors."""
        c, tok = server["client"], server["admin_token"]

        # Purge with aggressive filter — should return ok even with 0 results
        resp = c.post(
            "/v1/admin/gc/logs",
            headers=_auth(tok),
        )
        assert resp.status_code == 200

    # ── test: builder update ────────────────────────────────

    def test_17_builder_update_fields(self, server):
        """Update builder labels and max_jobs."""
        c, tok = server["client"], server["admin_token"]

        builder = self._register_builder(c, tok, "update-builder")

        resp = c.patch(
            f"/v1/builders/{builder['id']}",
            headers=_auth(tok),
            json={
                "labels": ["gpu", "high-mem"],
                "max_jobs": 8,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["labels"]) == {"gpu", "high-mem"}
        assert data["max_jobs"] == 8

    # ── test: builder unregister ────────────────────────────

    def test_18_builder_unregister(self, server):
        """Unregister a builder and verify it's gone."""
        c, tok = server["client"], server["admin_token"]

        builder = self._register_builder(c, tok, "unreg-builder")
        resp = c.delete(
            f"/v1/builders/{builder['id']}",
            headers=_auth(tok),
        )
        assert resp.status_code == 200

        resp = c.get(
            f"/v1/builders/{builder['id']}",
            headers=_auth(tok),
        )
        # Should be 404 or return offline status
        assert resp.status_code in (404, 200)


# ── Error‑Recovery Test Class ───────────────────────────────────


class TestBuilderRecovery:
    """Simulate error & recovery scenarios: builder crashes, timeouts, reconnects."""

    # ── helpers (shared with the workflow class) ────────────

    def _push_recipe(self, c, token, name, **kw):
        bundle = _make_recipe_bundle(name, **kw)
        params = {"version": kw.get("version", "1.0.0")}
        resp = c.post(
            f"/v1/recipes/{name}",
            headers=_auth(token),
            params=params,
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _register_builder(self, c, token, name, **kw):
        resp = c.post(
            "/v1/builders/register",
            headers=_auth(token),
            json={
                "name": name,
                "platform": kw.get("platform", "linux"),
                "arch": kw.get("arch", "x86_64"),
                "org_slug": kw.get("org", ""),
                "max_jobs": kw.get("max_jobs", 2),
                "labels": ["ci"],
                "capabilities": kw.get("capabilities", {}),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _submit_job(self, c, token, recipe, **kw):
        resp = c.post(
            "/v1/builds",
            headers=_auth(token),
            json={
                "recipe_name": recipe,
                "platform": kw.get("platform", "linux"),
                "arch": kw.get("arch", "x86_64"),
                "config": kw.get("config", "release"),
                "link": kw.get("link", "shared"),
                "org_slug": kw.get("org", ""),
                "timeout_seconds": kw.get("timeout_seconds"),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    # ── test: builder killed mid-build, job orphaned ────────

    def test_19_builder_killed_mid_build(self, server):
        """Unregister a builder while it has a running job.

        The job should remain 'running' (orphaned) — the timeout reaper
        will deal with it later.  Partial logs should still be readable.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "orphan-lib")
        builder = self._register_builder(c, tok, "doomed-builder")
        bid = builder["id"]
        job = self._submit_job(c, tok, "orphan-lib")
        jid = job["id"]

        # Claim and write partial logs
        c.post(f"/v1/builds/{jid}/claim", headers=_auth(tok), json={"builder_id": bid})
        for line in ["Step 1/3: configure\n", "Step 2/3: compile\n"]:
            c.patch(f"/v1/builds/{jid}/log", headers=_auth(tok), json={"data": line})

        # Simulate crash: unregister the builder
        resp = c.delete(f"/v1/builders/{bid}", headers=_auth(tok))
        assert resp.status_code == 200

        # Job is still running (orphaned, no builder to finish it)
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.status_code == 200
        info = resp.json()
        assert info["status"] == BuildJobStatus.running

        # Partial logs are still accessible
        resp = c.get(f"/v1/builds/{jid}/log", headers=_auth(tok))
        assert resp.status_code == 200
        assert "Step 2/3: compile" in resp.text

        # Builder is gone
        resp = c.get(f"/v1/builders/{bid}", headers=_auth(tok))
        assert resp.status_code in (404, 200)

    # ── DB helpers for time manipulation ─────────────────────

    @staticmethod
    def _db_path(server):
        return str(server["tmp_path"] / "test.db")

    def _backdate_job(self, server, job_id, seconds_ago):
        """Set a job's started_at to *seconds_ago* in the past via raw SQLite."""
        ts = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
        ).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
        conn = sqlite3.connect(self._db_path(server))
        conn.execute("UPDATE build_jobs SET started_at = ? WHERE id = ?", (ts, job_id))
        conn.commit()
        conn.close()

    def _backdate_heartbeat(self, server, builder_id, seconds_ago):
        """Set a builder's last_heartbeat to *seconds_ago* in the past."""
        ts = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
        ).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
        conn = sqlite3.connect(self._db_path(server))
        conn.execute(
            "UPDATE builders SET last_heartbeat = ? WHERE id = ?",
            (ts, builder_id),
        )
        conn.commit()
        conn.close()

    def _set_builder_status(self, server, builder_id, status, seconds_ago=600):
        """Force a builder to a given status via raw SQLite."""
        ts = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
        ).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
        conn = sqlite3.connect(self._db_path(server))
        conn.execute(
            "UPDATE builders SET status = ?, last_heartbeat = ? WHERE id = ?",
            (status, ts, builder_id),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _run_async(coro):
        """Run an async coroutine from synchronous test code."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    # ── test: job timeout reaper marks orphaned job ─────────

    def test_20_timeout_reaps_orphaned_job(self, server):
        """Directly invoke the timeout reaper on a job whose started_at
        is far in the past.  The job should transition to 'timed_out'
        and its error message should describe the timeout.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "timeout-lib")
        builder = self._register_builder(c, tok, "timeout-builder")
        # Submit with a very short timeout (60s is the minimum)
        job = self._submit_job(c, tok, "timeout-lib", timeout_seconds=60)
        jid = job["id"]

        # Claim so the job is running
        c.post(f"/v1/builds/{jid}/claim", headers=_auth(tok), json={"builder_id": builder["id"]})

        # Push started_at 2 minutes into the past so it exceeds the 60s timeout
        self._backdate_job(server, jid, seconds_ago=120)

        # Invoke the reaper
        import cvcpkg.server.app as _app

        reaped = self._run_async(_app._db_build_jobs.reap_timed_out(default_timeout=86400))
        assert len(reaped) >= 1
        reaped_ids = {j.id for j in reaped}
        assert jid in reaped_ids

        # Verify via REST
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        info = resp.json()
        assert info["status"] == BuildJobStatus.timed_out
        assert "timeout" in info["error_message"].lower()

    # ── test: stale heartbeat reaper marks builder offline ──

    def test_21_stale_heartbeat_reaps_builder(self, server):
        """Backdate last_heartbeat so the builder looks stale,
        then run the stale-builder reaper and verify it goes offline.
        """
        c, tok = server["client"], server["admin_token"]

        builder = self._register_builder(c, tok, "stale-builder")
        bid = builder["id"]

        # Confirm it's online
        resp = c.get(f"/v1/builders/{bid}", headers=_auth(tok))
        assert resp.json()["status"] == "online"

        # Backdate last_heartbeat by 5 minutes via raw SQLite
        self._backdate_heartbeat(server, bid, seconds_ago=300)

        # Run the stale-builder reaper (threshold = 180s)
        import cvcpkg.server.app as _app

        reaped = self._run_async(_app._db_builders.reap_stale(max_age_seconds=180))
        reaped_ids = {b.id for b in reaped}
        assert bid in reaped_ids

        # Verify via REST
        resp = c.get(f"/v1/builders/{bid}", headers=_auth(tok))
        assert resp.json()["status"] == "offline"

    # ── test: builder re-registration after crash ───────────

    def test_22_builder_re_registration_after_crash(self, server):
        """A builder that went offline can re-register with the same name
        and come back online.
        """
        c, tok = server["client"], server["admin_token"]

        b1 = self._register_builder(c, tok, "phoenix-builder")
        bid = b1["id"]

        # Force it offline via raw SQLite
        self._set_builder_status(server, bid, "offline")

        resp = c.get(f"/v1/builders/{bid}", headers=_auth(tok))
        assert resp.json()["status"] == "offline"

        # Re-register with same name — should come back online
        b2 = self._register_builder(c, tok, "phoenix-builder")
        assert b2["id"] == bid, "should reuse the same builder row"
        assert b2["status"] == "online"

        # Heartbeat confirms liveness
        resp = c.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(tok),
            json={"status": "online", "current_jobs": 0},
        )
        assert resp.status_code == 200

    # ── test: WebSocket disconnect mid-build ────────────────

    def test_23_websocket_disconnect_mid_build(self, server):
        """Abruptly close a WebSocket while a job is running.

        The job should remain 'running' — the builder can reconnect
        (or the timeout reaper will eventually clean it up).
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "ws-crash-lib")
        builder = self._register_builder(c, tok, "ws-crash-builder")
        bid = builder["id"]
        job = self._submit_job(c, tok, "ws-crash-lib")
        jid = job["id"]

        # Connect via WebSocket, claim via WS, write log via REST, then drop
        with c.websocket_connect(f"/v1/builders/{bid}/ws?token={tok}") as ws:
            ws.send_json({"type": "job.claim", "job_id": jid})
            ack = ws.receive_json()
            assert ack["type"] == "job.claim_ack"
            assert ack["status"] == BuildJobStatus.running
            # WebSocket closes here (context manager exit)

        # Write log via REST (reliable, synchronous)
        c.patch(
            f"/v1/builds/{jid}/log",
            headers=_auth(tok),
            json={"data": "compiling before crash…\n"},
        )

        # Job is still running — not cancelled by disconnect
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.running

        # Log written before disconnect is preserved
        resp = c.get(f"/v1/builds/{jid}/log", headers=_auth(tok))
        assert "compiling before crash" in resp.text

        # A new builder (or same builder reconnecting) can still
        # complete the job via REST
        resp = c.post(
            f"/v1/builds/{jid}/complete",
            headers=_auth(tok),
            json={"result_archive_url": "/recovered"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.succeeded

    # ── test: DAG cascading timeout ─────────────────────────

    def test_24_dag_cascading_timeout(self, server):
        """When the root job of a DAG times out, downstream jobs
        should be cancelled with an explanatory error message.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "dag-timeout-root")
        self._push_recipe(c, tok, "dag-timeout-leaf")

        builder = self._register_builder(c, tok, "dag-timeout-builder")

        # Submit a 2-job DAG: leaf depends on root
        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "jobs": [
                    {
                        "recipe_name": "dag-timeout-root",
                        "platform": "linux",
                        "arch": "x86_64",
                        "timeout_seconds": 60,
                    },
                    {
                        "recipe_name": "dag-timeout-leaf",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ]
            },
        )
        assert resp.status_code == 200
        dag = resp.json()
        root_id = next(j["id"] for j in dag["jobs"] if j["recipe_name"] == "dag-timeout-root")
        leaf_id = next(j["id"] for j in dag["jobs"] if j["recipe_name"] == "dag-timeout-leaf")

        # Claim root
        c.post(
            f"/v1/builds/{root_id}/claim", headers=_auth(tok), json={"builder_id": builder["id"]}
        )
        c.patch(f"/v1/builds/{root_id}/log", headers=_auth(tok), json={"data": "building root…\n"})

        # Backdate started_at to trigger timeout
        self._backdate_job(server, root_id, seconds_ago=120)

        # Reap + cascade
        import cvcpkg.server.app as _app

        reaped = self._run_async(_app._db_build_jobs.reap_timed_out(default_timeout=86400))
        assert any(j.id == root_id for j in reaped)

        # Cancel downstream
        cancelled = self._run_async(_app._db_build_jobs.cancel_downstream(root_id))
        assert cancelled >= 1

        # Verify root is timed_out
        resp = c.get(f"/v1/builds/{root_id}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.timed_out
        assert "timeout" in resp.json()["error_message"].lower()

        # Verify leaf is cancelled with dependency explanation
        resp = c.get(f"/v1/builds/{leaf_id}", headers=_auth(tok))
        leaf = resp.json()
        assert leaf["status"] == BuildJobStatus.cancelled
        assert "dependency" in leaf["error_message"].lower()

    # ── test: second builder completes after first fails ────

    def test_25_second_builder_picks_up_after_failure(self, server):
        """Builder A claims and fails a job.  A new job for the same
        recipe is submitted and completed by Builder B.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "resilient-lib")
        b_a = self._register_builder(c, tok, "builder-A")
        b_b = self._register_builder(c, tok, "builder-B")

        # First attempt — Builder A fails
        j1 = self._submit_job(c, tok, "resilient-lib")
        c.post(f"/v1/builds/{j1['id']}/claim", headers=_auth(tok), json={"builder_id": b_a["id"]})
        c.patch(
            f"/v1/builds/{j1['id']}/log", headers=_auth(tok), json={"data": "FATAL: disk full\n"}
        )
        resp = c.post(
            f"/v1/builds/{j1['id']}/fail",
            headers=_auth(tok),
            json={"error_message": "disk full"},
        )
        assert resp.json()["status"] == BuildJobStatus.failed

        # Verify failure is logged
        resp = c.get(f"/v1/builds/{j1['id']}/log", headers=_auth(tok))
        assert "disk full" in resp.text.lower()

        # Second attempt — re-submit, Builder B succeeds
        j2 = self._submit_job(c, tok, "resilient-lib")
        assert j2["id"] != j1["id"]
        c.post(f"/v1/builds/{j2['id']}/claim", headers=_auth(tok), json={"builder_id": b_b["id"]})
        c.patch(
            f"/v1/builds/{j2['id']}/log",
            headers=_auth(tok),
            json={"data": "Build succeeded on Builder B\n"},
        )
        resp = c.post(
            f"/v1/builds/{j2['id']}/complete",
            headers=_auth(tok),
            json={"result_archive_url": "/v1/packages/resilient-lib"},
        )
        assert resp.json()["status"] == BuildJobStatus.succeeded

        # Both jobs exist — one failed, one succeeded
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"recipe_name": "resilient-lib"},
        )
        statuses = {j["status"] for j in resp.json()["jobs"]}
        assert BuildJobStatus.failed in statuses
        assert BuildJobStatus.succeeded in statuses

    # ── test: WS reconnect and complete orphaned job ────────

    def test_26_ws_reconnect_completes_orphaned_job(self, server):
        """Builder disconnects, reconnects on a fresh WebSocket,
        and completes the orphaned job.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "reconnect-lib")
        builder = self._register_builder(c, tok, "reconnect-builder")
        bid = builder["id"]
        job = self._submit_job(c, tok, "reconnect-lib")
        jid = job["id"]

        # First WS session — claim via WS, log via REST, then disconnect
        with c.websocket_connect(f"/v1/builders/{bid}/ws?token={tok}") as ws:
            ws.send_json({"type": "job.claim", "job_id": jid})
            ack = ws.receive_json()
            assert ack["status"] == BuildJobStatus.running
            # disconnect

        # Write log via REST (reliable across WS disconnect)
        c.patch(
            f"/v1/builds/{jid}/log",
            headers=_auth(tok),
            json={"data": "phase 1 done\n"},
        )

        # Job still running
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.running

        # Second WS session — pick up where we left off
        with c.websocket_connect(f"/v1/builders/{bid}/ws?token={tok}") as ws:
            ws.send_json({"type": "job.log", "job_id": jid, "data": "phase 2 done\n"})
            ws.send_json(
                {
                    "type": "job.complete",
                    "job_id": jid,
                    "archive_url": "/v1/packages/reconnect-lib",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "job.complete_ack"
            assert ack["status"] == BuildJobStatus.succeeded

        # Log contains both phases
        resp = c.get(f"/v1/builds/{jid}/log", headers=_auth(tok))
        assert "phase 1 done" in resp.text
        assert "phase 2 done" in resp.text

    # ── test: cancel running build ──────────────────────────

    def test_27_cancel_running_build_is_rejected(self, server):
        """Cancel on a running build is a no-op — only pending/dispatched
        jobs can be cancelled.  The job stays 'running'.
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "cancel-running-lib")
        builder = self._register_builder(c, tok, "cancel-running-builder")
        job = self._submit_job(c, tok, "cancel-running-lib")
        jid = job["id"]

        # Claim → running
        c.post(f"/v1/builds/{jid}/claim", headers=_auth(tok), json={"builder_id": builder["id"]})
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.running

        # Attempt cancel — should return current status (running), not cancelled
        resp = c.post(f"/v1/builds/{jid}/cancel", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

    # ── test: cross-compilation via cross_platforms ──────────

    def test_28_cross_compile_wasm_dispatched_to_linux_builder(self, server):
        """A wasm job should be claimable by a linux builder registered
        with cross_platforms: [{"platform": "wasm", "arch": "wasm32"}].
        """
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "wasm-lib")

        # Register linux builder with cross_platforms capability
        builder = self._register_builder(
            c,
            tok,
            "linux-wasm-builder",
            platform="linux",
            arch="x86_64",
            capabilities={"cross_platforms": [{"platform": "wasm", "arch": "wasm32"}]},
        )

        # Submit a wasm build
        job = self._submit_job(c, tok, "wasm-lib", platform="wasm", arch="wasm32")
        assert job["platform"] == "wasm"
        assert job["arch"] == "wasm32"

        # The linux builder should be able to claim this job
        resp = c.post(
            f"/v1/builds/{job['id']}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

        # Complete the build
        resp = c.post(
            f"/v1/builds/{job['id']}/complete",
            headers=_auth(tok),
            json={"result_archive_url": "/v1/packages/wasm-lib"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.succeeded
        assert resp.json()["platform"] == "wasm"
        assert resp.json()["arch"] == "wasm32"

    def test_29_cross_compile_dag_wasm_on_linux(self, server):
        """Submit a wasm DAG and complete via linux builder with cross_platforms."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "wasm-base")
        self._push_recipe(c, tok, "wasm-app", deps=["wasm-base"])

        builder = self._register_builder(
            c,
            tok,
            "linux-wasm-dag-builder",
            platform="linux",
            arch="x86_64",
            capabilities={"cross_platforms": [{"platform": "wasm", "arch": "wasm32"}]},
        )

        # Submit 2-job DAG targeting wasm
        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "dag_id": "wasm-dag-1",
                "jobs": [
                    {
                        "recipe_name": "wasm-base",
                        "platform": "wasm",
                        "arch": "wasm32",
                        "depends_on": [],
                    },
                    {
                        "recipe_name": "wasm-app",
                        "platform": "wasm",
                        "arch": "wasm32",
                        "depends_on": [0],
                    },
                ],
            },
        )
        assert resp.status_code == 200
        dag = resp.json()
        assert dag["total"] == 2
        job_ids = [j["id"] for j in dag["jobs"]]

        # Complete in dependency order
        for jid in job_ids:
            c.post(
                f"/v1/builds/{jid}/claim",
                headers=_auth(tok),
                json={"builder_id": builder["id"]},
            )
            resp = c.post(
                f"/v1/builds/{jid}/complete",
                headers=_auth(tok),
                json={"result_archive_url": ""},
            )
            assert resp.status_code == 200

        # All jobs succeeded
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"dag_id": "wasm-dag-1"},
        )
        statuses = {j["status"] for j in resp.json()["jobs"]}
        assert statuses == {BuildJobStatus.succeeded}

    def test_30_cross_platforms_in_builder_capabilities(self, server):
        """Verify cross_platforms are stored and returned in builder info."""
        c, tok = server["client"], server["admin_token"]

        cross = [
            {"platform": "wasm", "arch": "wasm32"},
            {"platform": "wasi", "arch": "wasm32"},
        ]
        builder = self._register_builder(
            c,
            tok,
            "cross-caps-builder",
            platform="linux",
            arch="x86_64",
            capabilities={"cross_platforms": cross},
        )
        assert builder["capabilities"]["cross_platforms"] == cross

        # Verify via GET
        resp = c.get(f"/v1/builders/{builder['id']}", headers=_auth(tok))
        assert resp.json()["capabilities"]["cross_platforms"] == cross

    # ── test: pause / resume single build ───────────────────

    def test_31_pause_and_resume_pending_build(self, server):
        """Pause a pending build, verify it's paused, resume it, verify pending."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "pause-lib")
        self._register_builder(c, tok, "pause-builder")
        job = self._submit_job(c, tok, "pause-lib")
        jid = job["id"]

        # Pause
        resp = c.post(f"/v1/builds/{jid}/pause", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.paused

        # Verify via GET
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.paused

        # Resume
        resp = c.post(f"/v1/builds/{jid}/resume", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.pending

        # Verify via GET
        resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.pending

    def test_32_pause_running_build_is_noop(self, server):
        """Pause on a running build should be a no-op."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "pause-running-lib")
        builder = self._register_builder(c, tok, "pause-running-builder")
        job = self._submit_job(c, tok, "pause-running-lib")
        jid = job["id"]

        # Claim → running
        c.post(
            f"/v1/builds/{jid}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )

        # Attempt pause — should be no-op
        resp = c.post(f"/v1/builds/{jid}/pause", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["status"] == BuildJobStatus.running

    # ── test: pause / resume DAG ────────────────────────────

    def test_33_pause_and_resume_dag(self, server):
        """Pause all pending jobs in a DAG, resume them, then complete."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "dag-pause-a")
        self._push_recipe(c, tok, "dag-pause-b", deps=["dag-pause-a"])
        builder = self._register_builder(c, tok, "dag-pause-builder")

        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "dag_id": "pause-resume-dag",
                "jobs": [
                    {
                        "recipe_name": "dag-pause-a",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [],
                    },
                    {
                        "recipe_name": "dag-pause-b",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ],
            },
        )
        assert resp.status_code == 200
        job_ids = [j["id"] for j in resp.json()["jobs"]]

        # Pause DAG
        resp = c.post("/v1/builds/dag/pause-resume-dag/pause", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["paused"] == 2

        # Verify both are paused
        for jid in job_ids:
            resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
            assert resp.json()["status"] == BuildJobStatus.paused

        # Resume DAG
        resp = c.post("/v1/builds/dag/pause-resume-dag/resume", headers=_auth(tok))
        assert resp.status_code == 200
        assert resp.json()["resumed"] == 2

        # Verify both are pending again
        for jid in job_ids:
            resp = c.get(f"/v1/builds/{jid}", headers=_auth(tok))
            assert resp.json()["status"] == BuildJobStatus.pending

        # Complete the DAG normally
        for jid in job_ids:
            c.post(
                f"/v1/builds/{jid}/claim",
                headers=_auth(tok),
                json={"builder_id": builder["id"]},
            )
            c.post(
                f"/v1/builds/{jid}/complete",
                headers=_auth(tok),
                json={"result_archive_url": ""},
            )

        # All succeeded
        resp = c.get(
            "/v1/builds",
            headers=_auth(tok),
            params={"dag_id": "pause-resume-dag"},
        )
        statuses = {j["status"] for j in resp.json()["jobs"]}
        assert statuses == {BuildJobStatus.succeeded}

    def test_34_pause_dag_skips_running_jobs(self, server):
        """Pausing a DAG should only affect pending/dispatched jobs, not running ones."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "dag-skip-a")
        self._push_recipe(c, tok, "dag-skip-b", deps=["dag-skip-a"])
        builder = self._register_builder(c, tok, "dag-skip-builder")

        resp = c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "dag_id": "partial-pause-dag",
                "jobs": [
                    {
                        "recipe_name": "dag-skip-a",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [],
                    },
                    {
                        "recipe_name": "dag-skip-b",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ],
            },
        )
        job_ids = [j["id"] for j in resp.json()["jobs"]]

        # Claim first job → running
        c.post(
            f"/v1/builds/{job_ids[0]}/claim",
            headers=_auth(tok),
            json={"builder_id": builder["id"]},
        )

        # Pause DAG — should only pause the second job (first is running)
        resp = c.post("/v1/builds/dag/partial-pause-dag/pause", headers=_auth(tok))
        assert resp.json()["paused"] == 1

        # First job still running
        resp = c.get(f"/v1/builds/{job_ids[0]}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.running

        # Second job paused
        resp = c.get(f"/v1/builds/{job_ids[1]}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.paused


# ── CLI integration tests ───────────────────────────────────────


class TestCLIBuildCommands:
    """Exercise CLI commands through main() against an in-process server.

    Uses monkeypatch to route httpx through the TestClient transport so
    that CLI commands hit the real server code path without HTTP overhead.
    """

    def _push_recipe(self, c, token, name, **kw):
        bundle = _make_recipe_bundle(name, **kw)
        params = {"version": kw.get("version", "1.0.0")}
        resp = c.post(
            f"/v1/recipes/{name}",
            headers=_auth(token),
            params=params,
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
        )
        assert resp.status_code == 200, resp.text

    def _register_builder(self, c, token, name, **kw):
        resp = c.post(
            "/v1/builders/register",
            headers=_auth(token),
            json={
                "name": name,
                "platform": kw.get("platform", "linux"),
                "arch": kw.get("arch", "x86_64"),
                "max_jobs": kw.get("max_jobs", 2),
                "labels": ["ci"],
                "capabilities": kw.get("capabilities", {}),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _cli(self, server, args):
        """Run a CLI command via main(), injecting server URL and token."""
        from cvcpkg.cli import main

        base_url = str(server["client"].base_url).rstrip("/")
        tok = server["admin_token"]
        full_args = args + ["--server", base_url, "--token", tok]
        return main(full_args)

    def _make_httpx_use_testclient(self, monkeypatch, server):
        """Monkeypatch httpx.Client to use the TestClient's transport."""
        import httpx

        tc = server["client"]
        base_url = str(tc.base_url)

        class PatchedClient(httpx.Client):
            def __init__(self, **kw):
                kw.setdefault("transport", tc._transport)
                kw.setdefault("base_url", base_url)
                super().__init__(**kw)

        monkeypatch.setattr("httpx.Client", PatchedClient)

    # ── builds submit → info → pause → resume → cancel ─────

    def test_35_cli_builds_submit_info_pause_resume_cancel(self, server, monkeypatch, capsys):
        """Full CLI lifecycle: submit → info → pause → resume → cancel."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "cli-lifecycle-lib")
        self._register_builder(c, tok, "cli-builder")
        self._make_httpx_use_testclient(monkeypatch, server)

        # Submit a job via API (CLI submit needs recipe hash etc.)
        resp = c.post(
            "/v1/builds",
            headers=_auth(tok),
            json={
                "recipe_name": "cli-lifecycle-lib",
                "platform": "linux",
                "arch": "x86_64",
            },
        )
        job_id = resp.json()["id"]

        # CLI: builds info
        ret = self._cli(server, ["builds", "info", str(job_id)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cli-lifecycle-lib" in out
        assert "pending" in out.lower()

        # CLI: builds pause
        ret = self._cli(server, ["builds", "pause", str(job_id)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "paused" in out.lower()

        # Verify via API
        resp = c.get(f"/v1/builds/{job_id}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.paused

        # CLI: builds resume
        ret = self._cli(server, ["builds", "resume", str(job_id)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "pending" in out.lower()

        # CLI: builds cancel
        ret = self._cli(server, ["builds", "cancel", str(job_id)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cancelled" in out.lower()

        # Verify via API
        resp = c.get(f"/v1/builds/{job_id}", headers=_auth(tok))
        assert resp.json()["status"] == BuildJobStatus.cancelled

    # ── DAG pause / resume / cancel via CLI ─────────────────

    def test_36_cli_dag_pause_resume_cancel(self, server, monkeypatch, capsys):
        """CLI: submit DAG → pause-dag → resume-dag → cancel-dag."""
        c, tok = server["client"], server["admin_token"]

        self._push_recipe(c, tok, "cli-dag-a")
        self._push_recipe(c, tok, "cli-dag-b", deps=["cli-dag-a"])
        self._register_builder(c, tok, "cli-dag-builder")
        self._make_httpx_use_testclient(monkeypatch, server)

        # Submit DAG via API
        c.post(
            "/v1/builds/dag",
            headers=_auth(tok),
            json={
                "dag_id": "cli-dag-test",
                "jobs": [
                    {"recipe_name": "cli-dag-a", "platform": "linux", "arch": "x86_64"},
                    {
                        "recipe_name": "cli-dag-b",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ],
            },
        )

        # CLI: builds pause-dag
        ret = self._cli(server, ["builds", "pause-dag", "cli-dag-test"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "paused" in out.lower()

        # CLI: builds resume-dag
        ret = self._cli(server, ["builds", "resume-dag", "cli-dag-test"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "resumed" in out.lower()

        # CLI: builds cancel-dag
        ret = self._cli(server, ["builds", "cancel-dag", "cli-dag-test"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cancelled" in out.lower()

    # ── builder list / status / unregister via CLI ──────────

    def test_37_cli_builder_list_status_unregister(self, server, monkeypatch, capsys):
        """CLI: builder list → status → unregister."""
        c, tok = server["client"], server["admin_token"]
        builder = self._register_builder(c, tok, "cli-mgmt-builder")
        self._make_httpx_use_testclient(monkeypatch, server)

        # CLI: builder list
        ret = self._cli(server, ["builder", "list"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cli-mgmt-builder" in out

        # CLI: builder status
        ret = self._cli(server, ["builder", "status", str(builder["id"])])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cli-mgmt-builder" in out

        # CLI: builder unregister
        ret = self._cli(server, ["builder", "unregister", str(builder["id"])])
        assert ret == 0
        out = capsys.readouterr().out
        assert "unregistered" in out.lower()

    # ── builds list with filters ────────────────────────────

    def test_38_cli_builds_list(self, server, monkeypatch, capsys):
        """CLI: builds list shows submitted jobs."""
        c, tok = server["client"], server["admin_token"]
        self._push_recipe(c, tok, "cli-list-lib")
        self._register_builder(c, tok, "cli-list-builder")
        self._make_httpx_use_testclient(monkeypatch, server)

        # Submit a job
        c.post(
            "/v1/builds",
            headers=_auth(tok),
            json={"recipe_name": "cli-list-lib", "platform": "linux", "arch": "x86_64"},
        )

        # CLI: builds list
        ret = self._cli(server, ["builds", "list"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cli-list-lib" in out

    # ── builds log-delete ───────────────────────────────────

    def test_39_cli_builds_log_delete(self, server, monkeypatch, capsys):
        """CLI: builds log-delete removes the log for a job."""
        c, tok = server["client"], server["admin_token"]
        self._push_recipe(c, tok, "cli-logdel-lib")
        self._register_builder(c, tok, "cli-logdel-builder")
        self._make_httpx_use_testclient(monkeypatch, server)

        # Submit + claim + append log + complete
        resp = c.post(
            "/v1/builds",
            headers=_auth(tok),
            json={"recipe_name": "cli-logdel-lib", "platform": "linux", "arch": "x86_64"},
        )
        jid = resp.json()["id"]
        builder = c.get("/v1/builders", headers=_auth(tok)).json()["builders"][-1]
        c.post(f"/v1/builds/{jid}/claim", headers=_auth(tok), json={"builder_id": builder["id"]})
        c.post(f"/v1/builds/{jid}/log", headers=_auth(tok), content="build output\n")
        c.post(f"/v1/builds/{jid}/complete", headers=_auth(tok))

        # CLI: builds log-delete
        ret = self._cli(server, ["builds", "log-delete", str(jid)])
        assert ret == 0

    # ── test: live token revocation tears down an open socket ──

    def test_40_revoked_token_tears_down_live_socket(self, server, monkeypatch):
        """Revoking a builder's token must close its already-open socket.

        A builder socket authenticates once at connect; without periodic
        re-verification a revoked token would retain full job-plane access
        until the builder happened to disconnect.  The server should instead
        re-verify on its re-auth cadence and close the live socket.
        """
        from fastapi import WebSocketDisconnect

        c = server["client"]
        admin = server["admin_token"]
        pub = server["pub_token"]  # role=publisher, name="builder-ci"

        builder = self._register_builder(c, admin, "reauth-builder")
        builder_id = builder["id"]

        # Re-verify aggressively so the test does not wait on the 30s default.
        monkeypatch.setattr("cvcpkg.server.app._WS_REAUTH_INTERVAL_SECONDS", 0.1)

        with c.websocket_connect(f"/v1/builders/{builder_id}/ws?token={pub}") as ws:
            # The socket is live: a heartbeat is acknowledged.
            ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
            assert ws.receive_json()["type"] == "heartbeat_ack"

            # Revoke the publisher's token out from under the open socket.
            resp = c.delete("/v1/tokens/builder-ci", headers=_auth(admin))
            assert resp.status_code == 200, resp.text

            # The next re-auth tick tears the socket down (4001 = token gone).
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4001
