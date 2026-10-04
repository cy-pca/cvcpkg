# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Re-invoking cvcpkg from a pip install vs. the PyInstaller single binary.

The frozen binary hands its argv straight to the click CLI, so the
``python -m cvcpkg`` form makes it fail with ``No such option '-m'``.  These
tests simulate the frozen binary (``sys.frozen`` + ``sys.executable`` pointing
at cvcpkg itself) for every place that starts a cvcpkg child.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from cvcpkg.selfexec import cvcpkg_argv, cvcpkg_env, is_frozen, restore_library_path

_REAL_PYTHON = sys.executable
_FAKE_BINARY = "/opt/cvcpkg/bin/cvcpkg"


@pytest.fixture
def source_install(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    return _REAL_PYTHON


@pytest.fixture
def frozen(monkeypatch):
    """Look like the PyInstaller single binary at ``_FAKE_BINARY``."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", _FAKE_BINARY)
    return _FAKE_BINARY


# ── argv / env ──────────────────────────────────────────────────


def test_source_install_runs_python_dash_m(source_install):
    assert not is_frozen()
    assert cvcpkg_argv("builder", "run") == [source_install, "-m", "cvcpkg", "builder", "run"]


def test_frozen_runs_the_binary_directly(frozen):
    assert is_frozen()
    argv = cvcpkg_argv("builder", "run", "--max-jobs", "2")
    assert argv == [frozen, "builder", "run", "--max-jobs", "2"]
    assert "-m" not in argv[:2]


def test_source_install_env_is_an_unmodified_copy(source_install, monkeypatch):
    monkeypatch.delenv("CVCPKG_ENTRY", raising=False)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    base = {"PATH": "/bin", "KEEP": "1"}
    env = cvcpkg_env(base)
    assert env == base
    assert env is not base
    # Default base is the live environment.
    assert cvcpkg_env() == dict(os.environ)


def test_frozen_env_pins_client_entry(frozen):
    base = {"PATH": "/bin", "CVCPKG_ENTRY": "server"}
    env = cvcpkg_env(base)
    # Overrides an inherited server entry: the children are all client commands.
    assert env["CVCPKG_ENTRY"] == "client"
    assert env["PATH"] == "/bin"
    # Every frozen child is its own instance: the parent's _MEIPASS is off the
    # library path after restore_library_path(), so it cannot be shared.
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    # The caller's mapping is not modified.
    assert base["CVCPKG_ENTRY"] == "server"


def test_frozen_child_unpacks_its_own_copy(frozen):
    env = cvcpkg_env({})
    assert env == {"CVCPKG_ENTRY": "client", "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def test_frozen_child_of_combined_binary_dispatches_to_client(frozen, monkeypatch):
    """The combined client+server binary picks its CLI by program name; a child
    started from a binary whose file name contains "server" must still get the
    client CLI that its argv is written for."""
    launcher_path = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "cvcpkg_launcher.py"
    spec = importlib.util.spec_from_file_location("cvcpkg_launcher_selfexec", launcher_path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    monkeypatch.setattr(sys, "executable", "/opt/cvcpkg/bin/cvcpkg-server")
    child_argv = cvcpkg_argv("builder", "run")
    child_env = cvcpkg_env({})
    monkeypatch.setattr(sys, "argv", child_argv)
    for key, value in child_env.items():
        monkeypatch.setenv(key, value)
    assert launcher._want_server() is False


# ── a real child process that behaves like the frozen binary ────


@pytest.mark.skipif(sys.platform == "win32", reason="needs an executable sh script")
def test_fake_frozen_binary_accepts_new_argv_and_rejects_dash_m(tmp_path, monkeypatch):
    """Run a stand-in for the single binary: an executable whose entry point is
    the frozen one (``cvcpkg/__main__.py``: its argv goes straight into click)."""
    entry = "import sys; from cvcpkg.cli import main; sys.exit(main())"
    fake = tmp_path / "cvcpkg"
    fake.write_text(f'#!/bin/sh\nexec {shlex.quote(_REAL_PYTHON)} -c {shlex.quote(entry)} "$@"\n')
    fake.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake))

    ok = subprocess.run(
        cvcpkg_argv("builder", "run", "--help"),
        env=cvcpkg_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ok.returncode == 0, ok.stderr
    assert "--max-jobs" in ok.stdout

    # The pre-fix form is what crash-looped every fleet worker.
    old = subprocess.run(
        [str(fake), "-m", "cvcpkg", "builder", "run", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert old.returncode != 0
    assert "No such option" in old.stderr and "-m" in old.stderr


# ── builder self-update ─────────────────────────────────────────


@pytest.mark.parametrize("windows_supervised", [False, True])
def test_frozen_builder_self_update_is_skipped(frozen, windows_supervised, monkeypatch, capsys):
    """A frozen builder told to update must keep running: `-m pip` would be
    rejected and the re-exec would hand the binary its own path as a command.
    Under the (pip-based) Windows supervisor it must not exit for a restart
    either, or it is relaunched on the same version and asked again."""
    from cvcpkg.cli import _builder

    if windows_supervised:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("CVCPKG_BUILDER_SUPERVISED", "1")
    else:
        monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)

    def _boom(*_a, **_k):
        raise AssertionError("frozen self-update must not run or exec anything")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(os, "execv", _boom)

    _builder._self_update()  # returns: no exec, no SystemExit

    err = capsys.readouterr().err
    assert "self-update: skipped" in err
    assert "single-file cvcpkg binary" in err


# ── cvcpkg cpkg deps ────────────────────────────────────────────


def _make_prefix(root: pathlib.Path) -> pathlib.Path:
    (root / "include").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "lib" / "libz.a").write_text("")
    return root


@pytest.mark.parametrize("mode", ["source", "frozen"])
def test_cpkg_deps_installs_through_the_same_cvcpkg(mode, tmp_path, monkeypatch):
    from cvcpkg.cli import _cpkg
    from cvcpkg.cli._install import install

    if mode == "frozen":
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", _FAKE_BINARY)
        expected_head = [_FAKE_BINARY]
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
        expected_head = [_REAL_PYTHON, "-m", "cvcpkg"]
    monkeypatch.delenv("CVCPKG_ENTRY", raising=False)
    monkeypatch.delenv("CVCPKG_SERVER_URL", raising=False)

    prefix = _make_prefix(tmp_path / "deps")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(_cpkg.subprocess, "run", _fake_run)
    # The install child's stdout is sent to our stderr's fd.
    monkeypatch.setattr(sys, "stderr", sys.__stderr__)

    _cpkg.cpkg_deps.callback(
        components=("zlib",),
        prefix=str(prefix),
        fmt="json",
        release="v1",
        arch="x86_64",
        server="https://pkg.example",
        token="tok",
        require_signatures=True,
        no_install=False,
    )

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[: len(expected_head)] == expected_head
    args = cmd[len(expected_head) :]
    assert args == [
        "install",
        "zlib",
        "--prefix",
        str(prefix),
        "--release",
        "v1",
        "--arch",
        "x86_64",
        "--require-signatures",
    ]
    # Every flag must be one `cvcpkg install` accepts ...
    ctx = install.make_context("install", args[1:])
    assert ctx.params["prefix"] == str(prefix)
    # ... so the server travels the way install reads it: the environment.
    env = kwargs["env"]
    assert env["CVCPKG_SERVER_URL"] == "https://pkg.example"
    # The token travels the same way, never on a command line.
    assert env["CVCPKG_TOKEN"] == "tok"
    assert "tok" not in cmd
    if mode == "frozen":
        assert env["CVCPKG_ENTRY"] == "client"
    else:
        assert "CVCPKG_ENTRY" not in env


# ── the bootloader's LD_LIBRARY_PATH ────────────────────────────


def test_restore_library_path_puts_back_the_callers_value(frozen, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEIabc", raising=False)
    env = {"LD_LIBRARY_PATH": "/tmp/_MEIabc:/opt/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    restore_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/opt/lib"}


def test_restore_library_path_drops_a_bootloader_created_value(frozen, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEIabc", raising=False)
    env = {"LD_LIBRARY_PATH": "/tmp/_MEIabc", "PATH": "/bin"}
    restore_library_path(env)
    assert env == {"PATH": "/bin"}


def test_restore_library_path_is_a_noop_for_a_source_install(source_install):
    env = {"LD_LIBRARY_PATH": "/x", "LD_LIBRARY_PATH_ORIG": "/y"}
    restore_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/x", "LD_LIBRARY_PATH_ORIG": "/y"}
