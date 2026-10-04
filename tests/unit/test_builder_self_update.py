"""Server-pushed self-update of a pip-installed builder.

What used to go wrong (each reproduced against a real builder):

* the re-exec was ``[python] + sys.argv``.  Under ``python -m cvcpkg`` -- every
  fleet worker -- ``sys.argv[0]`` is ``.../cvcpkg/__main__.py``; run as a
  script, the package directory goes first on ``sys.path``, cvcpkg/platform.py
  shadows the stdlib ``platform`` and the successor dies at startup.  A
  standalone builder then stayed down.
* pip ran with ``check=False`` and the builder restarted whatever happened --
  including when pip failed, which it always did on pip < 23.0.1 because
  ``--break-system-packages`` was passed unconditionally.
* an update with nothing newer to install (a site-packages install with no
  checkout, a stale checkout, the same version) still drained the builder
  first, for as long as its longest job.

The slow steps (git, pip) run through ``_run_update_step``; tests stub it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cvcpkg.cli._builder as b
from cvcpkg import __version__

RUNNING = __version__
NEWER = "999.0.0"


# -- the re-exec ----------------------------------------------------------------


def test_reexec_argv_runs_the_package_not_its_main_file(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    tail = ["builder", "run", "--server", "https://x", "--name", "n", "--max-jobs", "2"]
    assert b._reexec_argv(tail) == [sys.executable, "-m", "cvcpkg", *tail]


def test_reexec_argv_frozen_simulation(monkeypatch):
    """The single-file binary is cvcpkg itself: no ``-m``."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/cvcpkg/cvcpkg")
    assert b._reexec_argv(["builder", "run", "--name", "n"]) == [
        "/opt/cvcpkg/cvcpkg",
        "builder",
        "run",
        "--name",
        "n",
    ]


@pytest.mark.parametrize(
    "tail",
    [
        ["builder", "run", "--token", "sekrit", "--name", "n", "--daemon"],
        ["builder", "run", "--token=sekrit", "--daemon", "--name", "n"],
    ],
)
def test_reexec_drops_token_and_daemon_from_argv(monkeypatch, tail):
    """The successor gets the token through CVCPKG_TOKEN, not argv; and it is
    already detached (forking again would change the PID under the pidfile)."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    argv = b._reexec_argv(tail)
    assert argv == [sys.executable, "-m", "cvcpkg", "builder", "run", "--name", "n"]


def test_reexec_builder_execs_python_dash_m_with_the_token_in_env(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    main_py = str(Path(b.__file__).resolve().parents[1] / "__main__.py")
    monkeypatch.setattr(sys, "argv", [main_py, "builder", "run", "--token", "tk", "--name", "n"])
    seen: list = []
    monkeypatch.setattr(os, "execve", lambda path, argv, env: seen.append((path, argv, env)))
    b._reexec_builder("tk", {"CVCPKG_BUILDER_SCRUB_ENV": "DEV_TOK"})

    (path, argv, env) = seen[0]
    assert path == sys.executable
    assert argv == [sys.executable, "-m", "cvcpkg", "builder", "run", "--name", "n"]
    assert main_py not in argv and "tk" not in argv
    assert env["CVCPKG_TOKEN"] == "tk"
    assert env["CVCPKG_BUILDER_SCRUB_ENV"] == "DEV_TOK"


def test_reexec_argv_starts_a_working_cvcpkg(tmp_path):
    """For real: the successor's argv starts a working cvcpkg.

    (The old ``[python, .../cvcpkg/__main__.py]`` died on the ``platform``
    shadowing -- but not when something imported the stdlib ``platform``
    first, e.g. pytest-cov's .pth hook under ``--cov``, so that half is not
    asserted here.)
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    new = subprocess.run(
        b._reexec_argv(["--version"]),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
    )
    assert new.returncode == 0, new.stderr
    assert RUNNING in new.stdout


# -- _self_update ----------------------------------------------------------------


class _Steps:
    """Stand-in for _run_update_step: records commands, answers per kind."""

    def __init__(self, *, pip_rc=0, pip_err="", fresh=NEWER, pip_answers=None):
        self.cmds: list[list[str]] = []
        self.pip_rc = pip_rc
        self.pip_err = pip_err
        self.fresh = fresh
        self.pip_answers = list(pip_answers or [])

    def __call__(self, cmd, *, timeout, cwd=None, beat=None):
        self.cmds.append(list(cmd))
        if "pip" in cmd:
            if self.pip_answers:
                rc, err = self.pip_answers.pop(0)
            else:
                rc, err = self.pip_rc, self.pip_err
            return subprocess.CompletedProcess(cmd, rc, "", err)
        if "-c" in cmd:  # the fresh-interpreter version probe
            return subprocess.CompletedProcess(cmd, 0, f"{self.fresh}\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def pips(self):
        return [c for c in self.cmds if "pip" in c]


@pytest.fixture
def update_env(monkeypatch, tmp_path):
    """A checkout at NEWER; re-exec captured instead of performed."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    checkout = tmp_path / "cvcpkg"
    checkout.mkdir()
    monkeypatch.setattr(b, "_find_update_checkout", lambda: checkout)
    monkeypatch.setattr(b, "_checkout_version", lambda path: NEWER)
    reexecs: list = []
    monkeypatch.setattr(
        b, "_reexec_builder", lambda token, extra_env=None: reexecs.append((token, extra_env))
    )
    return checkout, reexecs


def _set_pip_version(monkeypatch, version):
    import importlib.metadata as md

    real = md.version
    monkeypatch.setattr(md, "version", lambda name: version if name == "pip" else real(name))


def test_success_installs_with_builder_extra_and_reexecs(monkeypatch, update_env, capsys):
    checkout, reexecs = update_env
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk", extra_env={"X": "1"})

    assert steps.cmds[0][:3] == ["git", "pull", "--ff-only"]
    (pip,) = steps.pips()
    assert pip[-1] == f"{checkout}[builder]"
    assert reexecs == [("tk", {"X": "1"})]


def test_pip_failure_does_not_restart(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_run_update_step", _Steps(pip_rc=1, pip_err="ERROR: network down"))
    b._self_update(token="tk")

    assert reexecs == []
    err = capsys.readouterr().err
    assert "pip install failed (exit 1)" in err and "network down" in err
    assert f"staying on {RUNNING}" in err


def test_same_version_checkout_installs_nothing(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_checkout_version", lambda path: RUNNING)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")

    assert steps.pips() == [] and reexecs == []
    assert "not installing it" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("pip_version", "flag"),
    [("22.0.2", False), ("23.0", False), ("23.0.1", True), ("24.2", True)],
)
def test_break_system_packages_only_for_a_pip_that_knows_it(
    monkeypatch, update_env, pip_version, flag
):
    _set_pip_version(monkeypatch, pip_version)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")

    (pip,) = steps.pips()
    assert ("--break-system-packages" in pip) is flag


def test_pip_rejecting_the_flag_is_retried_without_it(monkeypatch, update_env):
    _, reexecs = update_env
    _set_pip_version(monkeypatch, "24.0")
    steps = _Steps(pip_answers=[(2, "no such option: --break-system-packages"), (0, "")])
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")

    first, second = steps.pips()
    assert "--break-system-packages" in first and "--break-system-packages" not in second
    assert len(reexecs) == 1


def test_an_install_a_fresh_interpreter_does_not_see_does_not_restart(
    monkeypatch, update_env, capsys
):
    """pip succeeded, but into somewhere the re-exec would not import from."""
    _, reexecs = update_env
    monkeypatch.setattr(b, "_run_update_step", _Steps(fresh=RUNNING))
    b._self_update(token="tk")

    assert reexecs == []
    assert "a fresh interpreter imports" in capsys.readouterr().err


def test_frozen_binary_never_runs_a_step(monkeypatch, update_env):
    _, reexecs = update_env
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert steps.cmds == [] and reexecs == []


def test_no_checkout_installs_nothing(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_find_update_checkout", lambda: None)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert steps.cmds == [] and reexecs == []
    assert "CVCPKG_SELF_UPDATE_DIR" in capsys.readouterr().err


# -- _run_update_step -----------------------------------------------------------


def test_update_step_heartbeats_while_it_runs(monkeypatch):
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.1)
    beats: list = []
    r = b._run_update_step(
        [sys.executable, "-c", "import time; time.sleep(0.8); print('done')"],
        timeout=30,
        beat=lambda: beats.append(1),
    )
    assert r.returncode == 0 and r.stdout.strip() == "done"
    assert len(beats) >= 3


def test_update_step_timeout_and_missing_command():
    slow = b._run_update_step([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert slow.returncode == 124 and "timed out" in slow.stderr
    missing = b._run_update_step(["cvcpkg-no-such-command-xyz"], timeout=5)
    assert missing.returncode == 127


# -- finding the checkout ---------------------------------------------------------


class _Dist:
    def __init__(self, text):
        self.text = text

    def read_text(self, name):
        return self.text if name == "direct_url.json" else None


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        ('{"url": "file:///home/tfx/libcvc-deps", "dir_info": {}}', "/home/tfx/libcvc-deps"),
        ('{"url": "https://files.pythonhosted.org/x.whl"}', None),
        (None, None),
    ],
)
def test_installed_source_dir_from_direct_url(monkeypatch, direct_url, expected):
    """A non-editable `pip install <checkout>` records where it came from."""
    import importlib.metadata as md

    monkeypatch.setattr(md, "distribution", lambda name: _Dist(direct_url))
    found = b._installed_source_dir()
    if expected is None:
        assert found is None
    elif sys.platform != "win32":
        assert found == Path(expected)


def _fake_checkout(path: Path, *, name="cvcpkg", git=True, version="1.0.0") -> Path:
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(f'[tool.poetry]\nname = "{name}"\nversion = "{version}"\n')
    if git:
        (path / ".git").mkdir()
    return path


def test_find_update_checkout_honours_the_override(monkeypatch, tmp_path):
    good = _fake_checkout(tmp_path / "good")
    monkeypatch.setenv("CVCPKG_SELF_UPDATE_DIR", str(good))
    assert b._find_update_checkout() == good
    monkeypatch.setenv("CVCPKG_SELF_UPDATE_DIR", str(_fake_checkout(tmp_path / "other", name="x")))
    assert b._find_update_checkout() is None  # not cvcpkg: never falls through
    monkeypatch.setenv("CVCPKG_SELF_UPDATE_DIR", str(_fake_checkout(tmp_path / "nogit", git=False)))
    assert b._find_update_checkout() is None


def _git(*args, cwd):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "init.defaultBranch=master",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_checkout(tmp_path, monkeypatch):
    """origin at RUNNING, cloned; then origin moves to NEWER."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    origin = _fake_checkout(tmp_path / "origin", git=False, version=RUNNING)
    _git("init", "-q", cwd=origin)
    _git("add", "pyproject.toml", cwd=origin)
    _git("commit", "-qm", "init", cwd=origin)
    _git("clone", "-q", str(origin), str(tmp_path / "clone"), cwd=tmp_path)
    clone = tmp_path / "clone"
    (origin / "pyproject.toml").write_text(f'[tool.poetry]\nname = "cvcpkg"\nversion = "{NEWER}"\n')
    _git("commit", "-qam", "bump", cwd=origin)
    monkeypatch.setenv("CVCPKG_SELF_UPDATE_DIR", str(clone))
    return clone


def test_resolve_update_source_reads_upstream_without_touching_the_tree(git_checkout):
    """Decided before draining, so it must not pull: an editable install runs
    out of the checkout, and jobs are still building."""
    assert b._resolve_update_source() == (git_checkout, NEWER)
    assert b._checkout_version(git_checkout) == RUNNING  # not pulled


def test_self_update_pulls_installs_and_restarts(git_checkout, monkeypatch):
    """git for real; pip and the version probe stubbed."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    real_step = b._run_update_step
    fake = _Steps()
    monkeypatch.setattr(
        b,
        "_run_update_step",
        lambda cmd, **kw: real_step(cmd, **kw) if cmd[0] == "git" else fake(cmd, **kw),
    )
    reexecs: list = []
    monkeypatch.setattr(b, "_reexec_builder", lambda token, extra_env=None: reexecs.append(token))
    b._self_update(token="tk")

    assert b._checkout_version(git_checkout) == NEWER
    (pip,) = fake.pips()
    assert pip[-1] == f"{git_checkout}[builder]"
    assert reexecs == ["tk"]
