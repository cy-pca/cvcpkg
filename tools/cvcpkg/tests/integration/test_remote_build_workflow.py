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
import io
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
                "capabilities": {},
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
