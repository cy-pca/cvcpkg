"""HTTP build-job endpoints answer what actually happened.

* claim: a job that could not be claimed (cancelled, paused, finished) is a
  409, not a 200 carrying the dead job -- every builder skips on 409 only, so
  a 200 made it build and publish a job nobody wanted.  The refused claim
  writes no audit entry and sends no ``build.started``.  An idempotent
  re-claim by the holder stays a 200 but is not audited or announced twice.
* cancel: repeating a cancel is a no-op -- no second audit entry, webhook,
  ``job.cancel`` push or cascade -- except that a forced repeat still
  cancels dependents a first, unforced cancel left behind.
* pause/resume: report "no-op" when the job was not in a state to move.
* complete/fail: a report naming a builder (or claimant) that no longer holds
  the job is a 409 and changes nothing -- unless nobody holds it any more
  because an admin deleted the holder mid-job, when the report still lands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    with server_on(
        request.param, tmp_path, monkeypatch, {"admin": "admin", "pub": "publisher"}
    ) as (client, raw):
        yield client, raw["admin"], raw["pub"]


def _chain(client, tok, dag_id):
    r = client.post(
        "/v1/builds/dag",
        json={
            "dag_id": dag_id,
            "jobs": [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
            ],
        },
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return [j["id"] for j in r.json()["jobs"]]


def _register(client, tok, name):
    r = client.post(
        "/v1/builders/register",
        json={"name": name, "platform": "linux", "arch": "x86_64"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _get(client, tok, job_id):
    r = client.get(f"/v1/builds/{job_id}", headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _audit_count(client, admin, action, job_id):
    r = client.get("/v1/audit", params={"action": action, "target": str(job_id)}, headers=_h(admin))
    assert r.status_code == 200, r.text
    return r.json()["total"]


def _claim(client, tok, job_id, **body):
    return client.post(
        f"/v1/builds/{job_id}/claim", json=body or {"claimant": "w1"}, headers=_h(tok)
    )


def _events(emit):
    return [c.args[0] for c in emit.await_args_list]


class TestClaimOfANonClaimableJob:
    @pytest.mark.parametrize("how", ["cancelled", "paused", "succeeded", "failed"])
    def test_is_409_with_no_audit_and_no_build_started(self, env, how):
        client, admin, pub = env
        a, b, _c = _chain(client, pub, f"claim-{how}")
        if how == "cancelled":
            assert client.post(f"/v1/builds/{a}/cancel", headers=_h(pub)).status_code == 200
        elif how == "paused":
            assert client.post(f"/v1/builds/{a}/pause", headers=_h(pub)).status_code == 200
        else:
            assert _claim(client, pub, a).status_code == 200
            verb = "complete" if how == "succeeded" else "fail"
            assert (
                client.post(f"/v1/builds/{a}/{verb}", json={}, headers=_h(pub)).status_code == 200
            )
        claims_before = _audit_count(client, admin, "build_claim", a)

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            r = _claim(client, pub, a, claimant="w2")
        assert r.status_code == 409, r.text
        assert how in r.json()["detail"]
        assert "build.started" not in _events(emit)
        assert _audit_count(client, admin, "build_claim", a) == claims_before
        assert _get(client, pub, a)["status"] == how

    def test_a_registered_builder_gets_the_same_409(self, env):
        client, _admin, pub = env
        a, _b, _c = _chain(client, pub, "claim-builder")
        bid = _register(client, pub, "bx")
        client.post(f"/v1/builds/{a}/cancel", headers=_h(pub))
        r = _claim(client, pub, a, builder_id=bid)
        assert r.status_code == 409
        assert _get(client, pub, a)["builder_id"] is None

    def test_holder_reclaim_is_200_but_not_audited_or_announced_twice(self, env):
        client, admin, pub = env
        a, _b, _c = _chain(client, pub, "reclaim")
        bid = _register(client, pub, "bx")
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            first = _claim(client, pub, a, builder_id=bid)
            again = _claim(client, pub, a, builder_id=bid)
        assert first.status_code == 200 and first.json()["status"] == "running"
        assert again.status_code == 200 and again.json()["status"] == "running"
        assert _events(emit).count("build.started") == 1
        assert _audit_count(client, admin, "build_claim", a) == 1


class TestCancelNoOp:
    def test_repeat_cancel_is_a_noop(self, env):
        client, admin, pub = env
        a, b, _c = _chain(client, pub, "cancel-twice")
        first = client.post(f"/v1/builds/{a}/cancel", headers=_h(pub))
        assert first.status_code == 200 and first.json()["message"] == "job cancelled"
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            again = client.post(f"/v1/builds/{a}/cancel", headers=_h(pub))
        assert again.status_code == 200
        assert again.json()["message"] == "no-op"
        assert again.json()["status"] == "cancelled"
        emit.assert_not_called()
        assert _audit_count(client, admin, "build_cancel", a) == 1

    def test_repeat_force_cancel_does_not_push_cascade_or_audit_again(self, env):
        client, admin, pub = env
        a, b, c = _chain(client, pub, "force-twice")
        bid = _register(client, pub, "bx")
        assert _claim(client, pub, a, builder_id=bid).status_code == 200
        with patch("cvcpkg.server.app._ws_send", new_callable=AsyncMock) as push:
            first = client.post(f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub))
            assert first.json()["cascaded"] == 2
            assert push.await_count == 1  # job.cancel to the builder
            with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
                again = client.post(
                    f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub)
                )
        assert again.json()["message"] == "no-op"
        assert push.await_count == 1  # no second job.cancel
        emit.assert_not_called()
        assert _audit_count(client, admin, "build_cancel", a) == 1
        assert _get(client, pub, b)["status"] == "cancelled"

    def test_force_after_an_unforced_cancel_cascades_once(self, env):
        client, admin, pub = env
        a, b, c = _chain(client, pub, "unforced-then-force")
        first = client.post(f"/v1/builds/{a}/cancel", headers=_h(pub))
        assert first.json()["message"] == "job cancelled"
        # An unforced cancel does not cascade; the dependents can never run.
        assert _get(client, pub, b)["status"] == "pending"
        with patch("cvcpkg.server.app._ws_send", new_callable=AsyncMock) as push:
            with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
                forced = client.post(
                    f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub)
                )
        assert forced.status_code == 200
        assert forced.json()["message"] == "dependents cancelled"
        assert forced.json()["cascaded"] == 2
        assert _get(client, pub, b)["status"] == "cancelled"
        assert _get(client, pub, c)["status"] == "cancelled"
        push.assert_not_called()  # the job itself did not change again
        assert _events(emit) == ["build.cancelled"]
        assert emit.await_args.args[1]["cascade_only"] is True
        assert _audit_count(client, admin, "build_cancel", a) == 2

        # ...and once the dependents are gone, a further force is a no-op.
        again = client.post(f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub))
        assert again.json()["message"] == "no-op"
        assert _audit_count(client, admin, "build_cancel", a) == 2


class TestPauseResumeNoOp:
    def test_pausing_a_running_job_says_noop(self, env):
        client, _admin, pub = env
        a, _b, _c = _chain(client, pub, "pause-running")
        assert _claim(client, pub, a).status_code == 200
        r = client.post(f"/v1/builds/{a}/pause", headers=_h(pub))
        assert r.status_code == 200
        assert r.json() == {"message": "no-op", "id": a, "status": "running"}

    def test_pause_then_resume(self, env):
        client, _admin, pub = env
        a, _b, _c = _chain(client, pub, "pause-resume")
        assert client.post(f"/v1/builds/{a}/pause", headers=_h(pub)).json()["message"] == (
            "job paused"
        )
        r = client.post(f"/v1/builds/{a}/resume", headers=_h(pub))
        assert r.json() == {"message": "job resumed", "id": a, "status": "pending"}
        r = client.post(f"/v1/builds/{a}/resume", headers=_h(pub))
        assert r.json()["message"] == "no-op"


class TestReporterOverHttp:
    def test_stale_report_from_a_previous_holder_is_409(self, env):
        client, admin, pub = env
        a, b, _c = _chain(client, pub, "stale-reporter")
        old = _register(client, pub, "old-holder")
        new = _register(client, pub, "new-holder")
        assert _claim(client, pub, a, builder_id=new).status_code == 200

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            stale_fail = client.post(
                f"/v1/builds/{a}/fail",
                json={"error_message": "stale", "builder_id": old},
                headers=_h(pub),
            )
            stale_done = client.post(
                f"/v1/builds/{a}/complete",
                json={"result_archive_url": "/stale", "builder_id": old},
                headers=_h(pub),
            )
        assert stale_fail.status_code == 409, stale_fail.text
        assert stale_done.status_code == 409, stale_done.text
        assert f"builder #{new}" in stale_fail.json()["detail"]
        emit.assert_not_called()
        assert _audit_count(client, admin, "build_fail", a) == 0
        assert _audit_count(client, admin, "build_complete", a) == 0
        job = _get(client, pub, a)
        assert job["status"] == "running" and job["builder_id"] == new
        assert _get(client, pub, b)["status"] == "pending"  # no cascade

        ok = client.post(
            f"/v1/builds/{a}/complete",
            json={"result_archive_url": "/v1/packages/a", "builder_id": new},
            headers=_h(pub),
        )
        assert ok.status_code == 200 and ok.json()["status"] == "succeeded"

    def test_a_report_without_a_reporter_is_unchanged(self, env):
        client, _admin, pub = env
        a, _b, _c = _chain(client, pub, "no-reporter")
        bid = _register(client, pub, "bx")
        assert _claim(client, pub, a, builder_id=bid).status_code == 200
        ok = client.post(f"/v1/builds/{a}/complete", json={}, headers=_h(pub))
        assert ok.status_code == 200 and ok.json()["status"] == "succeeded"

    def test_report_from_a_builder_deleted_mid_job_lands(self, env):
        client, admin, pub = env
        a, _b, _c = _chain(client, pub, "deleted-holder")
        bid = _register(client, pub, "doomed")
        assert _claim(client, pub, a, builder_id=bid).status_code == 200
        r = client.delete(f"/v1/builders/{bid}", headers=_h(admin))
        assert r.status_code == 200, r.text
        assert _get(client, pub, a)["builder_id"] is None

        ok = client.post(
            f"/v1/builds/{a}/complete",
            json={"result_archive_url": "/v1/packages/a", "builder_id": bid},
            headers=_h(pub),
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "succeeded"
        assert _audit_count(client, admin, "build_complete", a) == 1
