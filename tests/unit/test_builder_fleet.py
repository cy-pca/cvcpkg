"""Multi-homed builder fleet config parsing + worker argv + supervisor CLI."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from cvcpkg.builder_fleet import (
    FleetConfigError,
    load_fleet_config,
    parse_fleet_config,
    worker_argv,
)


def test_served_set_helper():
    from cvcpkg.orgs import served_set

    # Home is always first; extras appended, de-duplicated; '' is public.
    assert served_set("", None) == [""]
    assert served_set("cvc", []) == ["cvc"]
    assert served_set("", ["cvc"]) == ["", "cvc"]
    assert served_set("cvc", ["", "cvc"]) == ["cvc", ""]  # home not duplicated
    assert served_set("cvc", ["cypca", "cypca"]) == ["cvc", "cypca"]


def test_parse_multi_server_with_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("TOK_PROD", "ptok")
    monkeypatch.setenv("TOK_DEV", "dtok")
    cfg = parse_fleet_config(
        {
            "name": "catx-03",
            "max_jobs": 4,
            "work_dir": "/var/lib/cvcpkg-builder",
            "labels": ["ramdisk"],
            "servers": [
                {"server": "https://cvcpkg.org/", "token_env": "TOK_PROD", "serve": ["", "cvc"]},
                {
                    "server": "https://pkg.tx.wtf",
                    "token_env": "TOK_DEV",
                    "serve": ["", "cvc"],
                    "max_jobs": 2,
                },
            ],
        }
    )
    assert cfg.name == "catx-03"
    assert [s.host for s in cfg.servers] == ["cvcpkg.org", "pkg.tx.wtf"]
    prod, dev = cfg.servers
    # Per-server name derived from fleet name + host; trailing slash stripped.
    assert prod.name == "catx-03-cvcpkg-org"
    assert prod.server == "https://cvcpkg.org"
    assert prod.token == "ptok"
    assert prod.serve == ("", "cvc")
    assert prod.max_jobs == 4  # inherits fleet default
    assert dev.max_jobs == 2  # per-server override
    # work_dir gets a per-server subdirectory; labels inherit fleet default.
    # (Path is OS-native, so compare the leaf name rather than a POSIX suffix.)
    assert Path(prod.work_dir).name == "cvcpkg-org"
    assert prod.labels == ("ramdisk",)


def test_token_literal_and_missing_env(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    cfg = parse_fleet_config({"servers": [{"server": "https://x", "token": "lit", "serve": [""]}]})
    assert cfg.servers[0].token == "lit"
    with pytest.raises(FleetConfigError, match="token_env"):
        parse_fleet_config({"servers": [{"server": "https://x", "token_env": "NOPE"}]})
    with pytest.raises(FleetConfigError, match="token"):
        parse_fleet_config({"servers": [{"server": "https://x", "serve": [""]}]})


def test_serve_normalization_and_default(monkeypatch):
    monkeypatch.setenv("T", "t")
    # string coerced to list; default is public-only; duplicates removed.
    a = parse_fleet_config({"servers": [{"server": "https://x", "token": "t", "serve": "cvc"}]})
    assert a.servers[0].serve == ("cvc",)
    b = parse_fleet_config({"servers": [{"server": "https://x", "token": "t"}]})
    assert b.servers[0].serve == ("",)
    c = parse_fleet_config(
        {"servers": [{"server": "https://x", "token": "t", "serve": ["cvc", "cvc", ""]}]}
    )
    assert c.servers[0].serve == ("cvc", "")


def test_structural_errors():
    with pytest.raises(FleetConfigError, match="non-empty 'servers'"):
        parse_fleet_config({"servers": []})
    with pytest.raises(FleetConfigError, match="missing 'server'"):
        parse_fleet_config({"servers": [{"token": "t"}]})
    with pytest.raises(FleetConfigError, match="duplicate server"):
        parse_fleet_config(
            {
                "servers": [
                    {"server": "https://x", "token": "t"},
                    {"server": "https://x/", "token": "t"},
                ]
            }
        )


def test_worker_argv_maps_served_set_to_org_and_serve():
    from cvcpkg.builder_fleet import FleetServer

    fs = FleetServer(
        server="https://cvcpkg.org",
        token="secret",
        serve=("", "cvc"),
        name="w1",
        max_jobs=3,
        work_dir="/w/prod",
        labels=("ramdisk",),
    )
    argv = worker_argv(fs)
    # serve[0] is the home --org; the rest become --serve.
    assert argv[:2] == ["builder", "run"]
    assert "--org" in argv and argv[argv.index("--org") + 1] == ""
    assert "--serve" in argv and argv[argv.index("--serve") + 1] == "cvc"
    assert argv[argv.index("--max-jobs") + 1] == "3"
    assert argv[argv.index("--work-dir") + 1] == "/w/prod"
    # pidfile is a Path join → OS-native separators; compare as paths.
    assert Path(argv[argv.index("--pidfile") + 1]) == Path("/w/prod") / "cvcpkg-builder.pid"
    assert argv[argv.index("--label") + 1] == "ramdisk"


def test_cross_platforms_default_and_override_to_argv(monkeypatch):
    monkeypatch.setenv("TP", "p")
    monkeypatch.setenv("TD", "d")
    cfg = parse_fleet_config(
        {
            "name": "phm",
            "cross_platforms": ["haiku"],  # host default
            "servers": [
                {"server": "https://cvcpkg.org", "token_env": "TP", "serve": [""]},
                {
                    "server": "http://10.66.77.207:8420",
                    "token_env": "TD",
                    "serve": [""],
                    "cross_platforms": ["haiku", "wasm"],  # per-server override
                },
            ],
        }
    )
    prod, dev = cfg.servers
    assert prod.cross_platforms == ("haiku",)
    assert dev.cross_platforms == ("haiku", "wasm")

    def cps(fs):
        argv = worker_argv(fs)
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--cross-platform"]

    assert cps(prod) == ["haiku"]
    assert cps(dev) == ["haiku", "wasm"]
    # Absent config → no flag at all.
    plain = parse_fleet_config(
        {"servers": [{"server": "https://x", "token": "t", "serve": [""]}]}
    ).servers[0]
    assert plain.cross_platforms == ()
    assert "--cross-platform" not in worker_argv(plain)


def test_load_from_yaml_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TOK", "y")
    p = tmp_path / "fleet.yaml"
    p.write_text(
        textwrap.dedent(
            """
            name: unit-fleet
            servers:
              - server: https://a.example
                token_env: TOK
                serve: ["", "cvc"]
            """
        )
    )
    cfg = load_fleet_config(p)
    assert cfg.name == "unit-fleet"
    assert cfg.servers[0].serve == ("", "cvc")
    with pytest.raises(FleetConfigError, match="not found"):
        load_fleet_config(tmp_path / "missing.yaml")


def test_fleet_cli_dry_run_masks_token(tmp_path, monkeypatch):
    pytest.importorskip("click")
    from click.testing import CliRunner

    from cvcpkg.cli._builder import builder_fleet

    monkeypatch.setenv("TOK", "supersecret")
    p = tmp_path / "fleet.yaml"
    p.write_text(
        "name: f\nservers:\n  - server: https://a.example\n    token_env: TOK\n    serve: ['', cvc]\n"
    )
    res = CliRunner().invoke(builder_fleet, ["--config", str(p), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "supersecret" not in res.output  # token is masked
    assert "***" in res.output
    assert "a.example" in res.output
    assert "serves ['', 'cvc']" in res.output


# ── supervisor: how workers are started ─────────────────────────


def _full_fleet_server():
    from cvcpkg.builder_fleet import FleetServer

    # Every field worker_argv can emit, so the parse test below covers each flag.
    return FleetServer(
        server="https://cvcpkg.org",
        token="secret",
        serve=("", "cvc", "cypca"),
        name="phm-cvcpkg-org",
        max_jobs=3,
        work_dir="/w/prod",
        labels=("ramdisk", "big"),
        platform="linux",
        arch="x86_64",
        capabilities=("cuda",),
        cross_platforms=("haiku",),
        auto_capabilities=False,
        advertise_free_disk=False,
    )


def test_worker_argv_is_accepted_by_builder_run():
    """Every flag the fleet passes must be one `builder run` accepts, under the
    name it accepts it, or each worker dies on argument parsing."""
    from cvcpkg.cli._builder import builder_run

    fs = _full_fleet_server()
    argv = worker_argv(fs)
    assert argv[:2] == ["builder", "run"]
    ctx = builder_run.make_context("run", argv[2:])
    p = ctx.params
    assert p["server"] == "https://cvcpkg.org"
    assert p["token"] == "secret"
    assert p["name"] == "phm-cvcpkg-org"
    assert p["org_slug"] == ""
    assert p["max_jobs"] == 3
    assert list(p["labels"]) == ["ramdisk", "big"]
    assert p["platform"] == "linux"
    assert p["arch"] == "x86_64"
    assert p["work_dir"] == "/w/prod"
    assert Path(p["pidfile"]) == Path("/w/prod") / "cvcpkg-builder.pid"
    # The remaining flags parse under whatever parameter names `builder run` uses.
    values = {k: (list(v) if isinstance(v, tuple) else v) for k, v in p.items()}
    assert ["cvc", "cypca"] in values.values()  # --serve
    assert ["cuda"] in values.values()  # --capability
    assert ["haiku"] in values.values()  # --cross-platform


def _run_supervisor(monkeypatch, fleet):
    """Run _supervise_fleet with Popen/signal faked; stop once all have spawned.

    Returns the recorded (argv, env) of each spawn.
    """
    import signal
    import subprocess

    from cvcpkg.cli import _builder

    spawned: list[tuple[list[str], dict]] = []
    handlers: dict = {}

    class _FakeProc:
        pid = 4242
        returncode = None

        def poll(self):
            return None

        def send_signal(self, _sig):
            pass

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    def _fake_popen(argv, env=None, **_kw):
        spawned.append((list(argv), dict(env) if env is not None else None))
        if len(spawned) == len(fleet.servers):
            handlers[signal.SIGTERM](signal.SIGTERM, None)  # stop the supervisor
        return _FakeProc()

    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    _builder._supervise_fleet(fleet, restart_delay=0.0)
    return spawned


def test_supervisor_spawns_frozen_binary_without_dash_m(monkeypatch):
    """The single binary is cvcpkg, not Python: `<binary> -m cvcpkg ...` is
    rejected ("No such option '-m'") and the fleet crash-loops every worker."""
    from cvcpkg.builder_fleet import FleetConfig

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/home/u/.local/bin/cvcpkg")
    fs = _full_fleet_server()
    spawned = _run_supervisor(monkeypatch, FleetConfig(name="phm", servers=[fs]))

    assert len(spawned) == 1
    argv, env = spawned[0]
    assert argv == ["/home/u/.local/bin/cvcpkg", *worker_argv(fs)]
    assert "-m" not in argv
    # Client CLI even in the combined binary; own unpack, independent of ours.
    assert env["CVCPKG_ENTRY"] == "client"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_supervisor_spawns_python_dash_m_from_a_pip_install(monkeypatch):
    from cvcpkg.builder_fleet import FleetConfig, FleetServer

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CVCPKG_ENTRY", raising=False)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    servers = [
        FleetServer(server="https://a.example", token="t1", serve=("",), name="a"),
        FleetServer(server="https://b.example", token="t2", serve=("",), name="b"),
    ]
    spawned = _run_supervisor(monkeypatch, FleetConfig(name="f", servers=servers))

    assert [argv for argv, _ in spawned] == [
        [sys.executable, "-m", "cvcpkg", *worker_argv(fs)] for fs in servers
    ]
    for _, env in spawned:
        assert "CVCPKG_ENTRY" not in env
        assert "PYINSTALLER_RESET_ENVIRONMENT" not in env


def test_supervisor_reaps_the_process_group_of_an_exited_worker(monkeypatch):
    """The frozen binary runs a worker as launcher + interpreter.  If the
    launcher dies alone, the interpreter keeps building and holds the worker's
    pidfile, so every respawn would exit on the single-instance guard.  The
    supervisor starts each worker in its own group and stops that group before
    respawning."""
    import os
    import signal
    import subprocess

    from cvcpkg.builder_fleet import FleetConfig, FleetServer
    from cvcpkg.cli import _builder

    killed: list = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False)
    handlers: dict = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    spawns: list = []

    class _Proc:
        def __init__(self, pid, rc):
            self.pid, self.returncode = pid, rc

        def poll(self):
            return self.returncode

        def send_signal(self, _sig):
            pass

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    def _popen(argv, env=None, **kw):
        spawns.append(kw)
        if len(spawns) == 1:
            return _Proc(5001, -9)  # its launcher is already gone
        handlers[signal.SIGTERM](signal.SIGTERM, None)  # stop after the respawn
        return _Proc(5002, None)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    fs = FleetServer(server="https://a.example", token="t", serve=("",), name="a")
    _builder._supervise_fleet(FleetConfig(name="f", servers=[fs]), restart_delay=0.0)

    assert killed == [(5001, signal.SIGTERM)]
    assert len(spawns) == 2
    assert all(kw.get("start_new_session") for kw in spawns)
