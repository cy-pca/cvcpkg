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

And, in this change's own first cut:

* a timed-out step was killed, but ``communicate()`` then waited for EOF on
  its pipes, which any grandchild holds open -- git's ssh and
  git-remote-https helpers do.  A fetch stuck on a dead connection blocked
  the builder's socket thread (no heartbeats, no messages) indefinitely.
* the checkout search guessed ``~/src/cvc/cvcpkg``, ``~/cvcpkg`` and
  ``~/libcvc-deps``: on a PyPI install that picked up a developer's clone on a
  feature branch, which ``git pull`` + pip install then put on the builder.
* the source check ignored a failed fetch, a diverged branch and a dirty
  tree, so it could promise an upstream version the pull would never deliver
  -- and drain the builder for nothing.
* the post-install check compared pyproject.toml's version string to the
  installed metadata's, so ``2.5.0-rc1`` vs ``2.5.0rc1`` failed a good install.

The slow steps (git, pip) run through ``_run_update_step``; tests stub it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
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

    def __init__(
        self, *, pip_rc=0, pip_err="", fresh=NEWER, pip_answers=None, probe_rc=0, probe_err=""
    ):
        self.cmds: list[list[str]] = []
        self.pip_rc = pip_rc
        self.pip_err = pip_err
        self.fresh = fresh
        self.pip_answers = list(pip_answers or [])
        self.probe_rc = probe_rc
        self.probe_err = probe_err

    def __call__(self, cmd, *, timeout, cwd=None, beat=None):
        self.cmds.append(list(cmd))
        if "pip" in cmd:
            if self.pip_answers:
                rc, err = self.pip_answers.pop(0)
            else:
                rc, err = self.pip_rc, self.pip_err
            return subprocess.CompletedProcess(cmd, rc, "", err)
        if "-c" in cmd:  # the fresh-interpreter version probe
            if self.probe_rc:
                return subprocess.CompletedProcess(cmd, self.probe_rc, "", self.probe_err)
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
    err = capsys.readouterr().err
    assert f"imports cvcpkg {RUNNING}" in err
    # pip did succeed: the log must not leave the impression nothing changed.
    assert f"pip installed {NEWER}" in err and "on disk now" in err


def test_an_install_reported_in_the_other_spelling_restarts(monkeypatch, update_env):
    """pyproject.toml says 2.5.0-rc1, the installed metadata says 2.5.0rc1:
    the same version, so the install took."""
    _, reexecs = update_env
    monkeypatch.setattr(b, "_checkout_version", lambda path: "999.0.0-rc1")
    monkeypatch.setattr(b, "_run_update_step", _Steps(fresh="999.0.0rc1"))
    b._self_update(token="tk")
    assert len(reexecs) == 1


def test_an_install_that_cannot_be_imported_does_not_restart(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    steps = _Steps(probe_rc=1, probe_err="ModuleNotFoundError: No module named 'websockets'")
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")

    assert reexecs == []
    err = capsys.readouterr().err
    assert "cannot import cvcpkg" in err and "ModuleNotFoundError" in err
    assert "on disk now" in err


@pytest.mark.parametrize(
    ("a", "b_", "newer", "same"),
    [
        ("2.5.0-rc1", "2.5.0rc1", False, True),  # pyproject vs metadata spelling
        ("2.5.0rc.1", "2.5.0rc1", False, True),
        ("2.4", "2.4.0", False, True),
        ("2.5.0rc1", "2.4.0", True, False),
        ("2.4.0", "2.5.0rc1", False, False),
        ("2.5.0", "2.5.0rc1", True, False),
        ("2.5.0rc1", "2.5.0b2", True, False),
        ("2.5.0a1", "2.5.0.dev1", True, False),
        ("2.5.0.post1", "2.5.0", True, False),
        ("2.10.0", "2.9.9", True, False),
    ],
)
def test_version_comparison_is_pep440(a, b_, newer, same):
    assert b._is_newer_version(a, b_) is newer
    assert b._same_version(a, b_) is same


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


def _alive(pid: int) -> bool:
    """True while *pid* runs (a zombie, already dead but unreaped, is not)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _gone_within(pid: int, secs: float) -> bool:
    deadline = time.monotonic() + secs
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell and process groups")


@posix_only
def test_update_step_timeout_does_not_wait_for_grandchildren():
    """A grandchild holding the step's pipes (git's ssh / remote-https
    helper) used to keep the timed-out step blocked for its whole life."""
    started = time.monotonic()
    r = b._run_update_step(["sh", "-c", "sleep 30 & sleep 30"], timeout=1)
    assert r.returncode == 124
    assert time.monotonic() - started < 10


@posix_only
def test_update_step_timeout_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "bg.pid"
    started = time.monotonic()
    r = b._run_update_step(["sh", "-c", f'sleep 30 & echo $! > "{pidfile}"; sleep 30'], timeout=1)
    assert r.returncode == 124
    assert _gone_within(int(pidfile.read_text()), 5)
    assert time.monotonic() - started < 10  # killed, not waited out


@posix_only
def test_update_step_gives_up_on_a_process_that_escaped_the_kill(monkeypatch, tmp_path):
    """One that left the step's session survives the kill and keeps the pipes;
    the step still returns, a bounded time after its deadline."""
    monkeypatch.setattr(b, "_UPDATE_KILL_GRACE_SECS", 0.5)
    pidfile = tmp_path / "escaped.pid"
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    try:
        r = b._run_update_step([sys.executable, "-c", script], timeout=2)
        assert r.returncode == 124 and "output lost" in r.stderr
        assert time.monotonic() - started < 10
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 9)  # the exact pid this test started
            except (ProcessLookupError, ValueError):
                pass


@posix_only
@pytest.mark.parametrize("tail", ["; sleep 30", ""], ids=["step-running", "step-exited"])
def test_an_interrupted_update_step_takes_its_tree_with_it(monkeypatch, tmp_path, tail):
    """The step runs in its own session, so Ctrl-C at the builder does not
    reach it: the builder has to kill it on the way out -- including what it
    started when the step's own process has already exited."""
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.2)
    pidfile = tmp_path / "bg.pid"

    def interrupt():
        if pidfile.exists() and pidfile.read_text().strip():
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        b._run_update_step(
            ["sh", "-c", f'sleep 30 & echo $! > "{pidfile}"{tail}'],
            timeout=30,
            beat=interrupt,
        )
    assert _gone_within(int(pidfile.read_text()), 5)


def test_update_steps_run_in_their_own_process_group(monkeypatch):
    calls: list = []

    def fake_popen(cmd, **kw):
        calls.append((list(cmd), kw))
        raise OSError("not really started")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert b._run_update_step(["git", "fetch"], timeout=5).returncode == 127
    cmd, seen = calls[-1]  # earlier: the `git config` read for the env
    assert cmd == ["git", "fetch"]
    if sys.platform == "win32":
        assert seen["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert seen["start_new_session"] is True
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


@pytest.fixture
def bare_git_env(monkeypatch, tmp_path):
    """No git settings from the developer's own environment or config."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    for var in (
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_HTTP_LOW_SPEED_LIMIT",
        "GIT_HTTP_LOW_SPEED_TIME",
        "GCM_INTERACTIVE",
    ):
        monkeypatch.delenv(var, raising=False)
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    return repo


def test_git_steps_get_transport_bounds(bare_git_env):
    env = b._git_update_env(bare_git_env)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "ServerAliveInterval" in env["GIT_SSH_COMMAND"]
    assert env["GIT_HTTP_LOW_SPEED_LIMIT"] == "1000"
    assert env["GIT_HTTP_LOW_SPEED_TIME"] == "30"


def test_a_builders_own_git_settings_win(bare_git_env, monkeypatch):
    repo = bare_git_env
    _git("config", "core.sshCommand", "ssh -i /etc/cvcpkg/deploy_key", cwd=repo)
    _git("config", "http.lowSpeedTime", "600", cwd=repo)
    monkeypatch.setenv("GCM_INTERACTIVE", "auto")
    env = b._git_update_env(repo)
    assert "GIT_SSH_COMMAND" not in env  # would override core.sshCommand
    assert "GIT_HTTP_LOW_SPEED_TIME" not in env  # would override http.lowSpeedTime
    assert env["GIT_HTTP_LOW_SPEED_LIMIT"] == "1000"  # not configured: still bounded
    assert env["GCM_INTERACTIVE"] == "auto"

    monkeypatch.setenv("GIT_SSH_COMMAND", "my-ssh")
    monkeypatch.setenv("GIT_HTTP_LOW_SPEED_LIMIT", "5")
    env = b._git_update_env(repo)
    assert env["GIT_SSH_COMMAND"] == "my-ssh" and env["GIT_HTTP_LOW_SPEED_LIMIT"] == "5"


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


def test_update_candidates_never_guess_home_directory_clones(monkeypatch, tmp_path):
    """A developer's clone (here ~/src/cvc/cvcpkg, on whatever branch) is not
    where a PyPI-installed builder came from: never update from it."""
    monkeypatch.delenv("CVCPKG_SELF_UPDATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(b, "_installed_source_dir", lambda: None)
    guesses = [
        _fake_checkout(tmp_path / "src" / "cvc" / "cvcpkg"),
        _fake_checkout(tmp_path / "cvcpkg"),
        _fake_checkout(tmp_path / "libcvc-deps"),
    ]
    assert not set(guesses) & set(b._self_update_candidates())
    assert b._find_update_checkout() not in guesses

    pip_src = _fake_checkout(tmp_path / "installed-from")
    monkeypatch.setattr(b, "_installed_source_dir", lambda: pip_src)
    assert b._self_update_candidates()[0] == pip_src
    assert b._find_update_checkout() == pip_src


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


def test_resolve_update_source_distrusts_a_stale_ref_after_a_failed_fetch(
    git_checkout, tmp_path, capsys
):
    """The remote-tracking ref says NEWER (an earlier fetch), but the remote is
    unreachable now: the pull will fail, so only the working tree counts."""
    _git("fetch", "-q", cwd=git_checkout)
    (tmp_path / "origin").rename(tmp_path / "origin-gone")
    assert b._resolve_update_source() == (git_checkout, RUNNING)
    assert "git fetch" in capsys.readouterr().err


def test_resolve_update_source_ignores_an_upstream_it_cannot_fast_forward_to(git_checkout):
    (git_checkout / "local.txt").write_text("local work\n")
    _git("add", "local.txt", cwd=git_checkout)
    _git("commit", "-qm", "local", cwd=git_checkout)
    assert b._resolve_update_source() == (git_checkout, RUNNING)


def test_resolve_update_source_ignores_the_upstream_under_local_edits(git_checkout):
    pyproject = git_checkout / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "# local edit\n")
    assert b._resolve_update_source() == (git_checkout, RUNNING)


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
