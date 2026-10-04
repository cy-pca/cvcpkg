"""Builder tokens stay out of argv and out of every build's environment.

A builder runs third-party code -- each recipe's build and test scripts --
with its own environment, and that environment ends up in build logs the
builder streams to the server.  Before this:

* ``builder fleet`` put each worker's token on the worker's command line
  (``--token``), where ``ps`` / ``/proc/<pid>/cmdline`` show it to every local
  user, and copied the supervisor's whole environment -- every server's
  ``token_env`` variable -- into every worker;
* ``builder run`` left ``CVCPKG_TOKEN`` (and any env-file token) in
  ``os.environ``, so every build script it started inherited it.

These tests run the real ``builder run`` loop against a fake server and look at
what a child process started by a build actually receives.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time

import httpx
import pytest
import yaml
from click.testing import CliRunner

from cvcpkg.tokenenv import SCRUB_NAMES_ENV, is_token_env_name, scrub_token_env

OWN = "own-builder-secret-1"
OTHER = "other-server-secret-2"
PROD = "prod-server-secret-3"


# -- cvcpkg.tokenenv -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "is_token"),
    [
        ("CVCPKG_TOKEN", True),
        ("CVCPKG_TOKEN_PROD", True),
        ("CVCPKG_ADMIN_TOKEN", True),
        ("CVCPKG_SERVER_CACHE_TOKEN", True),
        ("cvcpkg_token", True),  # Windows environments are case-insensitive
        ("CVCPKG_SERVER_URL", False),
        ("GITHUB_TOKEN", False),  # not cvcpkg's to withhold
        ("TOKEN", False),
    ],
)
def test_is_token_env_name(name, is_token):
    assert is_token_env_name(name) is is_token


def test_scrub_by_name_pattern_explicit_names_and_value():
    env = {
        "CVCPKG_TOKEN": OWN,
        "CVCPKG_TOKEN_DEV": OTHER,
        "MY_PROD_TOK": PROD,  # named by the caller
        "ALIAS": OWN,  # holds a secret under an arbitrary name
        "GITHUB_TOKEN": "gh",
        "PATH": "/bin",
        "EMPTY": "",
    }
    removed = scrub_token_env(env, names=["my_prod_tok"], secrets=[OWN, ""])
    assert env == {"GITHUB_TOKEN": "gh", "PATH": "/bin", "EMPTY": ""}
    assert set(removed) == {"CVCPKG_TOKEN", "CVCPKG_TOKEN_DEV", "MY_PROD_TOK", "ALIAS"}
    assert removed["CVCPKG_TOKEN_DEV"] == OTHER


def test_scrub_works_on_os_environ(monkeypatch):
    monkeypatch.setenv("CVCPKG_TOKEN_UNITTEST", OTHER)
    removed = scrub_token_env(os.environ)
    assert "CVCPKG_TOKEN_UNITTEST" not in os.environ
    assert removed["CVCPKG_TOKEN_UNITTEST"] == OTHER


# -- build and test script environments ---------------------------------------


def _ctx(tmp_path):
    from cvcpkg.builder import BuildContext, Recipe

    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
                "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
                "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                "test": {"script": "test.sh"},
            }
        )
    )
    (d / "test.sh").write_text('#!/usr/bin/env bash\nenv > "$CVC_INSTALL_DIR/test-env.txt"\n')
    r = Recipe.load(d)
    work = tmp_path / "work"
    for sub in ("src", "build", "install"):
        (work / sub).mkdir(parents=True)
    return (
        BuildContext(
            recipe=r,
            platform="linux",
            config="release",
            link="shared",
            prefix=tmp_path / "prefix",
            source_dir=work / "src",
            build_dir=work / "build",
            install_dir=work / "install",
            work_dir=work,
        ),
        r,
    )


def test_build_script_env_has_no_cvcpkg_token(tmp_path, monkeypatch):
    """A local `cvcpkg build` with CVCPKG_TOKEN exported: the recipe's build
    script still does not get it."""
    from cvcpkg.builder import _build_env

    monkeypatch.setenv("CVCPKG_TOKEN", OWN)
    monkeypatch.setenv("CVCPKG_TOKEN_DEV", OTHER)
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    ctx, r = _ctx(tmp_path)
    env = _build_env(ctx, r.build_matrix[0])
    assert OWN not in env.values() and OTHER not in env.values()
    assert not [k for k in env if is_token_env_name(k)]
    assert env["GITHUB_TOKEN"] == "gh"
    assert env["CVC_COMPONENT"] == "tp"  # still a real build env


@pytest.mark.skipif(sys.platform == "win32", reason="runs a bash test script")
def test_test_script_env_has_no_cvcpkg_token(tmp_path, monkeypatch):
    from cvcpkg.builder import run_test

    monkeypatch.setenv("CVCPKG_TOKEN", OWN)
    monkeypatch.setenv("CVCPKG_ADMIN_TOKEN", OTHER)
    ctx, _ = _ctx(tmp_path)
    run_test(ctx)
    dumped = (ctx.install_dir / "test-env.txt").read_text()
    assert OWN not in dumped and OTHER not in dumped
    assert "CVC_INSTALL_DIR=" in dumped


# -- the real `builder run` loop ----------------------------------------------


class _Resp:
    def __init__(self, status=200, data=None, content=b"", text=""):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = content
        self.text = text

    def json(self):
        return self._data


def _bundle(name: str) -> bytes:
    data = yaml.safe_dump(
        {"schema_version": 1, "recipe": {"name": name, "upstream_version": "1.0"}}
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"{name}/recipe.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Server:
    """One job (``rleak``) dispatched to builder #7; records auth headers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = "dispatched"
        self.auth: set[str] = set()
        self.claims = 0

    def client_cls(self):
        server = self

        class _C:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def close(self):
                pass

            def _seen(self, headers):
                with server.lock:
                    server.auth.add((headers or {}).get("Authorization", ""))

            def post(self, url, headers=None, **k):
                self._seen(headers)
                if url.endswith("/register"):
                    return _Resp(200, {"id": 7})
                if url.endswith("/v1/builds/1/claim"):
                    with server.lock:
                        server.claims += 1
                        server.status = "running"
                    return _Resp(200, {"id": 1, "status": "running", "builder_id": 7})
                if url.endswith("/v1/builds/1/fail") or url.endswith("/v1/builds/1/complete"):
                    with server.lock:
                        server.status = "done"
                return _Resp(200, {})

            def patch(self, url, headers=None, **k):
                return _Resp(200, {})

            def get(self, url, headers=None, params=None, **k):
                self._seen(headers)
                if url.endswith("/next-job"):
                    with server.lock:
                        if server.status == "dispatched":
                            return _Resp(
                                200,
                                {
                                    "id": 1,
                                    "recipe_name": "rleak",
                                    "platform": "linux",
                                    "arch": "x86_64",
                                    "org_slug": "",
                                    "status": "dispatched",
                                },
                            )
                    time.sleep(0.05)
                    return _Resp(204)
                if url.endswith("/v1/recipes/rleak"):
                    return _Resp(200, content=_bundle("rleak"))
                return _Resp(404)

            def delete(self, url, headers=None, **k):
                return _Resp(200, {})

        return _C


@pytest.mark.parametrize("token_source", ["environment", "env-file"])
def test_builder_run_keeps_every_token_out_of_its_builds(monkeypatch, tmp_path, token_source):
    """The token reaches the server, and nothing the build starts can see it --
    nor the fleet's other token variables, however they are named."""
    import cvcpkg.builder as builder_mod
    from cvcpkg.builder import _script_base_env
    from cvcpkg.cli import cli

    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    server = _Server()
    monkeypatch.setattr(httpx, "Client", server.client_cls())

    # What a fleet worker's environment can hold: its own token, another
    # server's token re-read from an env file, a token under a custom
    # token_env name (announced by the supervisor), and a copy of its own.
    monkeypatch.setenv("CVCPKG_TOKEN_DEV", OTHER)
    monkeypatch.setenv("MY_PROD_TOK", PROD)
    monkeypatch.setenv(SCRUB_NAMES_ENV, "MY_PROD_TOK")
    monkeypatch.setenv("ALIAS_OF_MINE", OWN)
    monkeypatch.setenv("GITHUB_TOKEN", "third-party")
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    root_args: list[str] = []
    if token_source == "environment":
        monkeypatch.setenv("CVCPKG_TOKEN", OWN)
    else:
        env_file = tmp_path / "builder.env"
        env_file.write_text(f"CVCPKG_TOKEN={OWN}\n")
        if sys.platform != "win32":
            env_file.chmod(0o600)
        root_args = ["--env-file", str(env_file)]

    seen: dict = {}

    def fake_pack(recipe_dir, **kw):
        child = subprocess.run(
            [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
            capture_output=True,
            text=True,
            check=True,
        )
        seen["child_env"] = json.loads(child.stdout)
        seen["script_env"] = _script_base_env()
        raise RuntimeError("stop after looking")

    monkeypatch.setattr(builder_mod, "pack_recipe", fake_pack)
    argv = root_args + [
        "builder",
        "run",
        "--server",
        "http://test",
        "--name",
        "leak-probe",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--no-websocket",
        "--no-auto-capabilities",
        "--no-free-disk",
        "--work-dir",
        str(tmp_path / "wd"),
        "--recipe-cache-dir",
        str(tmp_path / "rc"),
        "--pidfile",
        str(tmp_path / "b.pid"),
        "--max-runtime",
        "2",
    ]
    assert OWN not in argv
    result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 0, result.output
    assert "child_env" in seen, "the job never reached the build step:\n" + result.output
    # The builder still authenticates with its token ...
    assert server.auth == {f"Bearer {OWN}"}, server.auth
    # ... and nothing a build starts can see any of them.
    for env in (seen["child_env"], seen["script_env"]):
        blob = json.dumps(env)
        for secret in (OWN, OTHER, PROD):
            assert secret not in blob
        assert not [k for k in env if is_token_env_name(k)]
        assert "MY_PROD_TOK" not in env and SCRUB_NAMES_ENV not in env
        assert env.get("GITHUB_TOKEN") == "third-party"
    assert OWN not in result.output
    # Restored for whatever embeds the CLI (env-file values go away again).
    if token_source == "environment":
        assert os.environ.get("CVCPKG_TOKEN") == OWN
    else:
        assert "CVCPKG_TOKEN" not in os.environ
    assert os.environ.get("CVCPKG_TOKEN_DEV") == OTHER
    assert os.environ.get(SCRUB_NAMES_ENV) == "MY_PROD_TOK"


def test_builder_run_drains_on_ctrl_break(monkeypatch, tmp_path):
    """`builder fleet` stops a Windows worker with CTRL_BREAK_EVENT, which
    arrives as SIGBREAK: builder run must treat it like Ctrl+C (drain), not
    die with its jobs."""
    from cvcpkg.cli._builder import builder_run

    registered: dict = {}
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(signal, "signal", lambda sig, h: registered.__setitem__(sig, h))
    server = _Server()
    server.status = "done"  # nothing to build
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            OWN,
            "--name",
            "brk",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-websocket",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--work-dir",
            str(tmp_path / "wd"),
            "--pidfile",
            str(tmp_path / "b.pid"),
            "--max-runtime",
            "0.3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert registered.get(21) is registered.get(signal.SIGINT) is not None
