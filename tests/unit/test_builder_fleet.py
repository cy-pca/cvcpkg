"""Multi-homed builder fleet config parsing + worker argv + supervisor CLI."""

from __future__ import annotations

import json
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


def test_worker_argv_is_accepted_by_builder_run(monkeypatch):
    """Every flag the fleet passes must be one `builder run` accepts, under the
    name it accepts it, or each worker dies on argument parsing.  The token is
    not one of them: it arrives as CVCPKG_TOKEN (see worker_env)."""
    from cvcpkg.builder_fleet import worker_env
    from cvcpkg.cli._builder import builder_run

    fs = _full_fleet_server()
    argv = worker_argv(fs)
    assert argv[:2] == ["builder", "run"]
    assert "--token" not in argv and "secret" not in argv
    monkeypatch.setenv("CVCPKG_TOKEN", worker_env(fs, [fs], {})["CVCPKG_TOKEN"])
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
    monkeypatch.setattr(sys, "platform", "linux")  # the POSIX path, on every CI OS
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


# ── tokens: env, not argv; each worker only its own ─────────────


def test_parse_records_which_variable_each_token_came_from(monkeypatch):
    monkeypatch.setenv("TP", "p")
    cfg = parse_fleet_config(
        {
            "servers": [
                {"server": "https://a", "token_env": "TP"},
                {"server": "https://b", "token": "literal"},
            ]
        }
    )
    assert [s.token_env for s in cfg.servers] == ["TP", ""]
    # Never in a repr (a log line, a traceback).
    assert "'p'" not in repr(cfg.servers[0]) and "literal" not in repr(cfg.servers[1])


def test_worker_env_holds_only_the_workers_own_token():
    from cvcpkg.builder_fleet import FleetServer, worker_env
    from cvcpkg.tokenenv import SCRUB_NAMES_ENV

    a = FleetServer(server="https://a", token="tok-a", serve=("",), name="a", token_env="TA")
    b = FleetServer(server="https://b", token="tok-b", serve=("",), name="b", token_env="TB")
    base = {
        "TA": "tok-a",
        "TB": "tok-b",
        "COPY": "tok-b",  # another server's token under an unrelated name
        "CVCPKG_TOKEN": "stray",
        "CVCPKG_ADMIN_TOKEN": "admin",
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "gh",
    }
    env = worker_env(a, [a, b], base)
    assert env == {
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "gh",
        "CVCPKG_TOKEN": "tok-a",
        SCRUB_NAMES_ENV: "TA,TB",
    }
    assert base["TB"] == "tok-b"  # the supervisor's own environment is untouched


def test_supervisor_puts_no_token_on_argv_and_one_in_each_env(monkeypatch):
    """The whole fleet's secrets, as the supervisor's environment holds them:
    no worker sees one on its command line, and each sees exactly its own."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("CVCPKG_TOKEN_PROD", "prod-secret-1")
    monkeypatch.setenv("DEV_TOK", "dev-secret-2")
    monkeypatch.setenv("CVCPKG_TOKEN", "stray-secret-3")
    monkeypatch.setenv("COPY_OF_PROD", "prod-secret-1")
    monkeypatch.setenv("UNRELATED_SETTING", "keep-me")
    cfg = parse_fleet_config(
        {
            "name": "two-servers",
            "servers": [
                {"server": "https://cvcpkg.org", "token_env": "CVCPKG_TOKEN_PROD"},
                {"server": "http://10.0.0.1:8420", "token_env": "DEV_TOK"},
            ],
        }
    )
    spawned = _run_supervisor(monkeypatch, cfg)

    secrets = {"prod-secret-1", "dev-secret-2", "stray-secret-3"}
    assert len(spawned) == 2
    for (argv, env), own in zip(spawned, ["prod-secret-1", "dev-secret-2"], strict=True):
        assert not [a for a in argv if any(sec in a for sec in secrets)], argv
        assert env["CVCPKG_TOKEN"] == own
        rest = json.dumps({k: v for k, v in env.items() if k != "CVCPKG_TOKEN"})
        assert not [sec for sec in secrets if sec in rest]
        assert env["UNRELATED_SETTING"] == "keep-me"
        assert env["CVCPKG_BUILDER_SCRUB_ENV"] == "CVCPKG_TOKEN_PROD,DEV_TOK"


def test_fleet_dry_run_shows_no_token_anywhere(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from cvcpkg.cli._builder import builder_fleet

    monkeypatch.setenv("TOK", "supersecret")
    p = tmp_path / "fleet.yaml"
    p.write_text("servers:\n  - server: https://a.example\n    token_env: TOK\n")
    res = CliRunner().invoke(builder_fleet, ["--config", str(p), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "supersecret" not in res.output
    assert "--token" not in res.output
    assert "CVCPKG_TOKEN=*** (from $TOK)" in res.output


# ── restarts: capped exponential backoff ────────────────────────


@pytest.mark.parametrize(
    ("previous", "base", "lived", "expected"),
    [
        (0.0, 5.0, 0.0, 5.0),  # first restart
        (5.0, 5.0, 3.0, 10.0),  # died young again: double
        (160.0, 5.0, 3.0, 300.0),  # capped
        (300.0, 5.0, 3.0, 300.0),
        (300.0, 5.0, 61.0, 5.0),  # ran a minute: healthy, start over
        (0.0, 0.0, 0.0, 0.0),  # --restart-delay 0 means no delay at all
        (0.0, 600.0, 0.0, 600.0),  # a base above the cap is honoured
        (600.0, 600.0, 1.0, 600.0),
    ],
)
def test_next_respawn_delay(previous, base, lived, expected):
    from cvcpkg.cli._builder import _next_respawn_delay

    assert _next_respawn_delay(previous, base, lived) == expected


def test_frozen_worker_crash_loop_backs_off(monkeypatch, capsys):
    """Frozen simulation: a single-file worker that dies at once (revoked token,
    stuck pidfile) is restarted on a doubling delay, not every 5 s -- each
    start unpacks a fresh ~85 MB copy of the binary."""
    import os
    import signal
    import subprocess

    from cvcpkg.builder_fleet import FleetConfig, FleetServer
    from cvcpkg.cli import _builder

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/cvcpkg/cvcpkg")
    monkeypatch.setattr(_builder, "_FLEET_POLL_SECS", 0.005)
    monkeypatch.setattr(_builder, "_FLEET_RESPAWN_MAX_DELAY", 0.08)
    monkeypatch.setattr(sys, "platform", "linux")  # the POSIX path, on every CI OS
    monkeypatch.setattr(os, "killpg", lambda *a: None, raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)  # never a real taskkill
    handlers: dict = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    spawns: list = []

    class _Dead:
        def __init__(self, pid):
            self.pid, self.returncode = pid, 1

        def poll(self):
            return self.returncode

        def send_signal(self, _sig):
            pass

        def wait(self, timeout=None):
            return 1

        def terminate(self):
            pass

    def _popen(argv, env=None, **kw):
        spawns.append((list(argv), dict(env or {})))
        if len(spawns) == 7:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        return _Dead(6000 + len(spawns))

    monkeypatch.setattr(subprocess, "Popen", _popen)
    fs = FleetServer(server="https://a.example", token="t", serve=("",), name="a")
    _builder._supervise_fleet(FleetConfig(name="f", servers=[fs]), restart_delay=0.01)

    out = capsys.readouterr().out
    delays = [
        line.rsplit("restarting in ", 1)[1] for line in out.splitlines() if "restarting in" in line
    ]
    assert delays[:6] == ["0.01s", "0.02s", "0.04s", "0.08s", "0.08s", "0.08s"], out
    argv, env = spawns[0]
    assert argv[:3] == ["/opt/cvcpkg/cvcpkg", "builder", "run"] and "-m" not in argv
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["CVCPKG_TOKEN"] == "t" and "t" not in argv


# ── Windows: own process group, Ctrl+Break drain, tree kill ─────


def _windows(monkeypatch):
    import os

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os, "killpg", raising=False)


def test_windows_worker_is_drained_with_ctrl_break_then_tree_killed(monkeypatch):
    """SIGINT cannot be sent to a process on Windows (send_signal raises
    ValueError, which was swallowed: workers never drained).  Each worker gets
    its own console process group and CTRL_BREAK_EVENT; one that outlives the
    drain is killed with its whole tree, not just the launcher."""
    import signal
    import subprocess

    from cvcpkg.builder_fleet import FleetConfig, FleetServer
    from cvcpkg.cli import _builder

    _windows(monkeypatch)
    monkeypatch.setattr(_builder, "_FLEET_POLL_SECS", 0.005)
    handlers: dict = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    popen_kw: list = []
    sent: list = []
    ran: list = []
    terminated: list = []

    class _Busy:
        pid = 777
        returncode = None
        polls = 0

        def poll(self):
            _Busy.polls += 1
            if _Busy.polls == 1:  # the supervisor's first look: stop the fleet
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            return None

        def send_signal(self, sig):
            sent.append(sig)

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("cvcpkg", timeout)

        def terminate(self):
            terminated.append(self.pid)

    def _popen(argv, env=None, **kw):
        popen_kw.append(kw)
        return _Busy()

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(list(cmd)))
    fs = FleetServer(server="https://a.example", token="t", serve=("",), name="a")
    _builder._supervise_fleet(FleetConfig(name="f", servers=[fs]), restart_delay=0.0)

    assert popen_kw[0].get("creationflags") == getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
    )
    assert "start_new_session" not in popen_kw[0]
    assert sent == [getattr(signal, "CTRL_BREAK_EVENT", 1)]
    assert ran == [["taskkill", "/T", "/F", "/PID", "777"]]
    assert terminated == []


def test_windows_exited_worker_is_reaped_as_a_tree(monkeypatch):
    import signal
    import subprocess

    from cvcpkg.builder_fleet import FleetConfig, FleetServer
    from cvcpkg.cli import _builder

    _windows(monkeypatch)
    monkeypatch.setattr(_builder, "_FLEET_POLL_SECS", 0.005)
    handlers: dict = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    ran: list = []
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
            return _Proc(5001, 3)  # exited
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return _Proc(5002, None)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(list(cmd)))
    fs = FleetServer(server="https://a.example", token="t", serve=("",), name="a")
    _builder._supervise_fleet(FleetConfig(name="f", servers=[fs]), restart_delay=0.0)

    assert ran == [["taskkill", "/T", "/F", "/PID", "5001"]]
    assert len(spawns) == 2
