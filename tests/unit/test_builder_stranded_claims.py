"""A restarted builder fails the jobs its previous process never reported.

Dev job 25560 (grpc, netbsd, 2026-11-27): the worker died mid-build, the fleet
supervisor restarted it five seconds later, and the new worker swept the job's
directories ("reclaimed 2 orphaned job dir(s)") without telling the server.
The server, which had never seen the builder go offline, kept the job
``running`` until its 2 h build timeout, and cancelled every job of the DAG
that depended on it only then.

Now each claim is recorded in the work dir (cvcpkg.builder_claims) until the
job's thread is done with it, and a builder that starts with records left over
reports those jobs failed before it takes any work.  The server is faked at the
``httpx.Client`` layer, as in test_builder_report_delivery.
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

from cvcpkg.builder_claims import CLAIMS_DIR_NAME, ClaimLedger
from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run

SERVER = "http://test"
NAME = "netbsd-build-test"


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
    """Registers the builder as #7; hands out job 42 (zlib) once, if asked to.

    ``fail_answers`` scripts the answers to ``/fail`` reports: a status code or
    an exception per request, the last one repeating.
    """

    def __init__(self, *, hand_out_job: bool = False, fail_answers=None):
        self.job = {"id": 42, "recipe_name": "zlib", "platform": "linux", "arch": "x86_64"}
        self.handed_out = not hand_out_job
        self.fail_answers = list(fail_answers or [200])
        self.posts: list[tuple[str, dict]] = []
        self.bundle = _recipe_bundle("zlib")

    def posted(self, path_suffix: str) -> list[dict]:
        return [j for u, j in self.posts if u.endswith(path_suffix)]

    def order(self) -> list[str]:
        return [u.removeprefix(SERVER) for u, _ in self.posts]

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
                if url.endswith("/register"):
                    return _Resp(200, {"id": 7})
                if url.endswith("/claim"):
                    return _Resp(200, {"id": 42, "status": "running"})
                if url.endswith("/fail"):
                    answers = server.fail_answers
                    entry = answers.pop(0) if len(answers) > 1 else answers[0]
                    if isinstance(entry, BaseException):
                        raise entry
                    return _Resp(entry, {"detail": f"scripted {entry}"} if entry >= 400 else {})
                return _Resp(200, {})

            def patch(self, url, **k):
                return _Resp(200, {})

            def get(self, url, **k):
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


def _invoke(monkeypatch, tmp_path: Path, server: _Server, *, pack=None) -> str:
    monkeypatch.setattr(httpx, "Client", server.client_cls())

    def fake_pack(recipe_dir, *, output_dir, **k):
        if pack is not None:
            pack()
        out = Path(output_dir) / "zlib-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    monkeypatch.setattr("cvcpkg.builder.pack_recipe", fake_pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            SERVER,
            "--token",
            "t",
            "--name",
            NAME,
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-websocket",
            "--no-auto-capabilities",
            "--no-free-disk",
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


def _ledger(tmp_path: Path, *, server: str = SERVER, builder: str = NAME) -> ClaimLedger:
    return ClaimLedger(tmp_path / "wd", server=server, builder=builder)


# ── At startup ───────────────────────────────────────────────────


def test_a_restarted_builder_fails_the_job_its_previous_process_left_running(monkeypatch, tmp_path):
    # What the dead worker left: its claim of 25560, made as builder #13.
    assert _ledger(tmp_path).record(25560, identity={"builder_id": 13}, recipe="grpc")
    server = _Server(hand_out_job=True)

    out = _invoke(monkeypatch, tmp_path, server)

    [report] = server.posted("/v1/builds/25560/fail")
    # Named as the claim named it, so the server takes it from this builder.
    assert report["builder_id"] == 13
    why = report["error_message"]
    assert f"builder {NAME} restarted while running this job" in why
    assert "grpc" in why and "Retry the job" in why
    # Reported before this process took any work of its own.
    order = server.order()
    assert order.index("/v1/builds/25560/fail") < order.index("/v1/builds/42/claim")
    assert "[25560] grpc: claimed by this builder's previous process" in out
    assert _ledger(tmp_path).stranded() == []


def test_a_report_the_server_refuses_drops_the_record(monkeypatch, tmp_path):
    """409: the job already ended (timed out, say).  Nothing left to report."""
    _ledger(tmp_path).record(25560, identity={"builder_id": 13}, recipe="grpc")
    server = _Server(fail_answers=[409])

    out = _invoke(monkeypatch, tmp_path, server)

    assert len(server.posted("/v1/builds/25560/fail")) == 1
    assert "[25560] the server had already ended it" in out
    assert _ledger(tmp_path).stranded() == []


def test_a_report_that_gets_no_answer_is_kept_for_the_next_start(monkeypatch, tmp_path):
    _ledger(tmp_path).record(25560, identity={"builder_id": 13}, recipe="grpc")
    server = _Server(fail_answers=[httpx.ConnectError("connection refused")])

    _invoke(monkeypatch, tmp_path, server)

    assert len(server.posted("/v1/builds/25560/fail")) == _builder._REPORT_ATTEMPTS
    assert [r["job_id"] for r in _ledger(tmp_path).stranded()] == [25560]


def test_records_of_another_builder_or_server_are_not_reported(monkeypatch, tmp_path):
    """Builders without --work-dir share one directory; each reports only its own."""
    _ledger(tmp_path, builder="someone-else").record(1, identity={"builder_id": 2})
    _ledger(tmp_path, server="http://elsewhere").record(3, identity={"builder_id": 4})
    server = _Server()

    _invoke(monkeypatch, tmp_path, server)

    assert server.posted("/fail") == []
    assert [r["job_id"] for r in _ledger(tmp_path, builder="someone-else").stranded()] == [1]
    assert [r["job_id"] for r in _ledger(tmp_path, server="http://elsewhere").stranded()] == [3]


def test_nothing_is_reported_when_nothing_was_left(monkeypatch, tmp_path):
    server = _Server()
    _invoke(monkeypatch, tmp_path, server)
    assert server.posted("/fail") == []


# ── While a job runs ─────────────────────────────────────────────


@pytest.mark.parametrize("build_ok", [True, False], ids=["succeeds", "fails"])
def test_a_job_is_on_record_while_it_runs_and_off_it_once_reported(monkeypatch, tmp_path, build_ok):
    seen: list[list[dict]] = []

    def pack():
        seen.append(_ledger(tmp_path).stranded())
        if not build_ok:
            raise RuntimeError("compiler exploded")

    server = _Server(hand_out_job=True)
    _invoke(monkeypatch, tmp_path, server, pack=pack)

    [during] = seen
    assert [(r["job_id"], r["identity"], r["recipe"]) for r in during] == [
        (42, {"builder_id": 7}, "zlib")
    ]
    verb = "complete" if build_ok else "fail"
    assert len(server.posted(f"/v1/builds/42/{verb}")) == 1
    assert _ledger(tmp_path).stranded() == []


# ── The ledger itself ────────────────────────────────────────────


def test_the_ledger_round_trips_and_keys_by_server_and_builder(tmp_path):
    mine = ClaimLedger(tmp_path, server="http://s/", builder="b")
    assert mine.record(9, identity={"claimant": "b"}, recipe="zlib")
    assert mine.record(5, identity={"builder_id": 1})
    # A trailing slash on the server URL is the same server.
    same = ClaimLedger(tmp_path, server="http://s", builder="b")
    assert [r["job_id"] for r in same.stranded()] == [5, 9]
    assert ClaimLedger(tmp_path, server="http://s", builder="other").stranded() == []
    same.forget(5)
    same.forget(5)  # already gone: no error
    [rec] = mine.stranded()
    assert (rec["job_id"], rec["identity"], rec["recipe"]) == (9, {"claimant": "b"}, "zlib")


def test_an_unreadable_record_is_dropped_and_a_half_written_one_cleared(tmp_path):
    ledger = ClaimLedger(tmp_path, server=SERVER, builder=NAME)
    ledger.record(1, identity={"builder_id": 2})
    good = next((tmp_path / CLAIMS_DIR_NAME).glob("1-*.json"))
    key = good.name.split("-", 1)[1]
    bad = tmp_path / CLAIMS_DIR_NAME / f"2-{key}"
    bad.write_text("{not json", encoding="utf-8")
    half = tmp_path / CLAIMS_DIR_NAME / f"3-{key}.123.tmp"
    half.write_text("{", encoding="utf-8")

    assert [r["job_id"] for r in ledger.stranded()] == [1]
    assert not bad.exists() and not half.exists()


def test_a_record_that_cannot_be_written_never_raises(tmp_path):
    blocker = tmp_path / "wd"
    blocker.write_text("a file where the work dir should be", encoding="utf-8")
    ledger = ClaimLedger(blocker, server=SERVER, builder=NAME)
    assert ledger.record(1, identity={"builder_id": 2}) is False
    ledger.forget(1)
    assert ledger.stranded() == []
