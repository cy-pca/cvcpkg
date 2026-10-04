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

And in its second cut:

* the steps run in their own session, so a stop signal meant for the builder
  (Ctrl-C, ``kill <pid>``) no longer reached git or pip -- and builder_run's
  handler only sets a flag, which nothing on the update path read.  The step
  finished and the builder re-exec'd into a fresh builder that took work
  again: the stop request was lost with the old process image.
* ``git fetch`` without ``--prune`` kept the remote-tracking ref of an
  upstream branch deleted on the remote, so the source check promised a
  version the pull could not fetch.
* step output was decoded strictly: one non-UTF-8 byte from git or pip raised
  UnicodeDecodeError -- out of the ``builder.update`` handler, ending the
  builder's socket session.
* builders sharing a checkout (a fleet's workers, a dev and a prod unit)
  updated it concurrently: two ``git pull``s, two pip installs into one
  site-packages.

And in its third cut, the stop handling itself:

* a stop ended pip part-way.  pip rolls its uninstall back only on an
  Exception, never on Ctrl-C, so a pip stopped between uninstalling the old
  cvcpkg and installing the new one left none at all: the next start died
  with ModuleNotFoundError (a fleet worker crash-loops; an ``@reboot``
  builder stays down).
* a stop ended ``git pull`` mid-checkout.  HEAD stayed on the old commit
  under a tree half from the new one -- pyproject.toml, which sorts before
  src/, already saying the new version -- and every later pull refused
  ("would be overwritten").  The next update then installed that mixed tree
  as the new version and re-exec'd into code that matches no commit.  The
  update is now ``git fetch`` (which a stop may end) and a local
  ``git merge --ff-only`` (which it may not), and nothing is installed from a
  tree that differs from HEAD -- the source check does not drain for one.
* two fetches in one checkout at once (every builder sharing it, notified by
  one push) collided on the remote-tracking ref; the loser went by a stale
  ref and dropped the update.
* a step with no heartbeat to send polled ten times a second after 20 s.

The slow steps (git, pip) run through ``_run_update_step``; tests stub it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import cvcpkg.cli._builder as b
from cvcpkg import __version__

RUNNING = __version__
NEWER = "999.0.0"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell and process groups")


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
    """Stand-in for _run_update_step: records commands, answers per kind.

    The fresh-interpreter probe answers *before* (the version installed
    before this update) until a pip install succeeded, then *fresh*.
    *on_step*, when given, is called with each command first (a test's way to
    have something happen "during" a step).  A stop seen then ends the step
    (exit 130) -- unless the step is one a stop lets finish
    (*finish_on_stop*), which then completes as it would have.
    """

    def __init__(
        self,
        *,
        pip_rc=0,
        pip_err="",
        fresh=NEWER,
        before=RUNNING,
        pip_answers=None,
        probe_rc=0,
        probe_err="",
        on_step=None,
        fetch_answers=None,
        merge_rc=0,
        merge_err="",
        diff_rc=0,
    ):
        self.cmds: list[list[str]] = []
        self.stops: list = []
        self.finishes: list[str] = []
        self.pip_rc = pip_rc
        self.pip_err = pip_err
        self.fresh = fresh
        self.before = before
        self.installed = False
        self.pip_answers = list(pip_answers or [])
        self.probe_rc = probe_rc
        self.probe_err = probe_err
        self.on_step = on_step
        self.fetch_answers = list(fetch_answers or [])
        self.merge_rc = merge_rc
        self.merge_err = merge_err
        self.diff_rc = diff_rc

    def kind(self, cmd) -> str:
        if cmd[0] == "git":
            return cmd[1]  # fetch / merge / diff
        if "pip" in cmd:
            return "pip"
        return "probe"

    def __call__(self, cmd, *, timeout, cwd=None, beat=None, stop=None, finish_on_stop=""):
        self.cmds.append(list(cmd))
        self.stops.append(stop)
        self.finishes.append(finish_on_stop)
        if self.on_step is not None:
            self.on_step(cmd)
        if stop is not None and stop() and not finish_on_stop:
            return subprocess.CompletedProcess(cmd, 130, "", b._UPDATE_STOPPED_MSG)
        kind = self.kind(cmd)
        if kind == "fetch" and self.fetch_answers:
            rc, err = self.fetch_answers.pop(0)
            return subprocess.CompletedProcess(cmd, rc, "", err)
        if kind == "merge":
            return subprocess.CompletedProcess(cmd, self.merge_rc, "", self.merge_err)
        if kind == "diff":
            return subprocess.CompletedProcess(cmd, self.diff_rc, "", "")
        if "pip" in cmd:
            if self.pip_answers:
                rc, err = self.pip_answers.pop(0)
            else:
                rc, err = self.pip_rc, self.pip_err
            self.installed = self.installed or rc == 0
            return subprocess.CompletedProcess(cmd, rc, "", err)
        if "-c" in cmd:  # the fresh-interpreter version probe
            if self.probe_rc and self.installed:
                return subprocess.CompletedProcess(cmd, self.probe_rc, "", self.probe_err)
            version = self.fresh if self.installed else self.before
            return subprocess.CompletedProcess(cmd, 0, f"{version}\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def pips(self):
        return [c for c in self.cmds if "pip" in c]

    def finish_of(self, kind: str) -> list[str]:
        """*finish_on_stop* of each step of *kind* that ran."""
        return [f for c, f in zip(self.cmds, self.finishes, strict=True) if self.kind(c) == kind]

    def probes(self):
        return [c for c in self.cmds if "-c" in c]


@pytest.fixture
def update_env(monkeypatch, tmp_path):
    """A checkout at NEWER; re-exec captured instead of performed."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    checkout = tmp_path / "cvcpkg"
    (checkout / ".git").mkdir(parents=True)  # where the self-update lock goes
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

    # `git pull`, split: the fetch, then a local fast-forward, then a check
    # that the tree is exactly the commit it is on.
    assert steps.cmds[:3] == [
        ["git", "fetch", "--prune", "--quiet"],
        ["git", "merge", "--ff-only", "--quiet", "@{upstream}"],
        ["git", "diff", "--quiet", "HEAD"],
    ]
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


# -- a stop request during the update ---------------------------------------------


class _Flag:
    def __init__(self):
        self.up = False

    def __call__(self) -> bool:
        return self.up


def test_a_stop_before_the_update_runs_nothing(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk", stop=lambda: True)
    assert steps.cmds == [] and reexecs == []
    assert "shutdown requested" in capsys.readouterr().out


_KINDS = ["fetch", "merge", "diff", "probe", "pip", "verify"]


@pytest.mark.parametrize("after", _KINDS)
def test_a_stop_after_any_step_prevents_the_restart(monkeypatch, update_env, capsys, after):
    """The stop arrives as step *after* finishes (successfully).  The update
    goes no further, and above all does not re-exec: that would start a fresh
    builder taking work, the stop request lost with the old process image."""
    _, reexecs = update_env
    flag = _Flag()
    steps = _Steps()

    def kind(cmd):
        k = steps.kind(cmd)
        return ("verify" if steps.installed else "probe") if k == "probe" else k

    ran: list[str] = []

    def step(cmd, **kw):
        ran.append(kind(cmd))
        r = steps(cmd, **kw)
        if ran[-1] == after:
            flag.up = True
        return r

    monkeypatch.setattr(b, "_run_update_step", step)
    b._self_update(token="tk", stop=flag)

    assert reexecs == []
    assert ran == _KINDS[: _KINDS.index(after) + 1]  # nothing after the stop
    assert all(s is flag for s in steps.stops)  # every step sees the stop ...
    # ... and only the two that rewrite files are let finish when it comes.
    let_finish = {k for k, f in zip(ran, steps.finishes, strict=True) if f}
    assert let_finish == {"merge", "pip"} & set(ran)
    out = capsys.readouterr().out
    assert "shutdown requested" in out
    if after in ("pip", "verify"):
        assert "is installed; not restarting into it" in out


def test_merge_and_pip_are_let_finish_and_say_so(monkeypatch, update_env):
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk", stop=lambda: False)
    (merge,) = steps.finish_of("merge")
    (pip,) = steps.finish_of("pip")
    assert "letting git merge finish" in merge and "no restart" in merge
    assert pip == "letting pip finish (at most 300 s), no restart"
    assert not any(steps.finish_of("fetch") + steps.finish_of("diff") + steps.finish_of("probe"))


def test_a_stop_during_pip_lets_it_finish_and_does_not_restart(monkeypatch, update_env, capsys):
    """pip, stopped part-way, can leave no cvcpkg at all (it rolls its
    uninstall back only on an Exception, never on Ctrl-C).  So it finishes;
    the builder then stops instead of restarting."""
    _, reexecs = update_env
    flag = _Flag()

    def during_pip(cmd):
        if "pip" in cmd:
            flag.up = True

    steps = _Steps(on_step=during_pip)
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk", stop=flag)

    assert reexecs == []
    assert steps.installed  # pip ran to the end
    assert [steps.kind(c) for c in steps.cmds][-1] == "pip"  # no verify probe after it
    captured = capsys.readouterr()
    assert f"{NEWER} is installed; not restarting into it" in captured.out
    assert "reinstall with" not in captured.err  # nothing was broken


def test_a_pip_timeout_says_how_to_repair(monkeypatch, update_env, capsys):
    """A timed-out pip is Ctrl-C'd and then killed, possibly mid-install."""
    _, reexecs = update_env
    monkeypatch.setattr(b, "_run_update_step", _Steps(pip_rc=124, pip_err="timed out after 300s"))
    b._self_update(token="tk")

    assert reexecs == []
    err = capsys.readouterr().err
    assert "pip install failed (exit 124)" in err
    assert "half-installed" in err and "reinstall with" in err and "[builder]" in err


def test_a_failing_pip_that_was_not_cut_short_gives_no_repair_hint(monkeypatch, update_env, capsys):
    monkeypatch.setattr(b, "_run_update_step", _Steps(pip_rc=1, pip_err="ERROR: no network"))
    b._self_update(token="tk")
    assert "reinstall with" not in capsys.readouterr().err


@posix_only
def test_a_stop_during_a_running_pip_lets_it_finish(monkeypatch, update_env, tmp_path, capsys):
    """For real: pip (a 2 s stand-in that writes a marker at its end) runs when
    the stop arrives.  It is not ended -- the marker is there -- and the
    builder does not restart."""
    _, reexecs = update_env
    done = tmp_path / "pip-done"
    monkeypatch.setattr(
        b,
        "_pip_install_cmd",
        lambda checkout, break_system_packages: ["sh", "-c", f'sleep 2; touch "{done}"'],
    )
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.1)
    real_step = b._run_update_step
    fake = _Steps()
    monkeypatch.setattr(
        b,
        "_run_update_step",
        lambda cmd, **kw: real_step(cmd, **kw) if cmd[0] == "sh" else fake(cmd, **kw),
    )
    started = time.monotonic()
    b._self_update(token="tk", stop=lambda: time.monotonic() - started > 0.5)

    assert done.exists(), "pip was ended part-way"
    assert reexecs == []
    assert time.monotonic() - started < 10
    out = capsys.readouterr().out
    assert out.count("letting pip finish (at most 300 s), no restart") == 1
    assert "is installed; not restarting into it" in out


_SIGINT_HARNESS = """
import json, os, signal, subprocess, sys, threading, time
from pathlib import Path
import cvcpkg.cli._builder as b

stop = {"flag": False}
signal.signal(signal.SIGINT, lambda *a: stop.__setitem__("flag", True))  # builder_run's kind

reexecs = []
b._reexec_builder = lambda token, extra_env=None: reexecs.append(token)
checkout = Path(sys.argv[1])
done = Path(sys.argv[2])
b._UPDATE_STOP_POLL_SECS = 0.1
b._find_update_checkout = lambda: checkout
b._checkout_version = lambda p: "999.0.0"
b._fresh_cvcpkg_version = lambda beat=None, stop=None: ("0.0.1", "")
b._pip_install_cmd = lambda checkout, break_system_packages: [
    "sh", "-c", 'sleep 2; touch "$0"', str(done)]
real = b._run_update_step
def step(cmd, **kw):
    if cmd[0] == "git":  # fetch, merge, tree check: all fine
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return real(cmd, **kw)
b._run_update_step = step

def ctrl_c():
    time.sleep(0.7)
    os.killpg(os.getpgrp(), signal.SIGINT)  # what a terminal's Ctrl-C does
threading.Thread(target=ctrl_c, daemon=True).start()
started = time.monotonic()
b._self_update(token="tk", stop=lambda: stop["flag"])
print(json.dumps({"stopped": stop["flag"], "reexecs": len(reexecs),
                  "pip_done": done.exists(), "secs": time.monotonic() - started}))
"""


@posix_only
def test_a_real_ctrl_c_during_pip_lets_it_finish_and_is_not_turned_into_a_restart(tmp_path):
    """SIGINT to the builder's process group, under a handler that only sets
    a flag (as builder_run's does).  pip runs in its own session, so the
    signal never reaches it -- which is right: pip must not be cut short --
    and the flag keeps the builder from restarting once pip is done.  In a
    process (and session) of its own, so the SIGINT reaches nothing else."""
    checkout = tmp_path / "cvcpkg"
    (checkout / ".git").mkdir(parents=True)
    done = tmp_path / "pip-done"
    script = tmp_path / "harness.py"
    script.write_text(_SIGINT_HARNESS)
    # The cvcpkg this session tests, not whichever one the interpreter finds.
    src = str(Path(b.__file__).resolve().parents[2])
    pythonpath = os.pathsep.join(p for p in (src, os.environ.get("PYTHONPATH", "")) if p)
    r = subprocess.run(
        [sys.executable, str(script), str(checkout), str(done)],
        capture_output=True,
        text=True,
        timeout=60,
        start_new_session=True,
        env=dict(os.environ, PYTHONPATH=pythonpath),
    )
    assert r.returncode == 0, r.stderr
    import json

    result = json.loads(r.stdout.strip().splitlines()[-1])
    assert result["stopped"] is True
    assert result["pip_done"] is True, r.stdout  # not cut short by the Ctrl-C
    assert result["reexecs"] == 0, r.stdout
    assert "letting pip finish" in r.stdout


# -- builders sharing a checkout ----------------------------------------------------


def test_a_version_installed_meanwhile_is_not_reinstalled(monkeypatch, update_env, capsys):
    """Another builder sharing the checkout and interpreter installed it
    (while this one waited for the lock): restart, but no second pip run."""
    _, reexecs = update_env
    steps = _Steps(before=NEWER)
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert steps.pips() == []
    assert reexecs == [("tk", None)]
    assert "not reinstalling" in capsys.readouterr().out


def test_never_downgrades_what_another_builder_installed(monkeypatch, update_env):
    _, reexecs = update_env
    steps = _Steps(before="1000.0.0")  # newer than the checkout's NEWER
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert steps.pips() == []
    assert len(reexecs) == 1


_LOCK_HOLDER = (
    "import fcntl, sys, time\n"
    "f = open(sys.argv[1], 'a')\n"
    "fcntl.flock(f, fcntl.LOCK_EX)\n"
    "print('locked', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


@pytest.fixture
def lock_holder(update_env):
    """Another builder's update, holding the checkout's lock for *secs*."""
    checkout, _ = update_env
    procs: list = []

    def hold(secs: float):
        p = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _LOCK_HOLDER,
                str(checkout / ".git" / b._UPDATE_LOCK_NAME),
                str(secs),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        procs.append(p)
        assert p.stdout.readline().strip() == "locked"
        return p

    yield hold
    for p in procs:  # only the processes this fixture started
        p.kill()
        p.wait()


@posix_only
def test_builders_sharing_a_checkout_update_one_at_a_time(
    monkeypatch, update_env, lock_holder, capsys
):
    """The lock is held elsewhere: wait (heartbeating), then update."""
    _, reexecs = update_env
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.2)
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.05)
    started = time.monotonic()
    first_step: list[float] = []
    steps = _Steps(on_step=lambda cmd: first_step or first_step.append(time.monotonic()))
    monkeypatch.setattr(b, "_run_update_step", steps)
    beats: list = []
    lock_holder(1.5)
    b._self_update(token="tk", beat=lambda: beats.append(1))

    assert first_step and first_step[0] - started >= 1.0, "did not wait for the lock"
    assert len(beats) >= 3, "no heartbeat while waiting for the lock"
    assert len(steps.pips()) == 1 and len(reexecs) == 1
    assert "waiting for it to finish" in capsys.readouterr().out


@posix_only
def test_a_stop_while_waiting_for_the_lock_ends_the_wait(monkeypatch, update_env, lock_holder):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.05)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    lock_holder(30)
    started = time.monotonic()
    b._self_update(token="tk", stop=lambda: time.monotonic() - started > 0.3)
    assert time.monotonic() - started < 3
    assert steps.cmds == [] and reexecs == []


@posix_only
def test_waiting_for_the_lock_is_bounded(monkeypatch, update_env, lock_holder, capsys):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_UPDATE_LOCK_TIMEOUT", 0.3)
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.05)
    steps = _Steps()
    monkeypatch.setattr(b, "_run_update_step", steps)
    lock_holder(30)
    b._self_update(token="tk")
    assert steps.cmds == [] and reexecs == []
    assert "giving up on this update" in capsys.readouterr().err


def test_the_lock_is_released_after_the_update(monkeypatch, update_env):
    checkout, reexecs = update_env
    monkeypatch.setattr(b, "_run_update_step", _Steps(pip_rc=1))
    b._self_update(token="tk")
    fd = os.open(checkout / ".git" / b._UPDATE_LOCK_NAME, os.O_RDWR)
    try:
        assert b._try_lock(fd)
        b._unlock(fd)
    finally:
        os.close(fd)


def test_an_unusable_lock_does_not_block_the_update(monkeypatch, update_env, capsys):
    """No writable git dir to put the lock in: update unlocked, as before."""
    checkout, reexecs = update_env
    (checkout / ".git").rmdir()
    monkeypatch.setattr(b, "_run_update_step", _Steps())
    b._self_update(token="tk")
    assert len(reexecs) == 1
    assert "updating unlocked" in capsys.readouterr().err


def test_git_dir_follows_a_linked_worktree(git_checkout, tmp_path):
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), cwd=git_checkout)
    assert b._git_dir(git_checkout) == git_checkout / ".git"
    gitdir = b._git_dir(worktree)
    assert gitdir.is_dir()
    assert os.path.samefile(gitdir.parent, git_checkout / ".git" / "worktrees")


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


@posix_only
def test_update_step_timeout_does_not_wait_for_grandchildren(monkeypatch):
    """A grandchild holding the step's pipes (git's ssh / remote-https
    helper) used to keep the timed-out step blocked for its whole life."""
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)  # `sleep &` ignores SIGINT
    started = time.monotonic()
    r = b._run_update_step(["sh", "-c", "sleep 30 & sleep 30"], timeout=1)
    assert r.returncode == 124
    assert time.monotonic() - started < 10


@posix_only
def test_update_step_timeout_kills_the_whole_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)  # `sleep &` ignores SIGINT
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
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)
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
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)  # `sleep &` ignores SIGINT
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


@posix_only
def test_update_step_is_ended_when_stop_turns_true(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)
    pidfile = tmp_path / "bg.pid"
    started = time.monotonic()
    r = b._run_update_step(
        ["sh", "-c", f'sleep 30 & echo $! > "{pidfile}"; sleep 30'],
        timeout=60,
        stop=lambda: time.monotonic() - started > 0.5,
    )
    assert r.returncode == 130 and b._UPDATE_STOPPED_MSG in r.stderr
    assert time.monotonic() - started < 5
    assert _gone_within(int(pidfile.read_text()), 5)  # its whole tree


def test_update_step_is_not_started_once_stopping(monkeypatch):
    def no_popen(*a, **k):
        raise AssertionError("started a step although the builder is stopping")

    monkeypatch.setattr(subprocess, "Popen", no_popen)
    r = b._run_update_step(["git", "pull"], timeout=5, stop=lambda: True)
    assert r.returncode == 130


def test_polling_for_a_stop_does_not_multiply_heartbeats(monkeypatch):
    """stop is polled often; the server still gets one beat per interval."""
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.05)
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.5)
    polls: list = []
    beats: list = []
    r = b._run_update_step(
        [sys.executable, "-c", "import time; time.sleep(1.3)"],
        timeout=30,
        beat=lambda: beats.append(1),
        stop=lambda: polls.append(1) or False,
    )
    assert r.returncode == 0
    assert len(polls) >= 10
    assert 1 <= len(beats) <= 3


def test_a_step_with_no_beat_to_send_does_not_busy_poll(monkeypatch):
    """With no *beat*, the next beat never came due -- and once its time had
    passed, every wait was the 0.1 s floor: ten polls a second to the end."""
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.05)
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.4)
    polls: list = []
    r = b._run_update_step(
        [sys.executable, "-c", "import time; time.sleep(1.5)"],
        timeout=30,
        stop=lambda: polls.append(1) or False,
    )
    assert r.returncode == 0
    assert len(polls) <= 6, f"{len(polls)} polls in 1.5 s"


@posix_only
def test_a_step_that_rewrites_files_is_let_finish_on_a_stop(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.1)
    done = tmp_path / "done"
    started = time.monotonic()
    r = b._run_update_step(
        ["sh", "-c", f'sleep 1.5; touch "{done}"'],
        timeout=60,
        stop=lambda: time.monotonic() - started > 0.3,
        finish_on_stop="letting it finish",
    )
    assert r.returncode == 0 and done.exists()
    assert capsys.readouterr().out.count("shutdown requested; letting it finish") == 1


def test_a_step_that_rewrites_files_is_not_started_once_stopping(monkeypatch):
    def no_popen(*a, **k):
        raise AssertionError("started a step although the builder is stopping")

    monkeypatch.setattr(subprocess, "Popen", no_popen)
    r = b._run_update_step(["git", "merge"], timeout=5, stop=lambda: True, finish_on_stop="x")
    assert r.returncode == 130


@posix_only
def test_a_step_that_rewrites_files_still_times_out(monkeypatch):
    """Let finish on a stop, but not forever: the timeout still ends it."""
    monkeypatch.setattr(b, "_UPDATE_STOP_GRACE_SECS", 0.5)
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.1)
    started = time.monotonic()
    r = b._run_update_step(
        ["sh", "-c", "sleep 30"],
        timeout=1,
        stop=lambda: time.monotonic() - started > 0.2,
        finish_on_stop="x",
    )
    assert r.returncode == 124
    assert time.monotonic() - started < 5


@posix_only
def test_an_interrupted_builder_leaves_a_step_that_rewrites_files_running(monkeypatch, tmp_path):
    """KeyboardInterrupt in the builder (no flag-only handler: an embedder)
    while pip runs: pip goes on, in its own session, and finishes."""
    monkeypatch.setattr(b, "_UPDATE_BEAT_SECS", 0.2)
    done = tmp_path / "done"

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        b._run_update_step(
            ["sh", "-c", f'sleep 1; touch "{done}"'],
            timeout=30,
            beat=interrupt,
            finish_on_stop="x",
        )
    deadline = time.monotonic() + 10
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert done.exists()


@posix_only
def test_a_step_ended_early_gets_a_ctrl_c_first(tmp_path):
    """git removes its lock files on SIGINT (a SIGKILLed pull leaves
    .git/index.lock to fail every later pull) and pip its temp dirs."""
    marker = tmp_path / "cleaned"
    script = (
        "import signal, sys, time\n"
        "def on_int(*a):\n"
        f"    open({str(marker)!r}, 'w').write('yes')\n"
        "    sys.exit(3)\n"
        "signal.signal(signal.SIGINT, on_int)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    started = time.monotonic()
    r = b._run_update_step([sys.executable, "-c", script], timeout=1.5)
    assert r.returncode == 124
    assert marker.read_text() == "yes"
    assert "ready" in r.stdout  # its output is kept
    assert time.monotonic() - started < 1.5 + b._UPDATE_STOP_GRACE_SECS


def test_update_step_output_that_is_not_utf8_does_not_raise():
    """Localized git/pip messages in another encoding used to raise
    UnicodeDecodeError -- out of the builder.update handler, ending the
    builder's socket session."""
    r = b._run_update_step(
        [sys.executable, "-c", "import sys; sys.stderr.buffer.write(bytes([99, 97, 102, 0xE9]))"],
        timeout=30,
    )
    assert r.returncode == 0 and r.stderr.startswith("caf")


def test_git_config_in_another_encoding_does_not_raise(bare_git_env):
    config = bare_git_env / ".git" / "config"
    config.write_bytes(config.read_bytes() + b"[core]\n\tsshCommand = ssh -i /home/j\xf6rg/key\n")
    assert "core.sshcommand" in b._git_configured_keys(bare_git_env)
    assert "GIT_SSH_COMMAND" not in b._git_update_env(bare_git_env)


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


def test_resolve_update_source_ignores_an_upstream_it_cannot_fast_forward_to(git_checkout, capsys):
    (git_checkout / "local.txt").write_text("local work\n")
    _git("add", "local.txt", cwd=git_checkout)
    _git("commit", "-qm", "local", cwd=git_checkout)
    assert b._resolve_update_source() == (git_checkout, RUNNING)
    assert "diverged" in capsys.readouterr().err  # says why it ignored the upstream


def test_resolve_update_source_refuses_a_tree_that_differs_from_head(git_checkout, capsys):
    """Local edits -- or a pull ended part-way, whose half-new tree already
    says the new version: _self_update() will not install from it, so the
    builder must not drain for it."""
    pyproject = git_checkout / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(RUNNING, NEWER))  # as a cut-short pull
    assert b._resolve_update_source() is None
    err = capsys.readouterr().err
    assert "differ from HEAD" in err and "will not install from it" in err
    assert b._shell_join(["git", "-C", str(git_checkout), "status"]) in err


def test_resolve_update_source_forgets_an_upstream_deleted_on_the_remote(
    git_checkout, tmp_path, capsys
):
    """The remote-tracking ref says NEWER (an earlier fetch), then the branch
    is deleted on the remote.  A plain fetch succeeds and keeps the stale ref,
    yet the pull cannot fetch it: only the working tree counts."""
    origin = tmp_path / "origin"
    _git("fetch", "-q", cwd=git_checkout)
    _git("checkout", "-q", "-b", "other", cwd=origin)
    _git("branch", "-q", "-D", "master", cwd=origin)

    assert b._resolve_update_source() == (git_checkout, RUNNING)
    assert "nothing to merge" in capsys.readouterr().err
    # What the resolve has to predict: the update's fetch succeeds, its merge
    # cannot.
    _git("fetch", "--prune", "--quiet", cwd=git_checkout)
    merge = subprocess.run(
        ["git", "merge", "--ff-only", "--quiet", "@{upstream}"],
        cwd=git_checkout,
        capture_output=True,
    )
    assert merge.returncode != 0


def test_resolve_update_source_returns_nothing_once_stopping(git_checkout):
    assert b._resolve_update_source(stop=lambda: True) is None


@pytest.fixture
def real_git(git_checkout, monkeypatch):
    """_self_update with git for real; pip and the version probe stubbed."""
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
    return git_checkout, fake, reexecs


def _head(checkout: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_self_update_fast_forwards_installs_and_restarts(real_git):
    git_checkout, fake, reexecs = real_git
    b._self_update(token="tk")

    assert b._checkout_version(git_checkout) == NEWER
    (pip,) = fake.pips()
    assert pip[-1] == f"{git_checkout}[builder]"
    assert reexecs == ["tk"]


def test_a_tree_that_differs_from_head_is_never_installed(real_git, capsys):
    """The tree a pull ended mid-checkout leaves: HEAD on the old commit,
    pyproject.toml (it sorts before src/) already the new one.  The merge
    refuses ("would be overwritten"), and the tree, though it says NEWER, is
    not installed: no pip, no restart."""
    git_checkout, fake, reexecs = real_git
    head = _head(git_checkout)
    pyproject = git_checkout / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(RUNNING, NEWER))
    b._self_update(token="tk")

    assert fake.pips() == [] and fake.probes() == [] and reexecs == []
    assert _head(git_checkout) == head  # the merge did refuse
    err = capsys.readouterr().err
    assert "git merge --ff-only failed" in err
    assert "differ from HEAD" in err and "not installing" in err
    assert b._shell_join(["git", "-C", str(git_checkout), "status"]) in err


def test_a_tree_that_differs_from_head_after_a_failed_fetch_is_not_installed_either(
    monkeypatch, update_env, capsys
):
    _, reexecs = update_env
    steps = _Steps(fetch_answers=[(128, "fatal: unable to access")], diff_rc=1)
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert [steps.kind(c) for c in steps.cmds] == ["fetch", "diff"]  # no merge, no pip
    assert reexecs == []
    assert "not installing" in capsys.readouterr().err


@posix_only
def test_a_stop_during_the_merge_lets_it_finish(real_git, monkeypatch, capsys):
    """For real: a merge whose post-merge hook (part of the merge's process
    tree) takes 1.5 s.  The stop arrives meanwhile; the merge -- hook and
    all -- completes, the tree is the new commit, and nothing is installed."""
    git_checkout, fake, reexecs = real_git
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.1)
    marker = git_checkout.parent / "hook-done"
    hook = git_checkout / ".git" / "hooks" / "post-merge"
    hook.write_text(f'#!/bin/sh\nsleep 1.5\ntouch "{marker}"\n')
    hook.chmod(0o755)
    _git("fetch", "-q", cwd=git_checkout)  # so the update's fetch is quick
    started: list[float] = []

    def stop() -> bool:
        return bool(started) and time.monotonic() - started[0] > 0.3

    real_step = b._run_update_step

    def step(cmd, **kw):
        if cmd[:2] == ["git", "merge"]:
            started.append(time.monotonic())
        return real_step(cmd, **kw) if cmd[0] == "git" else fake(cmd, **kw)

    monkeypatch.setattr(b, "_run_update_step", step)
    b._self_update(token="tk", stop=stop)

    assert marker.exists(), "the merge was ended part-way"
    assert b._checkout_version(git_checkout) == NEWER
    assert subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=git_checkout).returncode == 0
    assert fake.pips() == [] and reexecs == []
    out = capsys.readouterr().out
    assert "letting git merge finish" in out and "staying on" in out


def test_git_errors_are_logged_by_their_first_and_last_lines():
    long = "error: Your local changes would be overwritten by merge:\n" + "".join(
        f"\tsrc/f{i:05d}.txt\n" for i in range(500)
    )
    text = b._git_error(long + "Aborting\n")
    assert text.startswith("error: Your local changes") and text.endswith("Aborting")
    assert "(500 more lines)" in text
    assert b._git_error("fatal: one line\n") == "fatal: one line"


# -- concurrent fetches in one checkout -----------------------------------------


def test_a_fetch_that_lost_a_ref_race_is_retried_once(monkeypatch, update_env, capsys):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_UPDATE_FETCH_RETRY_SECS", 0.1)
    lost = (
        1,
        "error: cannot lock ref 'refs/remotes/origin/master': is at 1111 but expected 2222",
    )
    steps = _Steps(fetch_answers=[lost, (0, "")])
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert [steps.kind(c) for c in steps.cmds][:3] == ["fetch", "fetch", "merge"]
    assert len(reexecs) == 1
    assert "collided with another fetch" in capsys.readouterr().out


def test_any_other_fetch_failure_is_not_retried(monkeypatch, update_env):
    steps = _Steps(fetch_answers=[(128, "fatal: could not read from remote"), (0, "")])
    monkeypatch.setattr(b, "_run_update_step", steps)
    b._self_update(token="tk")
    assert [steps.kind(c) for c in steps.cmds].count("fetch") == 1


def test_a_stop_while_waiting_to_retry_the_fetch_ends_the_update(monkeypatch, update_env):
    _, reexecs = update_env
    monkeypatch.setattr(b, "_UPDATE_FETCH_RETRY_SECS", 30)
    monkeypatch.setattr(b, "_UPDATE_STOP_POLL_SECS", 0.05)
    steps = _Steps(fetch_answers=[(1, "error: cannot lock ref 'refs/remotes/origin/master'")])
    monkeypatch.setattr(b, "_run_update_step", steps)
    started = time.monotonic()
    b._self_update(token="tk", stop=lambda: time.monotonic() - started > 0.3)
    assert time.monotonic() - started < 5
    assert [steps.kind(c) for c in steps.cmds] == ["fetch", "fetch"]  # the 2nd: not started
    assert reexecs == []


@posix_only
def test_concurrent_update_checks_in_one_checkout_both_see_the_upstream(git_checkout):
    """Two builders sharing a checkout, told to update by the same push, check
    for the update at once.  Their fetches race for refs/remotes/origin/master
    and the loser failed ("cannot lock ref ... is at <new> but expected
    <old>"), went by the working tree's older version, and dropped the update.

    A slow transport makes the race certain: protocol v0, so upload-pack
    advertises the refs at once (both fetches read the ref's old value), and
    then reads the client's request only after a second (both are still
    transferring when the first updates the ref)."""
    _git("config", "protocol.version", "0", cwd=git_checkout)
    _git("config", "remote.origin.uploadpack", "(sleep 1; cat) | git-upload-pack", cwd=git_checkout)
    results: dict = {}

    def check(k):
        results[k] = b._resolve_update_source()

    threads = [threading.Thread(target=check, args=(k,)) for k in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert results == {1: (git_checkout, NEWER), 2: (git_checkout, NEWER)}
