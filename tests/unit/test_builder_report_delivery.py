"""A builder reports honestly to its server, and keeps reporting.

Two ways a running builder stopped reporting without anything saying so:

* **Job reports.**  ``/complete`` and ``/fail`` were posted unchecked.  httpx
  does not raise on a 4xx, so a report the server refused (409: the job was
  cancelled, timed out, or moved to another holder) still printed
  "Completed".  And a timeout on ``/complete`` -- after the bundle was
  published -- raised into the job's own failure handler, which then reported
  a good build as failed.  Both left the job ``running`` until the build
  timeout.  Now a report that gets no answer is asked again, a refusal is
  logged as one, and a failing report never turns into a different report.

* **Registration.**  A server that forgets the builder (an admin unregister, a
  database reset or restore) answers its heartbeat with 404 -- and so does
  every other call, forever.  The builder logged "heartbeat failed: 404" once a
  minute and never came back.  It now registers again and adopts the new id.

The server is faked at the ``httpx.Client`` layer.
"""

from __future__ import annotations

import io
import json
import signal
import tarfile
import textwrap
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    monkeypatch.setattr(_builder, "_REPORT_RETRY_DELAY", 0.0, raising=False)


def _recipe_bundle(name: str) -> bytes:
    recipe_yaml = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
        """
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="recipe.yaml")
        info.size = len(recipe_yaml)
        tar.addfile(info, io.BytesIO(recipe_yaml))
    return buf.getvalue()


class _Resp:
    def __init__(self, status: int = 200, data=None, content: bytes = b""):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = content
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class _Server:
    """Hands out one job; answers each verb from a script.

    ``answers[verb]`` is a list consumed one entry per request; an entry is a
    status code (an int) or an exception to raise.  The last entry repeats.
    """

    def __init__(self, answers: dict | None = None, register_ids=(7,)):
        self.job = {"id": 42, "recipe_name": "zlib", "platform": "linux", "arch": "x86_64"}
        self.answers = answers or {}
        self.register_ids = list(register_ids)
        self.posts: list[tuple[str, dict]] = []  # (url, json)
        self.gets: list[str] = []
        self.handed_out = False
        self.bundle = _recipe_bundle("zlib")

    def _answer(self, verb: str) -> int:
        script = self.answers.get(verb)
        if not script:
            return 200
        entry = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def sent(self, verb: str) -> list[dict]:
        return [j for u, j in self.posts if u.rstrip("/").rsplit("/", 1)[-1] == verb]

    def client_cls(self):
        server = self

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def close(self):
                pass

            def post(self, url, json=None, **k):
                server.posts.append((url, json))
                verb = url.rstrip("/").rsplit("/", 1)[-1]
                if verb == "register":
                    ids = server.register_ids
                    return _Resp(200, {"id": ids.pop(0) if len(ids) > 1 else ids[0]})
                status = server._answer(verb)
                if verb == "claim":
                    return _Resp(status, {"id": 42, "status": "running"})
                return _Resp(status, {"detail": f"scripted {status}"} if status >= 400 else {})

            def patch(self, url, **k):
                return _Resp(200, {})

            def get(self, url, **k):
                server.gets.append(url)
                if url.endswith("/v1/recipes/zlib"):
                    return _Resp(200, content=server.bundle)
                if url.endswith(("/next-job", "/next-claimable")):
                    if not server.handed_out:
                        server.handed_out = True
                        return _Resp(200, server.job)
                return _Resp(204)

            def delete(self, url, **k):
                return _Resp(200, {})

        return _Client


def _invoke(monkeypatch, tmp_path, server: _Server, *, build_ok: bool = True):
    monkeypatch.setattr(httpx, "Client", server.client_cls())

    def fake_pack(recipe_dir, *, output_dir, **k):
        if not build_ok:
            raise RuntimeError("compiler exploded")
        out = Path(output_dir) / "zlib-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    monkeypatch.setattr("cvcpkg.builder.pack_recipe", fake_pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            "t",
            "--name",
            "w-report",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-websocket",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--max-jobs",
            "2",
            "--exit-when-empty",
            "--max-runtime",
            "60",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    return result.output


# ── Job reports ──────────────────────────────────────────────────


def test_a_refused_completion_is_not_reported_as_completed(monkeypatch, tmp_path):
    server = _Server({"complete": [409]})
    out = _invoke(monkeypatch, tmp_path, server)

    assert len(server.sent("complete")) == 1, "a refusal is final: asking again changes nothing"
    assert server.sent("fail") == [], "a refused completion must not become a failure"
    assert "Completed: zlib" not in out
    assert "refused the complete report (409)" in out
    assert "did not record it as completed (refused)" in out


def test_a_timeout_on_complete_is_asked_again_not_turned_into_a_failure(monkeypatch, tmp_path):
    server = _Server({"complete": [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), 200]})
    out = _invoke(monkeypatch, tmp_path, server)

    assert len(server.sent("complete")) == 3
    assert server.sent("fail") == [], "a published build was reported as failed"
    assert "Completed: zlib" in out


def test_a_proxy_502_on_complete_is_asked_again(monkeypatch, tmp_path):
    server = _Server({"complete": [502, 503, 200]})
    out = _invoke(monkeypatch, tmp_path, server)

    assert len(server.sent("complete")) == 3
    assert server.sent("fail") == []
    assert "Completed: zlib" in out


def test_a_completion_that_never_gets_through_says_so_and_does_not_fail_the_job(
    monkeypatch, tmp_path
):
    server = _Server({"complete": [httpx.ConnectError("down")]})
    out = _invoke(monkeypatch, tmp_path, server)

    assert len(server.sent("complete")) == _builder._REPORT_ATTEMPTS
    assert server.sent("fail") == []
    assert "Completed: zlib" not in out
    assert "could not deliver the complete report" in out


def test_a_failed_build_retries_its_fail_report_and_keeps_the_identity(monkeypatch, tmp_path):
    server = _Server({"fail": [httpx.ReadTimeout("slow"), 200]})
    out = _invoke(monkeypatch, tmp_path, server, build_ok=False)

    fails = server.sent("fail")
    assert len(fails) == 2
    assert all(f["builder_id"] == 7 and "compiler exploded" in f["error_message"] for f in fails)
    assert "Failed: zlib" in out
    assert "did not record it" not in out


def test_a_refused_fail_report_is_logged_as_unrecorded(monkeypatch, tmp_path):
    server = _Server({"fail": [409]})
    out = _invoke(monkeypatch, tmp_path, server, build_ok=False)

    assert len(server.sent("fail")) == 1
    assert "refused the fail report (409)" in out
    assert "the server did not record it: refused" in out


# ── Registration ─────────────────────────────────────────────────


def test_a_builder_the_server_forgot_registers_again_and_adopts_the_new_id(monkeypatch, tmp_path):
    """The first heartbeat 404s (the server dropped the row); the builder
    registers again, gets id 9, and from then on polls and reports as #9."""
    server = _Server({"heartbeat": [404, 200]}, register_ids=(7, 9))
    out = _invoke(monkeypatch, tmp_path, server)

    registers = [u for u, _ in server.posts if u.endswith("/register")]
    assert len(registers) == 2, out
    assert "Re-registered as builder #9" in out
    polls = [g for g in server.gets if g.endswith("/next-job")]
    assert polls and all("/builders/9/" in g for g in polls), polls
    assert server.sent("claim") == [{"builder_id": 9}]
    assert server.sent("complete")[0]["builder_id"] == 9


def test_a_rejected_token_is_named_not_left_as_a_bare_status(monkeypatch, tmp_path):
    server = _Server({"heartbeat": [401]})
    out = _invoke(monkeypatch, tmp_path, server)

    assert "heartbeat rejected: 401" in out
    assert "token revoked" in out
    assert (
        len([u for u, _ in server.posts if u.endswith("/register")]) == 1
    ), "a revoked token is not cured by registering again"
