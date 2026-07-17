"""Tests for draining a queue without registering a builder.

macOS has no persistent builder — GitHub-hosted runners are ephemeral. The old
arrangement registered one per drain, which left a dead `macos-drain-gha-<run>`
registration behind for a machine that no longer existed (the unregister is
admin-only and CI holds a publisher token, so the DELETE 403'd and the client
never checked). An unregistered worker has nothing to leave behind.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from tests.unit.test_build_jobs import db_server_env  # noqa: F401  (fixture)


def _submit(client, token, recipe_name="zlib", platform="macos", arch="arm64", priority=0):
    return client.post(
        "/v1/builds",
        json={
            "recipe_name": recipe_name,
            "platform": platform,
            "arch": arch,
            "config": "release",
            "link": "shared",
            "priority": priority,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


class TestNextClaimable:
    def test_returns_pending_job_for_platform(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]

        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos", "arch": "arm64"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == job_id

    def test_204_when_no_work(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 204

    def test_does_not_return_other_platforms(self, db_server_env):  # noqa: F811
        # A macOS drainer must never pick up the Linux fleet's work.
        client, _admin, pub, _ = db_server_env
        _submit(client, pub, platform="linux", arch="x86_64")
        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 204

    def test_arch_filter(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        _submit(client, pub, platform="macos", arch="x86_64")
        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos", "arch": "arm64"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 204

    def test_empty_arch_matches_any(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub, platform="macos", arch="x86_64").json()["id"]
        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos", "arch": ""},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_priority_then_age_ordering(self, db_server_env):  # noqa: F811
        # Must match the dispatcher's order, or an unregistered drainer would
        # quietly service a different queue order than a registered builder.
        client, _admin, pub, _ = db_server_env
        _submit(client, pub, recipe_name="first-low", priority=0)
        hi = _submit(client, pub, recipe_name="second-high", priority=10).json()["id"]

        resp = client.get(
            "/v1/builds/next-claimable",
            params={"platform": "macos", "arch": "arm64"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == hi, "higher priority must win over age"

    def test_requires_auth(self, db_server_env):  # noqa: F811
        client, _admin, _pub, _ = db_server_env
        resp = client.get("/v1/builds/next-claimable", params={"platform": "macos"})
        assert resp.status_code in (401, 403)


class TestAnonymousClaim:
    def test_claim_without_builder_id(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]

        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-29372085620"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "running"
        assert data["builder_id"] is None, "no builder should be invented"
        assert data["claimed_by"] == "gha-run-29372085620"

    def test_claim_without_builder_or_claimant_refused(self, db_server_env):  # noqa: F811
        # A running job must always be attributable to something.
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            json={},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 422
        assert "anonymous" in resp.text.lower()

    def test_second_claim_conflicts(self, db_server_env):  # noqa: F811
        # Unregistered workers select by platform, so two can reach the same
        # job.  The loser must be told, not handed a 200 and left to build a
        # job someone else is already building.
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]

        first = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-1"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-2"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert second.status_code == 409, second.text
        assert "already running" in second.text

        # The first claimant keeps the job.
        info = client.get(f"/v1/builds/{job_id}", headers={"Authorization": f"Bearer {pub}"}).json()
        assert info["claimed_by"] == "gha-run-1"

    def test_same_claimant_reclaim_is_idempotent(self, db_server_env):  # noqa: F811
        # A worker whose claim response was lost to a network blip must be able
        # to retry.  Only a *different* worker gets a conflict — that is the
        # line between "retry" and "someone else has it".
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]

        for _ in range(2):
            resp = client.post(
                f"/v1/builds/{job_id}/claim",
                json={"claimant": "gha-run-1"},
                headers={"Authorization": f"Bearer {pub}"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "running"
            assert resp.json()["claimed_by"] == "gha-run-1"

    def test_registered_builder_claim_still_works(self, db_server_env):  # noqa: F811
        # The existing path must be untouched.
        client, _admin, pub, _ = db_server_env
        bid = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "macos", "arch": "arm64"},
            headers={"Authorization": f"Bearer {pub}"},
        ).json()["id"]
        job_id = _submit(client, pub).json()["id"]

        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"builder_id": bid},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200
        assert resp.json()["builder_id"] == bid
        assert resp.json()["claimed_by"] == ""

    def test_drained_job_completes(self, db_server_env):  # noqa: F811
        # complete() reconciles a builder's job count; with no builder that
        # reconciliation must be skipped rather than blow up.
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]
        client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-1"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        resp = client.post(
            f"/v1/builds/{job_id}/complete",
            json={},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "succeeded"

    def test_drained_job_fails_cleanly(self, db_server_env):  # noqa: F811
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]
        client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-1"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        resp = client.post(
            f"/v1/builds/{job_id}/fail",
            json={"error_message": "boom"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "failed"

    def test_drain_leaves_no_builder_registered(self, db_server_env):  # noqa: F811
        # The whole point: macOS drains without ever appearing in the fleet.
        client, _admin, pub, _ = db_server_env
        job_id = _submit(client, pub).json()["id"]
        client.post(
            f"/v1/builds/{job_id}/claim",
            json={"claimant": "gha-run-29372085620"},
            headers={"Authorization": f"Bearer {pub}"},
        )
        builders = client.get("/v1/builders", headers={"Authorization": f"Bearer {pub}"}).json()[
            "builders"
        ]
        assert builders == [], f"drain must register nothing, got {builders}"
