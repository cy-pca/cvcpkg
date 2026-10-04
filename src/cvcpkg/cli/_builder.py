# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CLI commands - auto-extracted from cli.py."""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import click

from cvcpkg._archive import safe_tar_extractall
from cvcpkg.cli import cli
from cvcpkg.cli._publish import _publish_to_server
from cvcpkg.cli._server import _api_request
from cvcpkg.heartbeat import unwatch as heartbeat_unwatch
from cvcpkg.heartbeat import watch as heartbeat_watch
from cvcpkg.optional import require_httpx
from cvcpkg.semver import version_sort_key

# Written last, inside a fully extracted cross-toolchain cache entry.  Its
# presence -- not "the directory exists and is non-empty" -- is what makes a
# cached toolchain usable, so concurrent jobs cannot pick up a partial tree.
_TC_CACHE_MARKER = ".cvcpkg-toolchain-complete"


def _toolchain_cache_ready(tc_cache_path: Path) -> bool:
    """True only when *tc_cache_path* holds a COMPLETE cached toolchain.

    Readiness is the completion marker, deliberately not "the directory exists
    and is non-empty": builders run several cross-compilation jobs at once
    against one shared cache, and the non-empty test was satisfied the moment
    the first job wrote its download into the cache directory.  A second job
    then symlinked a half-unpacked toolchain into its build prefix -- for emsdk
    that means emsdk_env.sh aborts with "unable to determine 'emsdk' directory"
    because emsdk.py has not been extracted yet.
    """
    return (tc_cache_path / _TC_CACHE_MARKER).is_file()


def _publish_toolchain_cache(staging: Path, cache_path: Path, stamp: str) -> None:
    """Publish a fully extracted *staging* tree as the cache entry *cache_path*.

    The marker is written last and the tree is moved with a single rename, so
    a concurrent job polling ``_toolchain_cache_ready`` observes the entry as
    either absent or complete -- never mid-extraction.  Losing a publish race
    is not an error: the winner's tree has identical content, so ours is
    simply discarded.
    """
    import shutil

    (staging / _TC_CACHE_MARKER).write_text(f"{stamp}\n")
    # A marker-less directory here is debris from an older cvcpkg or a crashed
    # run; nothing publishes into cache_path except this rename, so replacing
    # it cannot pull a tree out from under a concurrent job.
    if cache_path.exists() and not _toolchain_cache_ready(cache_path):
        shutil.rmtree(cache_path, ignore_errors=True)
    try:
        staging.rename(cache_path)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)


def _symlink_merge_into(src_root: Path, dst_root: Path) -> None:
    """Symlink the contents of *src_root* into *dst_root*, merging directories.

    A cross-toolchain is linked into a build prefix that may ALREADY contain
    top-level dirs (bin/, include/, lib/, share/) from dependency packages
    installed into the same prefix.  Symlinking a toolchain's same-named
    top-level dir is then skipped, silently dropping everything the toolchain
    ships beneath it -- e.g. wasi-sdk's ``share/wasi-sysroot`` and
    ``share/cmake/wasi-sdk.cmake``, so the compiler cannot find libc headers or
    the CMake toolchain file and the build fails deep into configure/compile.
    Recurse on directory collisions and symlink at the first level that is not
    already occupied.
    """
    for child in src_root.iterdir():
        dst = dst_root / child.name
        if not (dst.exists() or dst.is_symlink()):
            dst.symlink_to(child)
        elif child.is_dir() and dst.is_dir() and not dst.is_symlink():
            # Real directory on both sides -- merge their contents so neither
            # the dependency's nor the toolchain's subtree is lost.
            _symlink_merge_into(child, dst)
        # else: a real file or symlink already occupies dst -- keep it.


def _newest_first(pkg: dict) -> tuple:
    """Newest-first ordering key for a catalog entry (a dict with "version").

    Delegates to the canonical ``version_sort_key`` so the builder's dep
    selection agrees with the resolver, installer, and server -- one ordering,
    not five.  The +cvc.N tiebreak is why this exists: SemVer ignores build
    metadata, so ``8.3+cvc.2`` and ``8.3+cvc.1`` compare equal and a plain sort
    left the winner to the server's list order.  That is how a libpq build
    silently got the broken readline ``8.3+cvc.1`` (its libreadline.so predates
    the SHLIB_LIBS fix, so it declares no libtinfo, leaving tgetent unresolvable
    and failing both halves of libpq's readline probe).  The earlier local key
    additionally collapsed every *unparseable* version to one sentinel, which
    re-tied openssh/x264/llvm-cbe -- version_sort_key orders those too.
    """
    return version_sort_key(pkg.get("version", ""))


# Exit code the builder uses to ask its supervisor wrapper to pull the latest
# cvcpkg and relaunch it (Windows supervised self-update).  Kept in sync with
# windows/cvcpkg-builder-supervisor.cmd in the vm-provisioning repo.
_SUPERVISOR_RESTART_CODE = 90


# PEP 440 (the spec's appendix pattern).  cvcpkg's own versions travel in two
# spellings: pyproject.toml's as written ("2.5.0-rc1", what a checkout says it
# is) and the installed metadata's, which the build backend normalises
# ("2.5.0rc1", what ``cvcpkg.__version__`` and the server report).
_PEP440_RE = re.compile(
    r"""^\s*v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))
        |(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


def _pep440_key(version: str) -> tuple | None:
    """A PEP 440 ordering key for *version* (equal for equal versions), or None.

    The same order ``packaging.version.Version`` gives, for the forms cvcpkg
    uses -- ``packaging`` is not a cvcpkg dependency.
    """
    m = _PEP440_RE.match(version or "")
    if not m:
        return None
    release = [int(x) for x in m.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    # Slots are (rank, value): rank 0 sorts below any value, 2 above.
    has_pre, has_post, has_dev = m.group("pre"), m.group("post"), m.group("dev")
    if has_pre:
        letter = m.group("pre_l").lower()
        letter = {"alpha": "a", "beta": "b", "c": "rc", "pre": "rc", "preview": "rc"}.get(
            letter, letter
        )
        pre: tuple = (1, letter, int(m.group("pre_n") or 0))
    elif has_dev and not has_post:
        pre = (0, "", 0)  # 1.0.dev1 < 1.0a1
    else:
        pre = (2, "", 0)
    post = (1, int(m.group("post_n1") or m.group("post_n2") or 0)) if has_post else (0, 0)
    dev = (1, int(m.group("dev_n") or 0)) if has_dev else (2, 0)
    local: tuple = ()
    if m.group("local"):
        local = tuple(
            (1, int(p), "") if p.isdigit() else (0, 0, p.lower())
            for p in re.split(r"[-_.]", m.group("local"))
        )
    return (int(m.group("epoch") or 0), tuple(release), pre, post, dev, local)


def _is_newer_version(candidate: str, current: str) -> bool:
    """True when cvcpkg version *candidate* is newer than *current*.

    ``builder.update`` carries the server's own version.  A builder that is
    already at it, or ahead of it, has nothing to update to: acting on it
    would only drain the builder and restart it on the code it already runs.
    Compared as PEP 440 versions, so the two spellings of one version (see
    _PEP440_RE) are equal; failing that as SemVer, and failing both by plain
    inequality.
    """
    a, b = _pep440_key(candidate), _pep440_key(current)
    if a is not None and b is not None:
        return a > b
    from cvcpkg.semver import Version

    try:
        return Version.parse(candidate) > Version.parse(current)
    except ValueError:
        return candidate.strip() != current.strip()


def _same_version(a: str, b: str) -> bool:
    """True when *a* and *b* name the same cvcpkg version (see _is_newer_version)."""
    ka, kb = _pep440_key(a), _pep440_key(b)
    if ka is not None and kb is not None:
        return ka == kb
    from cvcpkg.semver import Version

    try:
        return Version.parse(a) == Version.parse(b)
    except ValueError:
        return a.strip() == b.strip()


def _pyproject_field(text: str, key: str) -> str | None:
    """The first top-level ``key = "value"`` in pyproject.toml *text*."""
    m = re.search(rf'^{key}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _checkout_version(checkout: Path) -> str | None:
    """The ``version`` a cvcpkg source checkout's pyproject.toml declares."""
    try:
        text = (checkout / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    return _pyproject_field(text, "version")


# -- Self-update (server-pushed ``builder.update``) ----------------------------
#
# A pip-installed builder updates itself from a cvcpkg source checkout: it
# resolves the checkout and the version it would install *before* it stops
# taking work (an update with nothing newer to install must not drain a busy
# builder for hours), then, once idle, pulls, pip-installs, checks that the
# install took, and re-execs.  Any failure leaves it running the code it runs.

# Explicit checkout to update from; when set, nothing else is searched.
_SELF_UPDATE_DIR_ENV = "CVCPKG_SELF_UPDATE_DIR"
# pip learned --break-system-packages (PEP 668) in 23.0.1.  Older pips -- 22.0.2
# is Ubuntu 22.04's -- reject it with "no such option", which used to fail
# every self-update on those hosts.
_PIP_BREAK_SYSTEM_PACKAGES_MIN = (23, 0, 1)
# Per-step timeouts.  Each step heartbeats every _UPDATE_BEAT_SECS while it runs
# (see _run_update_step): the server marks a builder silent for 180 s offline
# and fails the jobs dispatched to it.
_UPDATE_GIT_TIMEOUT = 60.0
_UPDATE_PIP_TIMEOUT = 300.0
_UPDATE_VERIFY_TIMEOUT = 60.0
_UPDATE_BEAT_SECS = 20.0
# After a timed-out step's process tree is killed: how long to wait for its
# output pipes to close.  A process that escaped the kill (it started its own
# session) can hold them open for as long as it lives; past this the step gives
# up on its output instead of blocking the builder with it.
_UPDATE_KILL_GRACE_SECS = 5.0
# A step that has to end early (timed out, or the builder is stopping) first
# gets a Ctrl-C (SIGINT to its process group, POSIX) and this long to exit on
# it -- git removes its lock files on SIGINT, and a SIGKILLed `git pull` leaves
# .git/index.lock behind to fail every later pull in that checkout; pip removes
# its temporary build directories -- before its tree is SIGKILLed.
_UPDATE_STOP_GRACE_SECS = 3.0
# How often a running step checks whether the builder was asked to stop (its
# `stop` callable).  The steps run in their own session, so the stop signal
# itself never reaches them: builder_run's handler only sets a flag.
_UPDATE_STOP_POLL_SECS = 1.0
_UPDATE_STOPPED_MSG = "stopped: the builder is shutting down"
# Builders that share a checkout -- a pip fleet's workers, or a dev and a prod
# unit on one host -- each get builder.update from their own server.  This
# lock (in the checkout's git dir) runs their updates one at a time: two
# concurrent `git pull`s collide on index.lock, and two concurrent pip
# installs into one site-packages can leave it broken.  The one that waits
# finds the version installed already and only restarts.  Waiting is bounded
# by the longest an update holding it can take (every step has a timeout).
_UPDATE_LOCK_NAME = "cvcpkg-self-update.lock"
_UPDATE_LOCK_TIMEOUT = (
    _UPDATE_GIT_TIMEOUT + 2 * _UPDATE_PIP_TIMEOUT + 2 * _UPDATE_VERIFY_TIMEOUT + 60.0
)

# git's network transports otherwise wait on a dead connection for as long as
# the kernel lets them.  Applied only where the user has not set their own
# (see _git_update_env).
_GIT_SSH_COMMAND = (
    "ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=2"
)
_GIT_LOW_SPEED = {
    # env var -> the git config key it overrides
    "GIT_HTTP_LOW_SPEED_LIMIT": ("http.lowspeedlimit", "1000"),  # bytes/s ...
    "GIT_HTTP_LOW_SPEED_TIME": ("http.lowspeedtime", "30"),  # ... for this many s
}


def _git_configured_keys(cwd: Path | str | None) -> set[str]:
    """Which transport settings git's own config sets for *cwd* (lowercase).

    One local ``git config`` read; any failure counts as "none set".
    """
    import subprocess

    try:
        r = subprocess.run(  # noqa: S603 - fixed command
            [
                "git",
                "config",
                "--get-regexp",
                r"^(core\.sshcommand|http\.lowspeedlimit|http\.lowspeedtime)$",
            ],
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            # A value in some other encoding (Latin-1, say) must not raise.
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split(None, 1)[0].lower() for line in r.stdout.splitlines() if line.strip()}


def _git_update_env(cwd: Path | str | None) -> dict[str, str]:
    """The environment a self-update's git commands run with.

    Unattended, and bounded: a remote that wants credentials fails the step
    instead of waiting on a prompt nobody will answer (terminal, or the
    Windows Git Credential Manager's dialog), and a stalled ssh or http
    transport gives up instead of holding the step open.  Each bound applies
    only when neither the environment nor git's config already sets it, so a
    builder's own ssh command or http limits win.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GCM_INTERACTIVE", "never")
    configured = _git_configured_keys(cwd)
    if not (env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH") or "core.sshcommand" in configured):
        env["GIT_SSH_COMMAND"] = _GIT_SSH_COMMAND
    for var, (key, value) in _GIT_LOW_SPEED.items():
        if not env.get(var) and key not in configured:
            env[var] = value
    return env


def _update_step_popen_kwargs() -> dict:
    """Start each step as the leader of its own process group / session.

    So a timed-out step can be killed as a whole tree -- git's ssh and
    git-remote-https helpers, pip's build backends.  It also means a signal
    meant for the builder (a Ctrl-C at its terminal, ``kill <pid>``) never
    reaches the step: a stopping builder ends the step itself, through
    _run_update_step's *stop*.
    """
    import subprocess

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def _kill_update_step(proc) -> None:
    """Kill *proc* and every process it started (best effort)."""
    import subprocess

    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603, S607 - fixed command
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal

        try:
            # The step leads its own session (start_new_session), so its pid
            # is its process group id -- never ours.
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # ESRCH: the whole group is gone already
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _collect_killed_step(proc) -> tuple[str, str]:
    """Output of a step whose tree was just killed, without waiting on stragglers.

    ``communicate()`` with no timeout waits for EOF on stdout/stderr, and any
    descendant that survived the kill and inherited them keeps them open --
    for a git transport stuck on a dead connection, indefinitely.  So wait a
    bounded time, then drop the pipes and settle for no output.
    """
    import subprocess

    try:
        out, err = proc.communicate(timeout=_UPDATE_KILL_GRACE_SECS)
        return out or "", err or ""
    except subprocess.TimeoutExpired:
        pass
    if sys.platform != "win32":
        # POSIX communicate() read on this thread, so nothing else holds these.
        # (On Windows its reader threads still do, and closing would block on
        # them; they are daemon threads and end when the straggler does.)
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
    try:
        proc.wait(timeout=_UPDATE_KILL_GRACE_SECS)
    except subprocess.TimeoutExpired:
        pass
    return "", "(output lost: a process the step started outlived the kill)"


def _end_update_step(proc) -> tuple[str, str]:
    """End a running step and everything it started; return its output.

    First as a Ctrl-C would (POSIX: SIGINT to the step's process group), so
    git can remove its lock files and pip its temporary directories; whatever
    still runs _UPDATE_STOP_GRACE_SECS later is killed with its tree.
    (Windows has no group-wide Ctrl-C for a process without a console of its
    own, so there it is the tree kill straight away.)
    """
    import subprocess

    if sys.platform != "win32":
        import signal

        try:
            # The step leads its own session, so this is its group, never ours.
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:  # ESRCH: nothing left in the group
            pass
        try:
            out, err = proc.communicate(timeout=_UPDATE_STOP_GRACE_SECS)
        except subprocess.TimeoutExpired:
            pass
        else:
            _kill_update_step(proc)  # anything it left behind
            return out or "", err or ""
    _kill_update_step(proc)
    return _collect_killed_step(proc)


def _run_update_step(
    cmd: list[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    beat: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
):
    """Run one self-update command, heartbeating while it runs.

    Returns a ``subprocess.CompletedProcess`` with text stdout/stderr (bytes
    that are not valid in the locale's encoding are replaced, never raised
    on).  A command that cannot be started (no git on PATH) comes back as exit
    127, and one that overruns *timeout* is ended -- with everything it
    started -- and comes back as exit 124 within a few seconds of the
    deadline; the caller treats both as a failed step, never as an exception.

    *stop* is polled about once a second: once it returns True the step is
    ended the same way and comes back as exit 130 (and is not started at all
    if it already does).  The step runs in its own session, so this is the
    only way a builder's stop request reaches it -- builder_run's signal
    handler only sets a flag.  *beat* is still called only every
    _UPDATE_BEAT_SECS.
    """
    import subprocess

    if stop is not None and stop():
        return subprocess.CompletedProcess(cmd, 130, "", _UPDATE_STOPPED_MSG)
    env = _git_update_env(cwd) if cmd and cmd[0] == "git" else None
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed commands
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            **_update_step_popen_kwargs(),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    started = time.monotonic()
    deadline = started + timeout
    next_beat = started + _UPDATE_BEAT_SECS
    poll = min(_UPDATE_STOP_POLL_SECS, _UPDATE_BEAT_SECS) if stop is not None else _UPDATE_BEAT_SECS
    try:
        while True:
            now = time.monotonic()
            wait = max(0.1, min(poll, deadline - now, next_beat - now))
            try:
                out, err = proc.communicate(timeout=wait)
                return subprocess.CompletedProcess(cmd, proc.returncode, out or "", err or "")
            except subprocess.TimeoutExpired:
                pass
            if stop is not None and stop():
                out, err = _end_update_step(proc)
                return subprocess.CompletedProcess(
                    cmd, 130, out, f"{err}\n{_UPDATE_STOPPED_MSG}".lstrip("\n")
                )
            now = time.monotonic()
            if now >= deadline:
                break
            if beat is not None and now >= next_beat:
                beat()
                next_beat = now + _UPDATE_BEAT_SECS
        if beat is not None:
            beat()  # ending the step can take a few seconds more
        out, err = _end_update_step(proc)
        return subprocess.CompletedProcess(cmd, 124, out, err + f"\ntimed out after {timeout:.0f}s")
    except BaseException:
        # KeyboardInterrupt (or a failing beat) while the step runs: it is in
        # its own session, so nothing else will stop it.  Unconditionally --
        # the step's own process may be gone while what it started is not, and
        # its group id stays reserved (no pid reuse) while any member lives.
        try:
            _end_update_step(proc)
        except BaseException:  # interrupted again: no more grace
            _kill_update_step(proc)
        raise


def _installed_source_dir() -> Path | None:
    """The local directory pip installed this cvcpkg from, if any.

    pip records it in the distribution's ``direct_url.json`` (PEP 610) for
    both ``pip install <dir>`` and ``pip install -e <dir>`` -- which is how a
    non-editable builder install still finds the checkout it came from.
    """
    try:
        from importlib.metadata import distribution

        raw = distribution("cvcpkg").read_text("direct_url.json")
    except Exception:  # noqa: BLE001 - not installed as a distribution, etc.
        return None
    if not raw:
        return None
    try:
        url = str(json.loads(raw).get("url") or "")
    except (ValueError, AttributeError):
        return None
    if not url.startswith("file:"):
        return None
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    return Path(url2pathname(unquote(urlparse(url).path)))


def _self_update_candidates() -> list[Path]:
    """Where to look for the cvcpkg checkout to update from, in order.

    Only checkouts this install is tied to: ``CVCPKG_SELF_UPDATE_DIR`` (and
    nothing else when it is set), the directory pip installed from, and the
    checkout this code runs out of.  Never a guessed path such as
    ``~/src/cvc/cvcpkg``: on a builder host that is as likely a developer's
    clone on a feature branch, and ``git pull`` + pip install there would put
    unreviewed code on the builder.  A PyPI install has none of these, so it
    updates only once ``CVCPKG_SELF_UPDATE_DIR`` names a checkout.
    """
    explicit = os.environ.get(_SELF_UPDATE_DIR_ENV, "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    out: list[Path] = []
    src = _installed_source_dir()
    if src is not None:
        out.append(src)
    # A source/editable checkout: <repo>/src/cvcpkg/cli/_builder.py.
    parents = Path(__file__).resolve().parents
    if len(parents) > 3:
        out.append(parents[3])
    seen: set[Path] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _find_update_checkout() -> Path | None:
    """The first candidate that is a git checkout of cvcpkg itself."""
    for c in _self_update_candidates():
        try:
            text = (c / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            continue
        if _pyproject_field(text, "name") == "cvcpkg" and (c / ".git").exists():
            return c
    return None


def _resolve_update_source(
    beat: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> tuple[Path, str] | None:
    """``(checkout, version)`` a self-update would install, or None.

    Must predict what _self_update() -- ``git pull --ff-only``, then pip
    install of whatever the working tree holds -- will actually install, since
    the builder drains (stops taking work, possibly for hours) on the strength
    of it.  So it fetches (``--prune``: an upstream branch deleted on the
    remote must not live on as a stale remote-tracking ref the pull cannot
    fetch), and counts on the upstream's version only when the pull can
    deliver it: the fetch succeeded (a failed one leaves a stale
    remote-tracking ref), HEAD is an ancestor of the upstream (a diverged
    branch cannot fast-forward) and no tracked file is modified (local edits
    can make the pull refuse).  Otherwise the pull would fail or change
    nothing, and the working tree's version is what gets installed; it logs
    which case it was.

    Not detected: an untracked file the pull would have to overwrite, which
    also makes the pull refuse.  That costs one drain for nothing -- the
    update then installs the working tree's version only if that is newer.

    It reads the upstream with ``git show`` and never touches the working
    tree: an editable install runs straight out of the checkout, and jobs are
    still building.  None, too, once *stop* returns True.
    """
    checkout = _find_update_checkout()
    if checkout is None:
        return None
    local = _checkout_version(checkout)

    def git(*args: str):
        return _run_update_step(
            ["git", *args], cwd=checkout, timeout=_UPDATE_GIT_TIMEOUT, beat=beat, stop=stop
        )

    def stopping() -> bool:
        return stop is not None and stop()

    def working_tree(why: str) -> tuple[Path, str] | None:
        click.echo(
            f"  self-update: {checkout}: {why}; going by its working tree "
            f"({local or 'no version'}), not its upstream",
            err=True,
        )
        return (checkout, local) if local else None

    fetched = git("fetch", "--prune", "--quiet")
    if stopping():
        return None
    if fetched.returncode != 0:
        return working_tree(
            f"git fetch failed (exit {fetched.returncode}): {fetched.stderr.strip()[-300:]}"
        )
    ancestor = git("merge-base", "--is-ancestor", "HEAD", "@{upstream}")
    if stopping():
        return None
    if ancestor.returncode == 1:
        return working_tree(
            "HEAD has commits its upstream does not (diverged), so a fast-forward pull "
            "cannot update it"
        )
    if ancestor.returncode != 0:
        return working_tree(
            "nothing to pull: a detached HEAD, no upstream branch, or an upstream "
            f"deleted on the remote ({ancestor.stderr.strip()[-200:]})"
        )
    modified = git("diff", "--quiet", "HEAD")
    if stopping():
        return None
    if modified.returncode != 0:
        return working_tree("tracked files are modified, and the pull may refuse")
    shown = git("show", "@{upstream}:pyproject.toml")
    if stopping():
        return None
    upstream = _pyproject_field(shown.stdout, "version") if shown.returncode == 0 else None
    if upstream is None:
        return working_tree("cannot read a version from the upstream's pyproject.toml")
    return (checkout, upstream)


def _parse_version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.match(r"\s*(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _pip_supports_break_system_packages() -> bool:
    """True when this interpreter's pip accepts ``--break-system-packages``."""
    try:
        from importlib.metadata import version

        found = _parse_version_tuple(version("pip"))
    except Exception:  # noqa: BLE001 - no pip metadata: assume an old pip
        return False
    return found is not None and found >= _PIP_BREAK_SYSTEM_PACKAGES_MIN


def _installed_in_user_site() -> bool:
    """True when this cvcpkg runs from the user site (a ``pip install --user``)."""
    import site

    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return False  # a venv: --user is an error there
    if not getattr(site, "ENABLE_USER_SITE", False):
        return False
    try:
        user_site = Path(site.getusersitepackages()).resolve()
        Path(__file__).resolve().relative_to(user_site)
    except (ValueError, OSError):
        return False
    return True


def _pip_install_cmd(checkout: Path, *, break_system_packages: bool) -> list[str]:
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    if break_system_packages:
        cmd.append("--break-system-packages")
    if _installed_in_user_site():
        # Install where the running copy lives, or the re-exec finds it again.
        cmd.append("--user")
    # [builder] keeps the agent's own optional deps (websockets) installed.
    cmd.append(f"{checkout}[builder]")
    return cmd


def _reexec_args(args: list[str]) -> list[str]:
    """*args* (``sys.argv[1:]``) for the re-exec'd builder.

    Drops ``--token`` (the successor gets it through ``CVCPKG_TOKEN``, so a
    self-update never re-publishes it on the command line) and ``--daemon``
    (the process is already detached; forking again would change its PID out
    from under the pidfile and the service manager).
    """
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--token":
            skip = True
            continue
        if a.startswith("--token=") or a == "--daemon":
            continue
        out.append(a)
    return out


def _reexec_argv(args: list[str]) -> list[str]:
    """The argv that restarts this builder on the freshly installed cvcpkg.

    ``[python, -m, cvcpkg, ...]`` for a pip install and ``[binary, ...]`` for
    the single-file binary -- never ``[python] + sys.argv``: under
    ``python -m cvcpkg`` (every fleet worker) ``sys.argv[0]`` is
    ``.../cvcpkg/__main__.py``, and running that as a script puts the package
    directory first on ``sys.path``, where cvcpkg/platform.py shadows the
    stdlib ``platform`` and the successor dies at startup.
    """
    from cvcpkg.selfexec import cvcpkg_argv

    return cvcpkg_argv(*_reexec_args(args))


def _reexec_builder(token: str, extra_env: dict[str, str] | None = None) -> None:
    """Replace this process with a fresh builder (POSIX; same PID)."""
    argv = _reexec_argv(sys.argv[1:])
    env = dict(os.environ)
    env.update(extra_env or {})
    if token:
        env["CVCPKG_TOKEN"] = token
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(argv[0], argv, env)


def _fresh_cvcpkg_version(
    beat: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> tuple[str | None, str]:
    """``(version, error)``: what a freshly started interpreter imports.

    *version* is None when the import failed, and *error* then says why.
    ``cvcpkg.__version__`` is the installed distribution's metadata, which
    only a pip install changes -- for an editable install too, whose code a
    ``git pull`` alone already changes.
    """
    probe = _run_update_step(
        [sys.executable, "-c", "import cvcpkg; print(cvcpkg.__version__)"],
        # Not the checkout: its directory must not shadow the installed copy.
        cwd=os.path.abspath(os.sep),
        timeout=_UPDATE_VERIFY_TIMEOUT,
        beat=beat,
        stop=stop,
    )
    if probe.returncode != 0:
        return None, probe.stderr.strip()[-300:] or f"exit {probe.returncode}"
    lines = probe.stdout.strip().splitlines()
    if not lines:
        return None, "it printed no version"
    return lines[-1].strip(), ""


def _git_dir(checkout: Path) -> Path:
    """*checkout*'s git directory: ``.git``, or where a ``.git`` file points
    (a linked worktree or a submodule)."""
    dot = checkout / ".git"
    try:
        if dot.is_file():
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith("gitdir:"):
                target = Path(text[len("gitdir:") :].strip())
                return target if target.is_absolute() else checkout / target
    except OSError:
        pass
    return dot


def _try_lock(fd: int) -> bool:
    """Take an exclusive lock on *fd* without waiting: False if another holds it.

    Other failures (a filesystem without locks) raise OSError.  Released when
    the fd is closed -- by the kernel too, if the holder dies, and across an
    exec, since Python opens it non-inheritable -- so it never goes stale.
    flock() where there is one, else (Windows) msvcrt.locking() on byte 0.
    """
    try:
        import fcntl
    except ImportError:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except PermissionError:  # EACCES: locked by another process
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # EWOULDBLOCK / EAGAIN: locked elsewhere
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        import fcntl
    except ImportError:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _self_update_lock(
    checkout: Path,
    *,
    beat: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> Iterator[bool]:
    """Hold *checkout*'s self-update lock (see _UPDATE_LOCK_NAME) for the block.

    Yields True once held -- or when the lock cannot be used at all (the git
    dir is not writable, the filesystem has no locks), in which case the
    update goes ahead unlocked, as it always used to, and says so.  Yields
    False, after logging why, when *stop* turned True or _UPDATE_LOCK_TIMEOUT
    passed while another builder held it.  Heartbeats while it waits.
    """
    path = _git_dir(checkout) / _UPDATE_LOCK_NAME
    try:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        except PermissionError:
            # Created by another user's builder: a read-only fd locks too.
            fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        click.echo(f"  self-update: cannot open {path} ({exc}); updating unlocked", err=True)
        yield True
        return
    locked = False
    try:
        acquired = False
        started = time.monotonic()
        next_beat = started + _UPDATE_BEAT_SECS
        waiting = False
        while True:
            try:
                locked = _try_lock(fd)
            except OSError as exc:
                click.echo(
                    f"  self-update: cannot lock {path} ({exc}); updating unlocked", err=True
                )
                acquired = True
                break
            if locked:
                acquired = True
                break
            if not waiting:
                click.echo(
                    f"  self-update: another builder sharing {checkout} is updating; "
                    "waiting for it to finish"
                )
                waiting = True
            now = time.monotonic()
            if stop is not None and stop():
                click.echo("  self-update: shutdown requested while waiting for the lock")
                break
            if now - started >= _UPDATE_LOCK_TIMEOUT:
                click.echo(
                    f"  self-update: another builder has held {path} for "
                    f"{now - started:.0f}s; giving up on this update",
                    err=True,
                )
                break
            if beat is not None and now >= next_beat:
                beat()
                next_beat = now + _UPDATE_BEAT_SECS
            time.sleep(min(_UPDATE_STOP_POLL_SECS, _UPDATE_BEAT_SECS))
        yield acquired
    finally:
        if locked:
            try:
                _unlock(fd)
            except OSError:
                pass
        os.close(fd)


# _ws_catch_up stops behind a job whose claim has not landed yet (see there).
# The socket loop then re-runs it on its next turns, waiting at most this long
# for a message in between -- so pushes are still handled between attempts --
# for up to _CATCH_UP_RERUN_SECS without progress before leaving the rest to
# the periodic sweep.  A claim normally lands within a second.
_CATCH_UP_RERUN_RECV_TIMEOUT = 0.5
_CATCH_UP_RERUN_SECS = 30.0

# How long an extracted recipe directory may sit before it is swept.  Well
# above any job timeout so a sweep cannot delete a directory a build is using.
_RECIPE_DIR_TTL_SECS = 24 * 60 * 60


class _ReconnectBackoff:
    """Capped exponential backoff, with jitter, for the builder's WebSocket.

    ``next_delay()`` yields ``minimum``, ``2 * minimum``, ``4 * minimum`` ...
    up to ``maximum``, each scaled by a random factor in ``[1 - jitter,
    1 + jitter]`` (and never above ``maximum``) so builders that lost the
    server together do not all retry in the same second.  ``reset()`` starts
    the sequence over, after a connection that stayed up.
    """

    def __init__(
        self,
        minimum: float,
        maximum: float,
        *,
        jitter: float = 0.2,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.minimum = max(0.0, minimum)
        self.maximum = max(self.minimum, maximum)
        self.jitter = min(max(jitter, 0.0), 1.0)
        self._rand = rand
        self._next = self.minimum

    def reset(self) -> None:
        self._next = self.minimum

    def next_delay(self) -> float:
        step = self._next
        self._next = min(self._next * 2, self.maximum)
        scaled = step * (1.0 + self.jitter * (2.0 * self._rand() - 1.0))
        return min(max(scaled, 0.0), self.maximum)


# -- Builder commands --------------------------------------------


@cli.group("builder")
def builder_group() -> None:
    """Manage remote build agents."""


@builder_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--platform", default=None, help="Filter by platform.")
@click.option("--arch", default=None, help="Filter by architecture.")
@click.option("--status", default=None, help="Filter by status (online/offline/busy).")
def builder_list(
    server: str, token: str, platform: str | None, arch: str | None, status: str | None
):
    """List registered builders."""
    httpx = require_httpx("builder")

    params: dict[str, str] = {}
    if platform:
        params["platform"] = platform
    if arch:
        params["arch"] = arch
    if status:
        params["status"] = status
    url = f"{server.rstrip('/')}/v1/builders"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    builders = data.get("builders", [])
    if not builders:
        click.echo("No builders registered.")
        return
    click.echo(
        f"{'ID':>5}  {'Name':<24} {'Platform':<10} {'Arch':<10} {'Status':<8} "
        f"{'Jobs':>4}  {'Disk':>8}  Capabilities"
    )
    click.echo("-" * 96)
    for b in builders:
        # Flag-style capabilities (cuda, ...); cross_platforms is a list with
        # its own display in `builder status`, not a flag.
        flags = ", ".join(
            sorted(
                k for k, v in (b.get("capabilities") or {}).items() if k != "cross_platforms" and v
            )
        )
        # '?' rather than '0' for a builder that advertises nothing: the
        # scheduler treats it as unknown, and the column must not read as an
        # out-of-space host.
        _disk = b.get("free_disk_gb")
        disk = f"{_disk} GiB" if _disk is not None else "?"
        click.echo(
            f"{b['id']:>5}  {b['name']:<24} {b['platform']:<10} {b['arch']:<10} "
            f"{b['status']:<8} {b['current_jobs']}/{b['max_jobs']:>3}  {disk:>8}  {flags}"
        )


@builder_group.command("status")
@click.argument("builder_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builder_status(builder_id: int, server: str, token: str):
    """Show details for a specific builder."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{data['id']}: {data['name']}")
    click.echo(f"  Org:         {data.get('org_slug') or '(global)'}")
    click.echo(f"  Platform:    {data['platform']}/{data['arch']}")
    click.echo(f"  Status:      {data['status']}")
    click.echo(f"  Jobs:        {data['current_jobs']}/{data['max_jobs']}")
    click.echo(f"  Labels:      {', '.join(data.get('labels', [])) or '(none)'}")
    cross = data.get("capabilities", {}).get("cross_platforms", [])
    if cross:
        if cross and isinstance(cross[0], dict):
            cross_strs = [f"{e['platform']}/{e['arch']}" for e in cross]
        else:
            cross_strs = cross
        click.echo(f"  Cross:       {', '.join(cross_strs)}")
    cap_flags = sorted(
        k for k, v in (data.get("capabilities") or {}).items() if k != "cross_platforms" and v
    )
    if cap_flags:
        click.echo(f"  Capabilities: {', '.join(cap_flags)}")
    _disk = data.get("free_disk_gb")
    _disk_str = (
        f"{_disk} GiB on the work volume (as of the last heartbeat)"
        if _disk is not None
        else "not advertised (treated as unknown, never as full)"
    )
    click.echo(f"  Free disk:   {_disk_str}")
    click.echo(f"  Affinity:    {'yes' if data.get('prefer_affinity') else 'no'}")
    click.echo(f"  Last HB:     {data.get('last_heartbeat') or 'never'}")
    click.echo(f"  Registered:  {data.get('created_at', 'unknown')}")


@builder_group.command("gc")
@click.option(
    "--work-dir",
    type=click.Path(),
    default="/tmp/cvcpkg-builder",
    show_default=True,
    help="Builder work dir to sweep for orphaned job scratch trees.",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="",
    help="Download cache to prune.  [default: the resolved cvcpkg cache dir]",
)
@click.option(
    "--max-age",
    type=float,
    default=21600,
    show_default=True,
    help="Only remove job dirs older than this many seconds.  0 removes ALL of "
    "them — correct only when no builder is running against this work dir.",
)
@click.option(
    "--cache-max-age",
    type=float,
    default=1209600,
    show_default=True,
    help="Prune cache entries older than this many seconds (0 disables).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be reclaimed without deleting anything.",
)
def builder_gc(
    work_dir: str,
    cache_dir: str,
    max_age: float,
    cache_max_age: float,
    dry_run: bool,
):
    """Reclaim disk from orphaned build scratch dirs and the download cache.

    A running builder already does this itself — it sweeps orphans at startup
    and on an interval — so this command is for hosts that want an explicit
    cron/timer, for one-off recovery on a full builder, and for inspecting the
    damage with ``--dry-run``.

    Job dirs are stranded when a builder is killed mid-build (a deploy restart,
    SIGKILL, OOM): the in-process cleanup never runs.  They are safe to remove
    once no builder is working in them, which ``--max-age`` approximates.
    """
    from cvcpkg.builder_gc import sweep_cache, sweep_work_dir
    from cvcpkg.cache import default_cache_dir

    cdir = Path(cache_dir) if cache_dir else default_cache_dir()
    work = sweep_work_dir(work_dir, max_age_seconds=max_age, dry_run=dry_run)
    cache = sweep_cache(cdir, max_age_seconds=cache_max_age, dry_run=dry_run)

    verb = "would reclaim" if dry_run else "reclaimed"
    click.echo(f"work dir {work_dir}: {verb} {work.removed} dir(s), {work.freed_mib:.0f} MiB")
    click.echo(f"cache {cdir}: {verb} {cache.removed} entr(ies), {cache.freed_mib:.0f} MiB")
    total_mib = work.freed_mib + cache.freed_mib
    click.echo(f"total: {verb} {total_mib:.0f} MiB")


# Fleet worker restarts (see _supervise_fleet).  A worker that keeps dying
# soon after it starts -- a revoked token, a pidfile held by a stray builder, a
# bad config -- is restarted on a doubling delay up to this cap, instead of every
# few seconds forever: each start of the single-file binary unpacks a fresh copy
# of itself (~85 MB) into TMPDIR, so a flat 5 s loop churns ~17 MB/s of disk.
_FLEET_RESPAWN_MAX_DELAY = 300.0
# A worker that ran at least this long before exiting was healthy; its next
# restart starts over at the configured --restart-delay.
_FLEET_WORKER_STABLE_SECS = 60.0
# How long the fleet waits for its workers to drain their in-flight jobs on
# shutdown before killing them.
_FLEET_DRAIN_SECS = 120.0
# How often the supervisor checks on its workers.
_FLEET_POLL_SECS = 1.0


def _next_respawn_delay(previous: float, base: float, lived: float) -> float:
    """Delay before restarting a worker that exited after *lived* seconds.

    *previous* is the delay used for its last restart (0 before the first).
    A worker that lived at least ``_FLEET_WORKER_STABLE_SECS`` restarts after
    *base*; one that died sooner waits twice as long as last time, capped at
    ``_FLEET_RESPAWN_MAX_DELAY`` (never below *base*).
    """
    if base <= 0:
        return 0.0
    if previous <= 0 or lived >= _FLEET_WORKER_STABLE_SECS:
        return base
    return min(max(previous * 2, base), max(base, _FLEET_RESPAWN_MAX_DELAY))


def _supervise_fleet(fleet, restart_delay: float) -> None:
    """Run one `cvcpkg builder run` worker per configured server.

    Each worker is a separate process whose environment holds only its own
    server's token (as ``CVCPKG_TOKEN``; never on its argv) -- see
    ``builder_fleet.worker_env``.  Workers that exit are restarted, on a
    capped exponential backoff while they keep dying young; SIGINT/SIGTERM
    (and Ctrl+Break on Windows) is forwarded so the whole fleet drains
    gracefully together.
    """
    import signal
    import subprocess
    import threading
    import time

    from cvcpkg.builder_fleet import worker_argv, worker_env
    from cvcpkg.selfexec import cvcpkg_argv, cvcpkg_env

    windows = sys.platform == "win32"
    # POSIX: each worker leads its own process group (see _spawn).
    own_group = hasattr(os, "killpg") and not windows
    stopping = threading.Event()
    procs: dict[str, subprocess.Popen] = {}
    started: dict[str, float] = {}
    delays: dict[str, float] = {}
    respawn_at: dict[str, float] = {}

    def _spawn(fs):
        # `python -m cvcpkg builder run ...` from a pip install, but
        # `<binary> builder run ...` from the single-file binary, which would
        # reject `-m` as an unknown option and crash-loop every worker.
        # A worker is long-lived and may be respawned after the binary on
        # disk was replaced, so a frozen worker unpacks its own copy rather
        # than borrowing this process's.
        argv = cvcpkg_argv(*worker_argv(fs))
        kw: dict = {}
        if own_group:
            # The frozen binary runs as a launcher + interpreter pair and only
            # the launcher is ours to wait on; if it dies alone, the
            # interpreter (the PID in the worker's pidfile) keeps building and
            # holds the single-instance guard, so every respawn would exit at
            # once.  Its own group lets _reap stop all of it.
            kw["start_new_session"] = True
        elif windows:
            # Its own console process group: the only way to deliver a
            # graceful stop (CTRL_BREAK_EVENT) to one worker -- SIGINT cannot
            # be sent to a process on Windows at all.
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        started[fs.name] = time.time()
        return subprocess.Popen(  # noqa: S603 - argv built from config
            argv,
            env=worker_env(fs, fleet.servers, cvcpkg_env()),
            **kw,
        )

    def _kill_tree(p) -> None:
        """Windows: kill worker *p* and every process it started."""
        try:
            subprocess.run(  # noqa: S603, S607 - fixed command, our own child's pid
                ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception:  # noqa: BLE001 - best effort
            pass

    def _reap(p) -> None:
        """Stop whatever outlived worker *p*'s launcher."""
        if own_group:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        elif windows:
            _kill_tree(p)

    def _ask_to_stop(p) -> None:
        """Ask worker *p* to finish its in-flight jobs and exit."""
        if p.poll() is not None:
            return
        sig = getattr(signal, "CTRL_BREAK_EVENT", 1) if windows else signal.SIGINT
        try:
            p.send_signal(sig)
        except Exception:  # noqa: BLE001 - best effort (e.g. no console on Windows)
            pass

    def _handle_signal(signum, _frame):
        stopping.set()
        for p in procs.values():
            _ask_to_stop(p)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break / CTRL_BREAK_EVENT
        signal.signal(signal.SIGBREAK, _handle_signal)

    for fs in fleet.servers:
        procs[fs.name] = _spawn(fs)
        click.echo(f"started worker {fs.name} (pid {procs[fs.name].pid}) -> {fs.server}")

    while not stopping.is_set():
        if stopping.wait(_FLEET_POLL_SECS):
            break
        now = time.time()
        for fs in fleet.servers:
            if stopping.is_set():
                break
            p = procs[fs.name]
            if fs.name in respawn_at:
                # Exited earlier; restart once its delay is up.  Scheduled
                # rather than slept, so one worker in a long backoff does not
                # hold up noticing (and restarting) the others.
                if now >= respawn_at[fs.name]:
                    del respawn_at[fs.name]
                    procs[fs.name] = _spawn(fs)
                    click.echo(f"restarted worker {fs.name} (pid {procs[fs.name].pid})")
                continue
            if p.poll() is not None:
                _reap(p)
                lived = now - started.get(fs.name, now)
                delay = _next_respawn_delay(delays.get(fs.name, 0.0), restart_delay, lived)
                delays[fs.name] = delay
                respawn_at[fs.name] = now + delay
                click.echo(
                    f"worker {fs.name} exited (code {p.returncode}) after {lived:.0f}s; "
                    f"restarting in {delay:g}s"
                )

    # Graceful drain: workers finish in-flight jobs on SIGINT / Ctrl+Break.
    deadline = time.time() + _FLEET_DRAIN_SECS
    for name, p in procs.items():
        try:
            p.wait(timeout=max(1.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            click.echo(f"worker {name} did not exit in time; terminating")
            if windows:
                # TerminateProcess on the launcher alone would orphan the
                # single-file binary's interpreter and its build children.
                _kill_tree(p)
            else:
                p.terminate()


@builder_group.command("fleet")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Fleet config YAML: servers[].{server, token|token_env, serve[]}.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the worker command for each server and exit (no processes spawned).",
)
@click.option(
    "--restart-delay",
    type=float,
    default=5.0,
    help="Seconds to wait before restarting a crashed worker (default: 5).",
)
def builder_fleet(config_path: str, dry_run: bool, restart_delay: float) -> None:
    """Supervise a multi-homed builder fleet across several cvcpkg servers.

    Runs one ``cvcpkg builder run`` worker per server in the config file, so a
    single machine (and a single service unit) serves multiple registries at
    once — e.g. the public ``cvcpkg.org`` and an org's edge server — instead of
    running a separate builder deployment per server. Each worker is given only
    its own server's token, in its environment rather than on its command
    line, and keeps it out of the environment of the builds it runs.  All
    workers share this process's uid, so servers that must not be able to read
    each other's tokens need separate ``builder run`` services under separate
    users instead.
    """
    from cvcpkg.builder_fleet import FleetConfigError, load_fleet_config, worker_argv

    try:
        fleet = load_fleet_config(config_path)
    except FleetConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"fleet '{fleet.name}': {len(fleet.servers)} server(s)")
    if dry_run:
        tokens = {s.token for s in fleet.servers if s.token}
        for fs in fleet.servers:
            # worker_argv carries no token; mask defensively all the same.
            masked = ["***" if a in tokens else a for a in worker_argv(fs)]
            source = f"${fs.token_env}" if fs.token_env else "the literal 'token'"
            click.echo(f"  {fs.name} [{fs.server}] serves {list(fs.serve)}")
            click.echo(f"    CVCPKG_TOKEN=*** (from {source}) cvcpkg " + " ".join(masked))
        return
    _supervise_fleet(fleet, restart_delay)


def _self_update(
    token: str = "",
    beat: Callable[[], None] | None = None,
    extra_env: dict[str, str] | None = None,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Pip-install a newer cvcpkg from its source checkout and re-exec.

    Returns -- leaving the builder on the code it runs -- when there is
    nothing newer to install, or when any step fails.  *beat* is called while
    the slow steps run, to keep the builder's heartbeat going.  *extra_env* is
    added to the successor's environment, along with ``CVCPKG_TOKEN``.

    *stop* says the builder was asked to stop.  Once it returns True the
    update ends where it is -- a running step is ended (see _run_update_step)
    -- and it never re-execs: a re-exec would start a fresh builder that
    takes work again, the stop request lost with the old process image.  It
    is checked on entry, after the pull, after pip, and right before the
    re-exec.

    Builders sharing the checkout update one at a time (_self_update_lock),
    and one that finds the version installed already only restarts.
    """
    # Stopping: nothing to update, and certainly no re-exec (see *stop*).
    if stop is not None and stop():
        click.echo("  self-update: shutdown requested; not updating")
        return

    # The single-file binary cannot update itself this way: `sys.executable`
    # is cvcpkg, not Python, so `-m pip` is rejected as a cvcpkg option.  Keep
    # running on the current version; replacing the binary and restarting is
    # the update.  Checked before the Windows supervisor hand-off too: that
    # wrapper updates with pip, so a frozen builder would only be relaunched on
    # the same version, be asked to update again, and exit again.
    from cvcpkg.selfexec import is_frozen

    if is_frozen():
        click.echo(
            "  self-update: skipped - this is the single-file cvcpkg binary, "
            "which cannot pip-install itself; replace the binary and restart "
            "the builder to update.",
            err=True,
        )
        return

    # When running under the Windows supervisor wrapper, hand the whole
    # update+restart cycle back to it: exit with a sentinel code so the
    # supervisor pulls the latest cvcpkg and relaunches us on fresh code.
    # This is what makes a server-pushed update apply without a manual
    # restart on Windows -- os.execv() cannot replace the process in
    # place there, and the freshly installed code otherwise only takes
    # effect on the next builder start.  The outer try/finally still runs
    # (in-flight jobs drain, builder unregisters, pidfile is removed) so
    # the successor starts clean past the single-instance guard.
    if sys.platform == "win32" and os.environ.get("CVCPKG_BUILDER_SUPERVISED"):
        click.echo(
            f"  self-update: requesting supervisor restart (exit {_SUPERVISOR_RESTART_CODE})."
        )
        raise SystemExit(_SUPERVISOR_RESTART_CODE)

    from cvcpkg import __version__ as running

    checkout = _find_update_checkout()
    if checkout is None:
        click.echo(
            "  self-update: cannot find a cvcpkg source checkout to update from "
            f"(set {_SELF_UPDATE_DIR_ENV}); staying on {running}",
            err=True,
        )
        return

    click.echo(f"  self-update: updating from {checkout}")
    try:
        with _self_update_lock(checkout, beat=beat, stop=stop) as locked:
            if not locked:
                return
            pulled = _run_update_step(
                ["git", "pull", "--ff-only", "--quiet"],
                cwd=checkout,
                timeout=_UPDATE_GIT_TIMEOUT,
                beat=beat,
                stop=stop,
            )
            if _stop_requested(stop, f"not updating, staying on {running}"):
                return
            if pulled.returncode != 0:
                click.echo(
                    f"  self-update: git pull failed (exit {pulled.returncode}): "
                    f"{pulled.stderr.strip()[-300:]}",
                    err=True,
                )
            # Only ever a NEWER cvcpkg.  An older one -- a stale clone whose
            # upstream no longer moves -- would downgrade the builder on every
            # update request, and the same version would restart it on the code
            # it already runs.
            found = _checkout_version(checkout)
            if not found or not _is_newer_version(found, running):
                click.echo(
                    f"  self-update: {checkout} is at {found or 'an unknown version'}, "
                    f"not newer than the running {running}; not installing it",
                    err=True,
                )
                return

            # A builder sharing this checkout and interpreter (a fleet's
            # workers; a dev and a prod unit) may have installed it already --
            # while this one waited for the lock, or earlier.  Installing it
            # again would rewrite the files under that builder's feet for
            # nothing.  Not older, either: never downgrade what it installed.
            before, _ = _fresh_cvcpkg_version(beat, stop)
            if _stop_requested(stop, f"not updating, staying on {running}"):
                return
            if before is not None and not _is_newer_version(found, before):
                click.echo(
                    f"  self-update: a fresh {sys.executable} imports cvcpkg {before} "
                    "already (installed by another builder sharing it?); not reinstalling"
                )
                found = before
            elif not _pip_install(checkout, found, running, beat=beat, stop=stop):
                return

            if sys.platform == "win32":
                # Windows has no in-place exec.  os.execv() here would spawn a
                # *new* process (CRT _P_OVERLAY semantics), which is wrong and a
                # source of stray/duplicate cvcpkg processes.  The freshly
                # pip-installed code is already on disk; it takes effect the next
                # time the scheduled task starts the builder.  Keep this single
                # instance running on the current code rather than spawning a
                # broken successor.
                click.echo(
                    f"  self-update: installed {found}; it applies on the next "
                    "builder restart (Windows).",
                )
                return
            if _stop_requested(
                stop, f"{found} is installed; not restarting into it (the next start runs it)"
            ):
                return
            click.echo(f"  self-update: installed {found}, restarting...")
            # POSIX: replace the process image in place - same PID, no new
            # process, so the single-instance pidfile stays valid.  The exec
            # also drops the lock (its fd is not inherited).
            _reexec_builder(token, extra_env)
    except Exception as exc:
        click.echo(f"  self-update failed: {exc}", err=True)


def _shell_join(cmd: list[str]) -> str:
    """*cmd* as a command line to paste into this platform's shell."""
    if sys.platform == "win32":
        import subprocess

        return subprocess.list2cmdline(cmd)
    import shlex

    return shlex.join(cmd)


def _stop_requested(stop: Callable[[], bool] | None, what: str) -> bool:
    """True, after logging *what* happens instead, once *stop* returns True."""
    if stop is not None and stop():
        click.echo(f"  self-update: shutdown requested; {what}")
        return True
    return False


def _pip_install(
    checkout: Path,
    found: str,
    running: str,
    *,
    beat: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> bool:
    """_self_update's install: pip-install *checkout* (at *found*), check it took.

    True when a fresh interpreter now imports *found*; otherwise it logs why
    and returns False -- also once *stop* returns True (see _self_update).
    """
    flag = _pip_supports_break_system_packages()

    def pip(break_system_packages: bool):
        return _run_update_step(
            _pip_install_cmd(checkout, break_system_packages=break_system_packages),
            timeout=_UPDATE_PIP_TIMEOUT,
            beat=beat,
            stop=stop,
        )

    installed = pip(flag)
    err_text = installed.stderr.lower()
    if installed.returncode != 0 and (
        (flag and "no such option" in err_text)
        or (not flag and "externally-managed-environment" in err_text)
    ):
        # The version probe guessed wrong (a pip without metadata, a
        # distro-patched pip): try once more the other way.
        flag = not flag
        installed = pip(flag)
    if installed.returncode == 130 and _stop_requested(
        stop, f"pip install did not finish; staying on {running}"
    ):
        # Ended mid-install, pip can leave a half-installed cvcpkg behind.
        click.echo(
            "  self-update: should the builder then fail to start, reinstall with: "
            + _shell_join(_pip_install_cmd(checkout, break_system_packages=flag)),
            err=True,
        )
        return False
    if installed.returncode != 0:
        click.echo(
            f"  self-update: pip install failed (exit {installed.returncode}); "
            f"staying on {running}: {installed.stderr.strip()[-500:]}",
            err=True,
        )
        return False
    on_disk = f"{found} is installed; not restarting into it (the next start runs it)"
    if _stop_requested(stop, on_disk):
        return False

    # pip can succeed and still leave the old copy first on sys.path (it
    # installed somewhere else).  Restarting then only comes back on the
    # same code, so check what a fresh interpreter actually imports.
    # Compared as versions, not strings: `found` is pyproject.toml's
    # spelling and `fresh` the installed metadata's (2.5.0-rc1 vs 2.5.0rc1).
    fresh, why = _fresh_cvcpkg_version(beat, stop)
    if _stop_requested(stop, on_disk):
        return False
    if fresh is None or not _same_version(fresh, found):
        # pip succeeded, so the new copy is on disk regardless: say so --
        # whatever restarts this builder next (a reboot, the service
        # manager) loads whichever copy the interpreter finds.
        seen = f"imports cvcpkg {fresh}" if fresh else f"cannot import cvcpkg ({why})"
        click.echo(
            f"  self-update: pip installed {found} from {checkout}, so it is on "
            f"disk now, but a fresh {sys.executable} {seen}; not restarting, "
            f"still running {running}.  The builder's next start runs whatever "
            "that interpreter imports -- check which copy of cvcpkg it finds.",
            err=True,
        )
        return False
    return True


@builder_group.command("run")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--name", required=True, help="Builder name (unique per org).")
@click.option("--platform", default=None, help="Platform (default: auto-detect).")
@click.option("--arch", default=None, help="Architecture (default: auto-detect).")
@click.option("--org", "org_slug", default="", help="Home namespace / identity (empty = public).")
@click.option(
    "--serve",
    "serve_namespaces",
    multiple=True,
    metavar="NS",
    help="Additional namespace to accept jobs for (repeatable; '' = public). The "
    "builder always serves its --org. e.g. --org cvc --serve '' serves both the "
    "cvc org and public work on one machine.",
)
@click.option("--max-jobs", type=int, default=1, help="Max concurrent jobs.")
@click.option("--label", "labels", multiple=True, help="Labels (repeatable).")
@click.option(
    "--work-dir",
    type=click.Path(),
    default=None,
    help="Directory for build work trees (default: system temp).",
)
@click.option(
    "--recipe-cache-dir",
    type=click.Path(),
    default=None,
    help="Directory to cache downloaded recipe bundles.",
)
@click.option(
    "--no-websocket",
    is_flag=True,
    default=False,
    help="Disable WebSocket and use HTTP long-poll only.",
)
@click.option(
    "--exit-when-empty",
    is_flag=True,
    default=False,
    help="Drain mode: exit 0 once the queue has no claimable job and none are "
    "in flight.  For ephemeral/CI runners.  Forces HTTP long-poll (the "
    "WebSocket path has no empty-queue signal).",
)
@click.option(
    "--max-runtime",
    type=float,
    default=None,
    help="Wall-clock budget in seconds.  Stop claiming new jobs once exceeded, "
    "let in-flight jobs finish, then exit 0.  For time-boxed CI runners that "
    "must stay under a hard job timeout.",
)
@click.option(
    "--no-register",
    is_flag=True,
    default=False,
    help="Drain the queue without registering as a builder.  Selects work by "
    "platform instead of waiting to be dispatched to, and never appears in "
    "the builder list.  For platforms whose runners are ephemeral (macOS on "
    "GitHub-hosted runners), where registering per CI run leaves a dead "
    "builder behind.  --name is used as the claimant identity.  Implies "
    "--exit-when-empty.",
)
@click.option(
    "--daemon",
    is_flag=True,
    help="Run as a background daemon (fork and detach).",
)
@click.option(
    "--pidfile",
    type=click.Path(),
    default="",
    help="Path to PID file.  [default: <work-dir>/cvcpkg-builder.pid]",
)
@click.option(
    "--cross-platform",
    "cross_platforms",
    multiple=True,
    help="Cross-compilation target platform (repeatable, e.g. --cross-platform wasm).",
)
@click.option(
    "--cross-arch",
    "cross_archs",
    multiple=True,
    help="Architecture for each --cross-platform (positional pairing). "
    "Defaults: wasm->wasm32, wasi->wasm32, others->host arch.",
)
@click.option(
    "--capability",
    "capabilities",
    multiple=True,
    help="Host capability to advertise (repeatable, e.g. --capability cuda). "
    "The scheduler routes jobs whose recipe declares requires_capabilities "
    "only to builders advertising ALL of them.  Merged with auto-detected "
    "capabilities (see --no-auto-capabilities).",
)
@click.option(
    "--no-auto-capabilities",
    is_flag=True,
    default=False,
    help="Advertise only the explicit --capability flags; skip host probing "
    "(cvcpkg.platform._CAPABILITY_PROBES: nvcc for cuda; a reachable and "
    "permitted daemon for incus and lxd).  The CVCPKG_CAPABILITIES env var, "
    "when set, overrides probing either way.",
)
@click.option(
    "--no-free-disk",
    is_flag=True,
    default=False,
    help="Do not advertise free disk on the work volume.  The scheduler then "
    "treats this builder's capacity as unknown and stops filtering it out of "
    "jobs that declare build.min_disk_gb — use only when the measurement is "
    "wrong (a bind-mounted or network work dir whose statvfs lies).",
)
def builder_run(
    server: str,
    token: str,
    name: str,
    platform: str | None,
    arch: str | None,
    org_slug: str,
    serve_namespaces: tuple[str, ...],
    max_jobs: int,
    labels: tuple[str, ...],
    work_dir: str | None,
    recipe_cache_dir: str | None,
    no_websocket: bool,
    exit_when_empty: bool,
    max_runtime: float | None,
    no_register: bool,
    daemon: bool,
    pidfile: str,
    cross_platforms: tuple[str, ...],
    cross_archs: tuple[str, ...],
    capabilities: tuple[str, ...],
    no_auto_capabilities: bool,
    no_free_disk: bool,
):
    """Register as a builder, poll for jobs, and execute builds.

    Registers this machine as a remote builder, then enters a loop
    that polls the server for dispatched jobs.  For each job the
    builder:

      1. Claims the job
      2. Downloads the recipe bundle (cached locally)
      3. Runs the build via ``pack_recipe()``
      4. Streams build logs back to the server
      5. Publishes the resulting archive
      6. Reports success or failure

    Press Ctrl-C to finish in-flight jobs, unregister, and exit.

    With ``--no-register`` the builder never registers: it selects pending
    jobs by platform, claims them under ``--name`` as the claimant, and
    leaves no entry in the builder list.  Steps 2-6 are identical.
    """
    import shutil
    import signal
    import tarfile
    import tempfile
    import threading
    import traceback
    import zipfile

    httpx = require_httpx("builder")

    from cvcpkg.builder import _rewrite_pc_prefixes, _rewrite_script_prefixes, pack_recipe
    from cvcpkg.platform import detect_arch, detect_platform
    from cvcpkg.tokenenv import SCRUB_NAMES_ENV, scrub_token_env, split_names

    # -- Keep credentials out of the builds' environment -------
    # Every recipe build/test script, git, ssh and compiler this builder runs
    # inherits os.environ -- and the token is in it whenever it came from
    # CVCPKG_TOKEN or an env file (the root group loads /etc/cvcpkg/env & co.
    # into os.environ before this runs), as is every other server's token on a
    # fleet host.  The builder itself only ever uses the `token` parameter, so
    # drop them all here, before anything is spawned.  A fleet worker is also
    # told the names of the fleet's token variables (builder_fleet.worker_env).
    _scrub_hint = os.environ.get(SCRUB_NAMES_ENV)
    _scrubbed_env = scrub_token_env(os.environ, names=split_names(_scrub_hint), secrets=[token])
    os.environ.pop(SCRUB_NAMES_ENV, None)
    # What a self-update re-exec hands its successor besides CVCPKG_TOKEN.
    _reexec_env = {SCRUB_NAMES_ENV: _scrub_hint} if _scrub_hint else {}
    # Put them back when this command ends, for whatever embeds the CLI (tests,
    # a parent process): os.environ outlives one invocation.
    _restore_env = dict(_scrubbed_env, **_reexec_env)
    _cli_ctx = click.get_current_context(silent=True)
    if _cli_ctx is not None and _restore_env:
        _cli_ctx.call_on_close(
            lambda: [os.environ.setdefault(k, v) for k, v in _restore_env.items()]
        )

    if platform is None:
        platform = detect_platform()
    if arch is None:
        arch = detect_arch()

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    work_root = Path(work_dir) if work_dir else None
    if work_root is not None:
        # Create the work-dir root up front (mirrors cache_dir below).  On a
        # long-lived builder a /tmp reaper (systemd-tmpfiles, BSD /etc/periodic
        # daily clean, tmpwatch) can later delete it out from under us; each job
        # re-ensures it before mkdtemp (see _execute_job).
        work_root.mkdir(parents=True, exist_ok=True)
        # Reclaim job dirs stranded by a PREVIOUS incarnation.  _execute_job
        # removes its own tree in a finally, but that never runs when the
        # builder is killed mid-job -- which is what every deploy restart does,
        # so the leak grows until a build dies with ENOSPC (28 GiB on the dev
        # cluster, 2026-08-02).  Anything here at startup is an orphan: the
        # single-instance pidfile guard means no other builder shares this work
        # dir, and this process owns nothing yet -- so no age heuristic.
        from cvcpkg.builder_gc import sweep_work_dir

        _startup_gc = sweep_work_dir(work_root)
        if _startup_gc:
            click.echo(
                f"cvcpkg-builder: reclaimed {_startup_gc.removed} orphaned job "
                f"dir(s), {_startup_gc.freed_mib:.0f} MiB"
            )
    cache_dir = (
        Path(recipe_cache_dir)
        if recipe_cache_dir
        else Path(tempfile.gettempdir()) / "cvcpkg-recipe-cache"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Per-recipe locks serialize concurrent _fetch_recipe() calls on the same
    # name.  Without this, the main job thread and the recipe.push websocket
    # handler thread can both rmtree + mkdir + extractall the same directory,
    # producing "recipe.yaml not found" mid-extraction.
    _recipe_fetch_locks: dict[str, threading.Lock] = {}
    _recipe_fetch_locks_guard = threading.Lock()

    def _get_recipe_lock(name: str) -> threading.Lock:
        with _recipe_fetch_locks_guard:
            return _recipe_fetch_locks.setdefault(name, threading.Lock())

    # -- Daemonize -------------------------------------------
    import os as _os

    pid_path = (
        Path(pidfile)
        if pidfile
        else (work_root or Path(tempfile.gettempdir())) / "cvcpkg-builder.pid"
    )

    if daemon:
        import sys as _sys

        if _sys.platform == "win32":
            raise click.ClickException("--daemon is not supported on Windows.")

        click.echo(f"cvcpkg-builder: daemonizing (pidfile {pid_path})...")
        if _os.fork() > 0:
            raise SystemExit(0)
        _os.setsid()
        if _os.fork() > 0:
            raise SystemExit(0)
        devnull = _os.open(_os.devnull, _os.O_RDWR)
        _os.dup2(devnull, _sys.stdin.fileno())
        _os.dup2(devnull, _sys.stdout.fileno())
        _os.dup2(devnull, _sys.stderr.fileno())
        _os.close(devnull)

    # -- Single-instance guard -------------------------------
    # The builder must be a singleton per host.  A second concurrent
    # ``cvcpkg builder run`` would register a duplicate builder, race on the
    # shared work / recipe-cache directories, and (historically) pile up as
    # "a ton of cvcpkg processes".  If the pidfile names a still-live builder,
    # refuse to start; a stale pidfile (dead PID, or PID recycled by an
    # unrelated program) is silently reclaimed.
    def _pid_is_live_builder(pid: int) -> bool:
        if pid <= 0 or pid == _os.getpid():
            return False
        if sys.platform == "win32":
            import subprocess as _sp

            try:
                out = _sp.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout.lower()
            except Exception:
                return False
            # Only a live python/cvcpkg image counts - otherwise the PID was
            # recycled by an unrelated process and the pidfile is stale.
            # NB: never use os.kill(pid, 0) here; on Windows signal 0 maps to
            # TerminateProcess and would *kill* the process being probed.
            return f'"{pid}"' in out and ("python" in out or "cvcpkg" in out)
        try:
            _os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True
        return True

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    if pid_path.exists():
        try:
            _existing_pid = int(pid_path.read_text().strip() or "0")
        except ValueError:
            _existing_pid = 0
        if _pid_is_live_builder(_existing_pid):
            raise click.ClickException(
                f"another cvcpkg builder is already running (pid {_existing_pid}, "
                f"pidfile {pid_path}); refusing to start a second instance. "
                f"Stop it first, or delete the pidfile if it is stale."
            )
        pid_path.unlink(missing_ok=True)  # stale - reclaim it
    pid_path.write_text(str(_os.getpid()))

    # -- Build cross-platform/arch pairs ---------------------
    _cross_arch_defaults = {
        "wasm": "wasm32",
        "wasm-mt": "wasm32",
        "wasi": "wasm32",
    }
    cross_entries: list[dict[str, str]] = []
    for i, cp in enumerate(cross_platforms):
        if i < len(cross_archs):
            ca = cross_archs[i]
        else:
            ca = _cross_arch_defaults.get(cp, arch or "x86_64")
        cross_entries.append({"platform": cp, "arch": ca})

    # -- Advertised capabilities -----------------------------
    # Explicit --capability flags, merged with the host probe (same probes as
    # the install-side resolver gating: nvcc for cuda; a reachable+permitted
    # daemon for incus and lxd, each proving WHICH server answered so an `lxc`
    # compatibility shim fronting Incus is never advertised as lxd)
    # unless --no-auto-capabilities.  CVCPKG_CAPABILITIES, when set, is
    # authoritative inside host_capabilities() itself.  The scheduler routes a
    # job whose recipe declares requires_capabilities only to a builder
    # advertising all of them; advertising a capability never *reserves* the
    # builder — it only adds eligibility.
    advertised_caps: set[str] = {c.strip() for c in capabilities if c.strip()}
    if not no_auto_capabilities:
        from cvcpkg.platform import host_capabilities

        advertised_caps |= host_capabilities()

        # glibc floors this machine can PRODUCE for (linux only).  Deliberately
        # not part of host_capabilities(): that set is also the install-side
        # gate, where the comparison runs the other way — a consumer can RUN a
        # bundle whose floor is <= its glibc, while a builder can BUILD one
        # whose floor is >= its glibc.  Same version, opposite direction, so
        # merging them into one set would silently mean the wrong thing on one
        # side.  See cvcpkg/glibc.py.
        if platform == "linux":
            from cvcpkg.glibc import builder_capabilities, format_version, host_glibc

            _g = host_glibc()
            advertised_caps |= builder_capabilities(_g)
            if _g is not None:
                click.echo(f"cvcpkg-builder: host glibc {format_version(_g)}")

    # -- Advertised free disk --------------------------------
    # A capability is a yes/no property of the host, so it is probed once.
    # Free disk is a *measurement* that one job can move by tens of GiB, so it
    # is re-taken on every heartbeat (see _heartbeat) and only seeded here.
    #
    # WHICH volume: work_root — the directory every job tree is mkdtemp'd into
    # (see _execute_job) — and NOT the CWD, the install prefix or the recipe
    # cache, which routinely sit on a different filesystem.  With --work-dir
    # unset, jobs land in the system temp dir, which is what free_disk_gb(None)
    # measures.
    #
    # --no-free-disk advertises nothing, which the scheduler reads as "unknown"
    # and lets every job through — deliberately the same treatment as an agent
    # too old to have this field, so opting out never routes a builder
    # differently from the rest of a mixed-version fleet.
    def _measure_free_disk() -> int | None:
        if no_free_disk:
            return None
        from cvcpkg.platform import free_disk_gb as _free_disk_gb

        return _free_disk_gb(work_root)

    advertised_disk = _measure_free_disk()

    # -- Registration ----------------------------------------
    if cross_entries:
        cross_msg = " [cross: {}]".format(
            ", ".join(f"{e['platform']}/{e['arch']}" for e in cross_entries)
        )
    else:
        cross_msg = ""
    if advertised_caps:
        cross_msg += " [capabilities: {}]".format(", ".join(sorted(advertised_caps)))
    if advertised_disk is not None:
        cross_msg += f" [free disk: {advertised_disk} GiB]"

    builder_id: int | None
    if no_register:
        # Unregistered drain: no builder row, so nothing to leave behind when
        # this runner evaporates.  Work is selected by platform rather than
        # dispatched to us, and `name` becomes the claimant identity.
        builder_id = None
        exit_when_empty = True  # a drainer with nothing to drain must exit
        click.echo(f"Draining as '{name}' (unregistered) - {platform}/{arch}{cross_msg}")
    else:
        caps: dict = {}
        if cross_entries:
            caps["cross_platforms"] = cross_entries
        for _cap in sorted(advertised_caps):
            caps[_cap] = True
        # Served set: home org always included, plus any --serve namespaces,
        # order-stable and de-duplicated ('' = public). Shared with the server
        # so both sides compute the same set.
        from cvcpkg.orgs import served_set

        served = served_set(org_slug, serve_namespaces)
        body = {
            "name": name,
            "platform": platform,
            "arch": arch,
            "org_slug": org_slug,
            "served_namespaces": served,
            "max_jobs": max_jobs,
            "labels": list(labels),
            "capabilities": caps,
        }
        if advertised_disk is not None:
            # Omitted rather than sent as null when unknown, so an older
            # server that does not know the field is unaffected.
            body["free_disk_gb"] = advertised_disk
        if len(served) > 1:
            cross_msg += " [serves: {}]".format(
                ", ".join(repr(ns) if ns == "" else ns for ns in served)
            )
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{base}/v1/builders/register", headers=headers, json=body)
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise click.ClickException(f"registration failed ({resp.status_code}): {detail}")
        info = resp.json()
        builder_id = info["id"]
        click.echo(f"Registered builder #{builder_id} ({name}) - {platform}/{arch}{cross_msg}")

    shutdown = False
    # ``current_jobs`` is derived from the set of in-flight job tokens so it
    # can never desync: a token is added under the lock when a job thread is
    # launched and removed in that thread's finally (see _run_job_guarded),
    # which runs no matter how the job exits.  A raw increment/decrement
    # counter previously leaked a slot whenever the claim step returned early,
    # wedging the builder at max capacity forever.
    active_jobs: set[int] = set()
    # Job roots of in-flight builds, so the periodic GC can never delete a live
    # tree.  Guarded by jobs_lock; added when the root is created and removed in
    # the same finally that rmtree's it.
    active_job_roots: set[Path] = set()
    # Server job ids this process is executing right now.  A job id can be
    # handed to us more than once while we are still running it -- see
    # _admit_job -- and must never start a second thread.  Guarded by
    # jobs_lock; added by _admit_job, removed in _run_job_guarded's finally.
    inflight_job_ids: set[int] = set()
    _job_seq = 0
    current_jobs = 0
    jobs_lock = threading.Lock()
    # Version a server-pushed ``builder.update`` asked for, while it waits for
    # in-flight jobs to finish.  _self_update() os.execv()s on POSIX, which
    # kills every job thread mid-build; the re-exec'd builder re-registers the
    # same row and heartbeats, so the offline reaper never frees those jobs and
    # they sit "running" -- blocking their DAG -- until the build timeout.  So
    # the update is deferred until the builder is idle, and no new job is
    # admitted meanwhile (jobs dispatched to us stay dispatched and are picked
    # up by the restarted builder's connect-time catch-up poll).
    pending_update: str | None = None

    # Per-job namespace context. A builder may serve several namespaces (see
    # --serve), and jobs from different namespaces run concurrently in separate
    # threads, so the namespace used to fetch recipes and publish results must
    # be scoped to the running job's thread -- not the process-wide --org.
    # _execute_job sets this at the top of each job thread.
    _job_ctx = threading.local()

    def _current_job_org() -> str:
        return getattr(_job_ctx, "org", org_slug)

    def _claim_slot() -> int:
        """Reserve a slot; returns a unique id to release it with.

        Deliberately NOT called a "token": this id lives in the same scope as
        ``builder_run``'s bearer ``token`` parameter, and binding it to that
        name silently replaced the credential with an int -- every subsequent
        publish then sent ``Authorization: Bearer 1``.  Call under jobs_lock.
        """
        nonlocal _job_seq, current_jobs
        _job_seq += 1
        active_jobs.add(_job_seq)
        current_jobs = len(active_jobs)
        return _job_seq

    def _admit_job(job: dict) -> int | None:
        """Reserve a slot for *job*, or return None if we are already running it.

        The server hands a job out until it is claimed, and the claim happens
        inside the job thread.  ``next-job`` returns a job for as long as it is
        still ``dispatched`` (``next-claimable`` while it is ``pending``), and
        the poll loop polls again the moment it has started a job thread -- so
        with a free slot it is routinely handed the SAME job before its own
        claim has landed.  The server cannot refuse that second claim: a
        re-claim by the job's own builder (or ``--name`` claimant) is
        idempotent on purpose, so a builder whose claim response was lost can
        retry.  Admitting it anyway ran most jobs on a max-jobs >= 2 builder
        two (once three) times concurrently, each in its own job tree and each
        publishing the same variant (populate-server runs 36933340879,
        37099829036, 37106564280).  The job id is what a repeated hand-out has
        in common with the first, so dedupe on it here.

        Call under jobs_lock.
        """
        job_id = job.get("id")
        if job_id is not None:
            if job_id in inflight_job_ids:
                return None
            inflight_job_ids.add(job_id)
        return _claim_slot()

    def _release_slot(slot_id: int, job_id: int | None = None) -> None:
        nonlocal current_jobs
        with jobs_lock:
            active_jobs.discard(slot_id)
            if job_id is not None:
                inflight_job_ids.discard(job_id)
            current_jobs = len(active_jobs)

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        click.echo("\nShutdown requested - finishing in-flight jobs...")

    def _stopping() -> bool:
        """The self-update's *stop*: its git and pip steps run in their own
        session, so the signal that set ``shutdown`` never reaches them, and a
        re-exec would replace this process -- ``shutdown`` with it -- by a
        fresh builder that takes work again."""
        return shutdown

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        # Windows: `builder fleet` starts each worker in its own console
        # process group and stops it with CTRL_BREAK_EVENT (a process cannot
        # be sent SIGINT there), so Ctrl+Break must drain like Ctrl+C.
        signal.signal(signal.SIGBREAK, _handle_signal)

    # -- Helpers ---------------------------------------------

    def _heartbeat():
        """Send heartbeat to server.

        A no-op when unregistered — there is no builder row to keep alive.

        Carries a FRESH free-disk measurement, not the one taken at
        registration: a long-lived builder's work volume moves constantly (a
        running job, the periodic GC sweep, a co-tenant), and the scheduler's
        disk filter is only as good as the number it matches against.  Worst
        case the server's figure is one heartbeat interval old.
        """
        if builder_id is None:
            return
        with jobs_lock:
            jobs_now = current_jobs
        payload: dict = {"status": "online", "current_jobs": jobs_now}
        _free = _measure_free_disk()
        if _free is not None:
            payload["free_disk_gb"] = _free
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{base}/v1/builders/{builder_id}/heartbeat",
                    headers=headers,
                    json=payload,
                )
            if resp.status_code >= 400:
                click.echo(f"  heartbeat failed: {resp.status_code}", err=True)
        except Exception as exc:
            click.echo(f"  heartbeat error: {exc}", err=True)

    def _sweep_stale_recipe_dirs() -> None:
        """Drop extraction dirs left by long-finished fetches.

        Each fetch gets its own directory (see _fetch_recipe), so they would
        otherwise accumulate for the life of the builder.  The TTL is far
        beyond any job timeout, so this can never reap a dir still in use.
        """
        cutoff = time.time() - _RECIPE_DIR_TTL_SECS
        for d in cache_dir.glob("*-*"):
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue

    def _fetch_recipe(recipe_name: str) -> Path:
        """Download a recipe bundle and extract it to a private directory.

        Returns the path to the extracted recipe directory.  Each call
        extracts into its own directory: the caller keeps using that path
        long after this function returns (``_execute_job`` builds out of it
        for the whole job), so a shared, stable path is unsafe.  It used to be
        ``cache_dir/<name>``, which any *other* fetch of the same recipe would
        rmtree mid-build -- the job thread then hit "recipe.yaml not found" and
        the job was recorded as failed.  That spurious failure also cancelled
        the job's dependents, which is how a gtk4 build was cancelled with
        "dependency 137 failed" 0.4s after glib started, while glib itself went
        on to succeed and publish.
        """
        # The lock still serializes same-recipe fetches: bundle_path below is a
        # shared path, and it is written and read entirely within this block.
        with _get_recipe_lock(recipe_name):
            _sweep_stale_recipe_dirs()
            bundle_path = cache_dir / f"{recipe_name}.tar.gz"

            # Always re-download (server may have a newer version).
            # A future optimisation can compare recipe_hash.
            url = f"{base}/v1/recipes/{recipe_name}"
            params: dict[str, str] = {}
            # Resolve the recipe in the running job's namespace (falls back to
            # the builder's home --org outside a job).
            fetch_org = _current_job_org()
            if fetch_org:
                params["org_slug"] = fetch_org
            with httpx.Client(timeout=120) as client:
                resp = client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                raise RuntimeError(f"failed to download recipe '{recipe_name}': {resp.status_code}")
            # cache_dir may have been reaped (e.g. OpenBSD /tmp cleanup) between
            # builder startup and this call; recreate before writing.
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_bytes(resp.content)

            # Extract into a directory nobody else will touch.  mkdtemp both
            # creates it and guarantees the name is unique, so there is no
            # existing tree to rmtree and no window for a concurrent fetch to
            # delete it out from under the build that is about to use it.
            extract_dir = Path(tempfile.mkdtemp(prefix=f"{recipe_name}-", dir=cache_dir))
            with tarfile.open(bundle_path, "r:gz") as tar:
                safe_tar_extractall(tar, extract_dir)

            # recipe_push stores recipe files under ``<name>/`` inside
            # the tar (with ``_common/`` alongside).  If that nested dir
            # exists, return it so that ``../_common`` resolves correctly
            # from build scripts.  Fall back to the flat layout for
            # bundles created before this convention.
            nested = extract_dir / recipe_name
            if nested.is_dir() and (nested / "recipe.yaml").is_file():
                return nested
            return extract_dir

    # Shared HTTP client for log streaming (created once, avoids
    # connection overhead on every chunk).
    _log_client = httpx.Client(timeout=30)

    def _stream_log(job_id: int, text: str):
        """Append a chunk of build log to the server."""
        # Truncate to 64 KB per-chunk (server limit)
        for i in range(0, len(text), 65536):
            chunk = text[i : i + 65536]
            try:
                _log_client.patch(
                    f"{base}/v1/builds/{job_id}/log",
                    headers=headers,
                    json={"data": chunk},
                )
            except Exception:
                pass  # best-effort log streaming

    def _extract_dep_names(
        recipe_dir: Path,
        job_platform: str,
    ) -> list[str]:
        """Return direct dependency names from a recipe directory."""
        import yaml as _yaml

        recipe_yaml = recipe_dir / "recipe.yaml"
        if not recipe_yaml.is_file():
            return []
        data = _yaml.safe_load(recipe_yaml.read_text())
        deps_block = data.get("depends", {})

        names: list[str] = []
        for key in ("runtime", "build", "host_tools"):
            for dep in deps_block.get(key, []) or []:
                if isinstance(dep, str):
                    names.append(dep)
                elif isinstance(dep, dict):
                    plats = dep.get("platforms")
                    if plats and job_platform not in plats:
                        continue
                    names.append(dep["name"])
        return names

    def _resolve_transitive_deps(
        recipe_dir: Path,
        job_platform: str,
        log_cb: Callable[[str], None],
    ) -> list[str]:
        """Compute the full transitive closure of dependencies.

        Returns dep names in topological order (deepest deps first)
        so that when packages are extracted into the prefix, transitive
        libraries are available before the packages that need them.
        """
        direct = _extract_dep_names(recipe_dir, job_platform)
        if not direct:
            return []

        # BFS to collect all transitive deps
        visited: set[str] = set()
        order: list[str] = []
        queue = list(direct)
        while queue:
            name = queue.pop(0)
            if name in visited:
                continue
            visited.add(name)
            # Fetch this dep's recipe to find *its* deps
            try:
                dep_recipe_dir = _fetch_recipe(name)
                sub_deps = _extract_dep_names(dep_recipe_dir, job_platform)
                for sd in sub_deps:
                    if sd not in visited:
                        queue.append(sd)
            except Exception:
                # Recipe fetch may fail for host-tools that aren't
                # packaged as recipes (system cmake, etc.) - skip.
                pass
            order.append(name)
        return order

    def _install_deps(
        recipe_dir: Path,
        prefix: Path,
        job_platform: str,
        job_arch: str,
        job_config: str,
        job_link: str,
        log_cb: Callable[[str], None],
    ) -> None:
        """Download and install runtime dependencies into *prefix*.

        Resolves the full transitive dependency closure, then queries
        the server catalog for each dep, downloads the matching
        archive, and extracts it into the shared prefix so that
        dependent builds can find headers/libraries.
        """
        dep_names = _resolve_transitive_deps(recipe_dir, job_platform, log_cb)
        if not dep_names:
            return

        prefix.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120) as client:
            for dep_name in dep_names:
                # Find the package on the server
                resp = client.get(
                    f"{base}/v1/packages/{dep_name}",
                    headers=headers,
                )
                if resp.status_code >= 400:
                    log_cb(f"  dep {dep_name}: not found on server (skipping)\n")
                    continue

                # Newest first, so an exact match below picks the newest build
                # of a dep rather than whichever the server happened to list
                # first (see _newest_first).
                pkgs = sorted(resp.json().get("packages", []), key=_newest_first, reverse=True)
                # Find best match for platform/arch/config/link
                match = None
                for p in pkgs:
                    if (
                        p.get("platform") == job_platform
                        and p.get("arch") == job_arch
                        and p.get("build_type", "release") == job_config
                        and p.get("link", "shared") == job_link
                    ):
                        match = p
                        break
                # Relax: try just platform/arch
                if match is None:
                    for p in pkgs:
                        if p.get("platform") == job_platform and p.get("arch") == job_arch:
                            match = p
                            break
                # Final relax: a platform-independent (noarch) dependency — a
                # pure-Python wheel — is valid on every host, so a concrete
                # build resolves it to the single any/noarch variant.
                if match is None:
                    for p in pkgs:
                        if p.get("platform") == "any" and p.get("arch") == "noarch":
                            match = p
                            break
                if match is None:
                    log_cb(
                        f"  dep {dep_name}: no matching variant for "
                        f"{job_platform}/{job_arch} (skipping)\n"
                    )
                    continue

                archive_url = match.get("archive_url", "")
                if not archive_url:
                    log_cb(f"  dep {dep_name}: no archive URL (skipping)\n")
                    continue

                # Ensure absolute URL (archive_url is a relative path like /v1/download/...)
                if archive_url.startswith("/"):
                    archive_url = f"{base}{archive_url}"

                # Download the archive
                log_cb(f"  Installing dep: {dep_name} ({match.get('version', '')})\n")
                dl_resp = client.get(archive_url)
                if dl_resp.status_code >= 400:
                    log_cb(f"  dep {dep_name}: download failed ({dl_resp.status_code})\n")
                    continue

                # Extract into prefix.  The catalog's archive_url suffix
                # (typically .tar.zst) is purely cosmetic - the server
                # serves whatever the builder produced (Linux/BSD/macOS:
                # gzip; Windows: zip).  Sniff the magic bytes instead.
                archive_bytes = dl_resp.content
                head = archive_bytes[:4]
                if head[:2] == b"PK":
                    suffix, kind = ".zip", "zip"
                elif head[:2] == b"\x1f\x8b":
                    suffix, kind = ".tar.gz", "gz"
                elif head == b"\x28\xb5\x2f\xfd":
                    suffix, kind = ".tar.zst", "zst"
                else:
                    suffix, kind = ".bin", "unknown"
                tmp_archive = prefix / f"_dep_{dep_name}{suffix}"
                tmp_archive.write_bytes(archive_bytes)
                try:
                    if kind == "zip":
                        with zipfile.ZipFile(tmp_archive) as zf:
                            zf.extractall(path=prefix)  # noqa: S202
                    elif kind == "gz":
                        with tarfile.open(tmp_archive, "r:gz") as tf:
                            tf.extractall(path=prefix)  # noqa: S202
                    elif kind == "zst":
                        import zstandard  # type: ignore[import-untyped]

                        with open(tmp_archive, "rb") as f_in:
                            dctx = zstandard.ZstdDecompressor()
                            with dctx.stream_reader(f_in) as reader:
                                with tarfile.open(fileobj=reader, mode="r|") as tf:
                                    tf.extractall(path=prefix)  # noqa: S202
                    else:
                        raise ValueError(f"unknown archive format (magic={head!r})")
                except Exception as exc:
                    log_cb(f"  dep {dep_name}: extract failed ({exc})\n")
                finally:
                    tmp_archive.unlink(missing_ok=True)

        # Packages bake their build-time --prefix into the files they ship:
        # .pc files carry it in ``prefix=``, and autotools utilities embed it
        # at configure time (aclocal hardcodes @datadir@, so it looks for
        # share/aclocal-X.Y under the temp dir it was built in).  Those paths
        # are gone by the time a dependent job extracts the archive here, which
        # is why swig failed with:
        #   aclocal: error: couldn't open directory
        #   '/tmp/cvcpkg-builder/cvcpkg-automake-g3spihed/install/share/aclocal-1.17'
        # build_all() already repoints both when it merges into a shared prefix;
        # reuse the same helpers so the builder agrees with local builds.
        # Rewriting once after the loop (not per dep) keeps this O(prefix), and
        # both helpers are idempotent.
        _rewrite_pc_prefixes(prefix)
        _rewrite_script_prefixes(prefix)

    def _install_cross_toolchains(
        target_platform: str,
        host_platform: str,
        host_arch: str,
        prefix: Path,
        log_cb: Callable[[str], None],
        cache_dir: Path | None = None,
    ) -> dict[str, str]:
        """Install cross-toolchain packages and return their env vars.

        Queries the server for recipes that provide a cross-toolchain
        for *target_platform* (e.g. emsdk for wasm, wasi-sdk for wasi).
        Downloads the pre-built host-platform package and extracts it.
        Returns a merged ``cross_toolchain_env`` dict with ``${PREFIX}``
        already resolved to the actual *prefix* path.

        When *cache_dir* is set, toolchain archives are extracted there
        once and symlinked into *prefix* on subsequent calls, avoiding
        repeated downloads of large toolchains (~800 MB for emsdk).
        """
        import yaml as _yaml

        # Map target platforms -> known toolchain recipe names.
        # The builder fetches the recipe bundle to read cross_toolchain.env
        # dynamically, but needs to know which recipes to look for.
        _toolchain_map: dict[str, list[str]] = {
            "wasm": ["emsdk"],
            "wasm-mt": ["emsdk"],
            "wasi": ["wasi-sdk"],
            "cosmo": ["cosmocc"],
        }
        toolchain_names = _toolchain_map.get(target_platform, [])
        if not toolchain_names:
            return {}

        merged_env: dict[str, str] = {}
        prefix.mkdir(parents=True, exist_ok=True)

        for tc_name in toolchain_names:
            # 1. Fetch the toolchain recipe bundle to read cross_toolchain.env
            try:
                tc_recipe_dir = _fetch_recipe(tc_name)
            except Exception as exc:
                log_cb(f"  toolchain {tc_name}: recipe fetch failed ({exc})\n")
                continue

            tc_yaml_path = tc_recipe_dir / "recipe.yaml"
            if not tc_yaml_path.is_file():
                log_cb(f"  toolchain {tc_name}: no recipe.yaml\n")
                continue

            tc_data = _yaml.safe_load(tc_yaml_path.read_text())
            ct_block = tc_data.get("cross_toolchain", {})
            ct_env = ct_block.get("env", {}) or {}
            ct_host_tools = (tc_data.get("depends", {}) or {}).get("host_tools", []) or []

            # 2. Download the pre-built package for the HOST platform
            with httpx.Client(timeout=120) as client:
                resp = client.get(
                    f"{base}/v1/packages/{tc_name}",
                    headers=headers,
                )
                if resp.status_code >= 400:
                    log_cb(f"  toolchain {tc_name}: not found on server ({resp.status_code})\n")
                    continue

                pkgs = resp.json().get("packages", [])
                match = None
                for p in pkgs:
                    if p.get("platform") == host_platform and p.get("arch") == host_arch:
                        match = p
                        break
                if match is None:
                    log_cb(
                        f"  toolchain {tc_name}: no {host_platform}/{host_arch} package on server\n"
                    )
                    continue

                archive_url = match.get("archive_url", "")
                if not archive_url:
                    log_cb(f"  toolchain {tc_name}: no archive URL\n")
                    continue

                # Ensure absolute URL
                if archive_url.startswith("/"):
                    archive_url = f"{base}{archive_url}"

                tc_version = match.get("version", "unknown")

                # -- Persistent toolchain cache ----------------------
                # When a cache_dir is provided, extract the toolchain
                # once into cache_dir/toolchains/<name>-<version>/
                # and symlink its contents into the per-build prefix.
                # This avoids re-downloading ~300-800 MB archives on
                # every cross-compilation job.
                extract_target = prefix
                tc_cache_path: Path | None = None
                tc_staging: Path | None = None
                if cache_dir is not None:
                    tc_cache_root = cache_dir / "toolchains"
                    tc_cache_path = tc_cache_root / f"{tc_name}-{tc_version}"
                    # Readiness is the completion MARKER, never "the directory
                    # has something in it".  Concurrent jobs on one builder
                    # share this cache, and the old non-empty test was true the
                    # instant the first job wrote its download into the cache
                    # dir -- so a second job symlinked a half-unpacked
                    # toolchain into its prefix (emsdk_env.sh then aborts with
                    # "unable to determine 'emsdk' directory", because
                    # emsdk.py has not been extracted yet).
                    if _toolchain_cache_ready(tc_cache_path):
                        log_cb(
                            f"  Toolchain {tc_name} ({tc_version}) cached, symlinking into prefix\n"
                        )
                        # Merge cached toolchain contents into the build prefix
                        # (recursing on dir collisions with deps already there).
                        _symlink_merge_into(tc_cache_path, prefix)
                        # Resolve env and skip download
                        for var, tpl in ct_env.items():
                            merged_env[var] = tpl.replace("${PREFIX}", str(prefix))
                        log_cb(
                            f"  Toolchain {tc_name} ready "
                            f"({', '.join(f'{k}={merged_env[k]}' for k in ct_env)})\n"
                        )
                        # Still install host_tools (cheap, small packages)
                        for tool_name in ct_host_tools:
                            if isinstance(tool_name, dict):
                                tool_name = tool_name.get("name", "")
                            if not tool_name:
                                continue
                            try:
                                _install_host_package(
                                    tool_name, host_platform, host_arch, prefix, log_cb
                                )
                            except Exception as exc:
                                log_cb(f"  host_tool {tool_name}: install failed ({exc})\n")
                        continue
                    # Not cached yet - unpack into a private staging directory
                    # and publish it atomically below, so a concurrent job
                    # never observes a partial tree (and never has to clean up
                    # after ours).
                    tc_cache_root.mkdir(parents=True, exist_ok=True)
                    tc_staging = tc_cache_root / (
                        f".{tc_name}-{tc_version}.{os.getpid()}.{time.time_ns()}.tmp"
                    )
                    shutil.rmtree(tc_staging, ignore_errors=True)
                    tc_staging.mkdir(parents=True, exist_ok=True)
                    extract_target = tc_staging

                log_cb(f"  Installing cross-toolchain: {tc_name} ({tc_version})\n")
                dl_resp = client.get(archive_url)
                if dl_resp.status_code >= 400:
                    log_cb(f"  toolchain {tc_name}: download failed ({dl_resp.status_code})\n")
                    if tc_staging is not None:
                        shutil.rmtree(tc_staging, ignore_errors=True)
                    continue

                tc_bytes = dl_resp.content
                head = tc_bytes[:4]
                if head[:2] == b"PK":
                    suffix, kind = ".zip", "zip"
                elif head[:2] == b"\x1f\x8b":
                    suffix, kind = ".tar.gz", "gz"
                elif head == b"\x28\xb5\x2f\xfd":
                    suffix, kind = ".tar.zst", "zst"
                else:
                    suffix, kind = ".bin", "unknown"
                tmp_archive = extract_target / f"_toolchain_{tc_name}{suffix}"
                tmp_archive.write_bytes(tc_bytes)
                try:
                    if kind == "zip":
                        with zipfile.ZipFile(tmp_archive) as zf:
                            zf.extractall(path=extract_target)  # noqa: S202
                    elif kind == "gz":
                        with tarfile.open(tmp_archive, "r:gz") as tf:
                            tf.extractall(path=extract_target)  # noqa: S202
                    elif kind == "zst":
                        import zstandard  # type: ignore[import-untyped]

                        with open(tmp_archive, "rb") as f_in:
                            dctx = zstandard.ZstdDecompressor()
                            with dctx.stream_reader(f_in) as reader:
                                with tarfile.open(fileobj=reader, mode="r|") as tf:
                                    tf.extractall(path=extract_target)  # noqa: S202
                    else:
                        raise ValueError(f"unknown archive format (magic={head!r})")
                except Exception as exc:
                    log_cb(f"  toolchain {tc_name}: extract failed ({exc})\n")
                    # Only ever discard OUR staging dir.  Removing the shared
                    # cache here used to delete a concurrent job's good
                    # toolchain out from under it, turning one failure into a
                    # cascade across every wasm job on the builder.
                    if tc_staging is not None:
                        shutil.rmtree(tc_staging, ignore_errors=True)
                    continue
                finally:
                    tmp_archive.unlink(missing_ok=True)

                if tc_staging is not None and tc_cache_path is not None:
                    _publish_toolchain_cache(tc_staging, tc_cache_path, f"{tc_name} {tc_version}")
                    extract_target = tc_cache_path

            # If we extracted into the cache, merge into prefix now
            # (recursing on dir collisions with deps already there).
            if tc_cache_path and extract_target != prefix:
                _symlink_merge_into(tc_cache_path, prefix)

            # 3. Resolve env templates
            for var, tpl in ct_env.items():
                merged_env[var] = tpl.replace("${PREFIX}", str(prefix))

            log_cb(
                f"  Toolchain {tc_name} installed "
                f"({', '.join(f'{k}={merged_env[k]}' for k in ct_env)})\n"
            )

            # 4. Install host_tools declared by the toolchain recipe
            # (e.g. wasmtime for wasi-sdk so test scripts can execute
            # wasm32-wasi binaries).  These are fetched as host_platform
            # packages and extracted into the same prefix.
            for tool_name in ct_host_tools:
                if isinstance(tool_name, dict):
                    tool_name = tool_name.get("name", "")
                if not tool_name:
                    continue
                try:
                    _install_host_package(tool_name, host_platform, host_arch, prefix, log_cb)
                except Exception as exc:
                    log_cb(f"  host_tool {tool_name}: install failed ({exc})\n")

        return merged_env

    def _install_host_package(
        pkg_name: str,
        host_platform: str,
        host_arch: str,
        prefix: Path,
        log_cb: Callable[[str], None],
    ) -> None:
        """Fetch a pre-built package for the host platform and extract to *prefix*.

        Used to install cross-toolchain companion tools like wasmtime
        alongside wasi-sdk.  Best-effort: logs and returns on any failure
        so the build can proceed without the tool.
        """
        with httpx.Client(timeout=120) as client:
            resp = client.get(f"{base}/v1/packages/{pkg_name}", headers=headers)
            if resp.status_code >= 400:
                log_cb(f"  host_tool {pkg_name}: not found on server ({resp.status_code})\n")
                return
            pkgs = resp.json().get("packages", [])
            match = None
            for p in pkgs:
                if p.get("platform") == host_platform and p.get("arch") == host_arch:
                    match = p
                    break
            if match is None:
                log_cb(
                    f"  host_tool {pkg_name}: no {host_platform}/{host_arch} package on server\n"
                )
                return

            archive_url = match.get("archive_url", "")
            if not archive_url:
                log_cb(f"  host_tool {pkg_name}: no archive URL\n")
                return
            if archive_url.startswith("/"):
                archive_url = f"{base}{archive_url}"

            log_cb(f"  Installing host tool: {pkg_name} ({match.get('version', '')})\n")
            dl_resp = client.get(archive_url)
            if dl_resp.status_code >= 400:
                log_cb(f"  host_tool {pkg_name}: download failed ({dl_resp.status_code})\n")
                return

            data = dl_resp.content
            head = data[:4]
            if head[:2] == b"PK":
                suffix, kind = ".zip", "zip"
            elif head[:2] == b"\x1f\x8b":
                suffix, kind = ".tar.gz", "gz"
            elif head == b"\x28\xb5\x2f\xfd":
                suffix, kind = ".tar.zst", "zst"
            else:
                log_cb(f"  host_tool {pkg_name}: unknown archive format\n")
                return
            tmp_archive = prefix / f"_hosttool_{pkg_name}{suffix}"
            tmp_archive.write_bytes(data)
            try:
                if kind == "zip":
                    with zipfile.ZipFile(tmp_archive) as zf:
                        zf.extractall(path=prefix)  # noqa: S202
                elif kind == "gz":
                    with tarfile.open(tmp_archive, "r:gz") as tf:
                        tf.extractall(path=prefix)  # noqa: S202
                elif kind == "zst":
                    import zstandard  # type: ignore[import-untyped]

                    with open(tmp_archive, "rb") as f_in:
                        dctx = zstandard.ZstdDecompressor()
                        with dctx.stream_reader(f_in) as reader:
                            with tarfile.open(fileobj=reader, mode="r|") as tf:
                                tf.extractall(path=prefix)  # noqa: S202
            finally:
                tmp_archive.unlink(missing_ok=True)

    def _claim_landed(job_id: int, why: str) -> bool:
        """After every claim attempt was lost: did one of them land anyway?

        A claim whose answer never arrives (a timeout, a dropped connection, a
        proxy 502/503/504) may still have committed, and then the job sits
        "running" under this builder with nothing building it until the build
        timeout reaps it -- next-job never hands a running job back.  So ask
        the server whose job it is now, and build it if it is ours.
        """
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{base}/v1/builds/{job_id}", headers=headers)
            info = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001 - same outcome as no answer
            info = None
        if isinstance(info, dict) and info.get("status") == "running":
            ours = (
                info.get("builder_id") == builder_id
                if builder_id is not None
                else info.get("claimed_by") == name
            )
            if ours:
                click.echo(
                    f"  [{job_id}] claim answer lost ({why}), but the job is "
                    "running under this builder; building it"
                )
                return True
        click.echo(f"  [{job_id}] claim failed ({why}), skipping", err=True)
        return False

    def _claim_job(job_id: int) -> bool:
        """Claim *job_id* for this builder; True when it is ours to build.

        A claim whose answer is lost is asked again: re-claiming a job this
        builder holds is idempotent on the server for exactly this reason.
        When every attempt is lost, _claim_landed checks whether one landed.
        """
        claim_body: dict = (
            {"builder_id": builder_id} if builder_id is not None else {"claimant": name}
        )
        try:
            resp = None
            why = ""
            for _attempt in range(3):
                if _attempt:
                    time.sleep(2.0 * _attempt)
                try:
                    with httpx.Client(timeout=30) as client:
                        resp = client.post(
                            f"{base}/v1/builds/{job_id}/claim",
                            headers=headers,
                            json=claim_body,
                        )
                except httpx.TransportError as exc:
                    resp, why = None, str(exc) or type(exc).__name__
                    click.echo(f"  [{job_id}] claim error: {why}", err=True)
                    continue
                if resp.status_code in (502, 503, 504):
                    why = f"answered {resp.status_code}"
                    click.echo(f"  [{job_id}] claim {why}", err=True)
                    continue
                break
            if resp is None or resp.status_code in (502, 503, 504):
                return _claim_landed(job_id, why)
            if resp.status_code == 409:
                # Someone else got there first (unregistered workers select by
                # platform, so two can see the same job).  Not an error.
                click.echo(f"  [{job_id}] already claimed elsewhere, skipping")
                return False
            if resp.status_code >= 400:
                click.echo(
                    f"  [{job_id}] claim failed ({resp.status_code}), skipping",
                    err=True,
                )
                return False
            # A 200 is not by itself a go-ahead.  For a job that is no longer
            # claimable (cancelled or paused while it was being handed out,
            # or already finished) the server answers 200 and hands the row
            # back so the caller can see why.  Building it would publish a
            # cancelled job and then /complete would flip it to succeeded.
            # A body without a status (an older or fake server) proceeds.
            try:
                claimed_status = str(resp.json().get("status") or "")
            except Exception:
                claimed_status = ""
            if claimed_status and claimed_status != "running":
                click.echo(f"  [{job_id}] claim returned status '{claimed_status}', skipping")
                return False
            return True
        except Exception as exc:
            click.echo(f"  [{job_id}] claim error: {exc}", err=True)
            return False

    def _execute_job(job: dict) -> None:
        """Execute a single build job.  The slot is released by the calling
        _run_job_guarded wrapper, not here."""
        job_id = job["id"]
        recipe_name = job["recipe_name"]
        job_platform = job.get("platform", platform)
        job_arch = job.get("arch", arch)
        job_config = job.get("config", "release")
        job_link = job.get("link", "shared")
        # A platform-independent (noarch) job produces a single any/noarch
        # bundle. It is not a cross-compile: it builds natively on this builder
        # and its build-time deps (e.g. the CPython interpreter) come from the
        # host's own concrete packages, while pack_recipe tags the result
        # any/noarch. Resolve deps + build against the builder's native target.
        is_noarch = job_platform == "any" or job_arch == "noarch"
        build_platform = platform if is_noarch else job_platform
        build_arch = arch if is_noarch else job_arch
        # Scope this thread's recipe fetches + publish to the job's namespace.
        # A multi-namespace builder must never fetch/publish a job under its
        # home --org instead of the job's own org.
        job_org = job.get("org_slug", org_slug)
        _job_ctx.org = job_org

        click.echo(
            f"  [{job_id}] Building {recipe_name} "
            f"({job_platform}/{job_arch}/{job_config}/{job_link})"
        )

        # 1. Claim the job.
        if not _claim_job(job_id):
            return

        error_message = ""
        archive_path: Path | None = None
        dep_prefix: Path | None = None
        job_root: Path | None = None
        try:
            # 2. Download recipe
            _stream_log(job_id, f"Downloading recipe '{recipe_name}'...\n")
            recipe_dir = _fetch_recipe(recipe_name)
            _stream_log(job_id, f"Recipe extracted to {recipe_dir}\n")

            # 3. Build + package
            # Detect cross-compilation: job targets a different platform
            # than the builder's native platform (e.g. wasm on linux).
            host_plat = ""
            if job_platform != platform and not is_noarch:
                host_plat = platform
                _stream_log(
                    job_id,
                    f"Cross-compiling: target={job_platform}, host={host_plat}\n",
                )

            _stream_log(
                job_id,
                f"Starting build: {recipe_name} "
                f"({job_platform}/{job_arch}/{job_config}/{job_link})\n",
            )

            # 3a. Install runtime dependencies into a shared prefix.
            # Re-ensure the work-dir root exists: on long-lived builders a /tmp
            # reaper can delete it between jobs, which would otherwise make
            # mkdtemp(dir=work_root) raise FileNotFoundError and fail the job.
            if work_root is not None:
                work_root.mkdir(parents=True, exist_ok=True)
            # Per-job isolation root. A single `builds submit` fans out to
            # several config/link variants of the SAME recipe that run
            # concurrently (max-jobs>1). Everything this job creates — dep
            # prefix, build work dir, output dir — lives under job_root so
            # the finally-cleanup can remove exactly this job's trees. The
            # previous cleanup globbed `cvcpkg-<recipe>-*` and deleted a
            # sibling variant's still-in-use work dir mid-build, which raised
            # FileNotFoundError at staging.mkdir() in the losing variant.
            job_root = Path(tempfile.mkdtemp(prefix=f"cvcpkg-job-{recipe_name}-", dir=work_root))
            with jobs_lock:
                active_job_roots.add(job_root)
            # active_job_roots only protects this tree from *our own* periodic
            # gc.  The fleet's disk reaper runs from the deploy workflow, in
            # another process and possibly as another user, and can only judge
            # by what it can stat.  Publish liveness so it can tell a running
            # job from one stranded by a SIGKILL. (cvcpkg/heartbeat.py)
            heartbeat_watch(job_root, label=recipe_name)
            dep_prefix = Path(
                tempfile.mkdtemp(prefix=f"cvcpkg-prefix-{recipe_name}-", dir=job_root)
            )
            log_cb = lambda text, _jid=job_id: _stream_log(_jid, text)  # noqa: E731
            _install_deps(
                recipe_dir, dep_prefix, build_platform, build_arch, job_config, job_link, log_cb
            )

            # 3a-2. Install cross-toolchains (e.g. emsdk for wasm)
            cross_env: dict[str, str] = {}
            if host_plat:
                cross_env = _install_cross_toolchains(
                    target_platform=job_platform,
                    host_platform=host_plat,
                    host_arch=arch,  # builder's native arch
                    prefix=dep_prefix,
                    log_cb=log_cb,
                    cache_dir=work_root,
                )

            # 3b. Build + package (output dir under the per-job root)
            output_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-out-{recipe_name}-", dir=job_root))
            try:
                archive_path, sha256, size = pack_recipe(
                    recipe_dir,
                    platform=build_platform,
                    arch=build_arch,
                    config=job_config,
                    link=job_link,
                    prefix=dep_prefix,
                    output_dir=output_dir,
                    work_dir_root=job_root,
                    log_callback=log_cb,
                    host_platform=host_plat,
                    cross_toolchain_env=cross_env or None,
                )
                _stream_log(
                    job_id,
                    f"Build succeeded: {archive_path.name} ({size:,} bytes, sha256={sha256})\n",
                )
            except Exception as exc:
                error_message = f"build failed: {exc}\n{traceback.format_exc()}"
                _stream_log(job_id, error_message)
                raise

            # 4. Publish the archive to the server
            _stream_log(job_id, f"Publishing {archive_path.name}...\n")
            try:
                _publish_to_server(
                    server=base,
                    token=token,
                    archive_paths=[archive_path],
                    release_tag="",
                    chunked_threshold=10 * 1024 * 1024,
                    org=job_org,
                )
                _stream_log(job_id, "Published successfully.\n")
            except click.ClickException as pub_exc:
                # Do NOT swallow this.  An already-published variant never
                # reaches here: _publish_to_server skips it up front via
                # _variant_exists, and every 409 -- simple upload, chunked
                # init, and chunked finalise -- returns "skipped" rather than
                # raising.  (Finalise was missing that until it was found to
                # fail jobs whose bytes had in fact reached the catalogue.)
                # So a ClickException here is a genuine publish failure --
                # auth, storage, a failed chunk -- and completing the job would
                # advertise a bundle that is not in the catalog.  That is
                # exactly how a "succeeded" build came to publish nothing.
                _stream_log(job_id, f"Publish FAILED: {pub_exc.format_message()}\n")
                raise

            result_url = f"{base}/v1/packages/{recipe_name}"

            # 5. Report completion
            with httpx.Client(timeout=30) as client:
                client.post(
                    f"{base}/v1/builds/{job_id}/complete",
                    headers=headers,
                    json={"result_archive_url": result_url},
                )
            click.echo(f"  [{job_id}] Completed: {recipe_name}")

        except Exception as exc:
            # Report failure
            if not error_message:
                error_message = f"{exc}\n{traceback.format_exc()}"
            try:
                with httpx.Client(timeout=30) as client:
                    client.post(
                        f"{base}/v1/builds/{job_id}/fail",
                        headers=headers,
                        json={"error_message": error_message[:4096]},
                    )
            except Exception:
                pass
            click.echo(f"  [{job_id}] Failed: {recipe_name} - {exc}", err=True)

        finally:
            # Remove exactly this job's isolated tree (dep prefix, build work
            # dir, and output dir all live under job_root). Do NOT glob
            # cvcpkg-<recipe>-* across the shared work_root — concurrent
            # variant jobs of the same recipe would delete each other's
            # live work dirs (the FileNotFoundError-at-staging bug).
            if job_root is not None:
                with jobs_lock:
                    active_job_roots.discard(job_root)
                heartbeat_unwatch(job_root)
                if job_root.is_dir():
                    shutil.rmtree(job_root, ignore_errors=True)
            # NB: the slot count is released by _run_job_guarded's finally,
            # not here — so an early return from the claim step above (which
            # never reaches this try) still frees the slot.

    def _run_job_guarded(job: dict, slot_id: int) -> None:
        """Thread entry point: run a job and ALWAYS release its slot.

        The caller reserves the slot (``_admit_job``) before starting the
        thread; this wrapper's finally releases it, along with the job id's
        in-flight mark, no matter how ``_execute_job`` exits — normal return,
        exception, or the early ``return`` in its claim step.  Previously the
        release lived inside ``_execute_job``'s try/finally, which a
        failed-claim early return
        skipped, permanently leaking a slot (a max-jobs=2 builder wedged at
        2/2 after two failed claims and stopped taking work).
        """
        try:
            _execute_job(job)
        finally:
            _release_slot(slot_id, job.get("id"))

    def _start_job(job: dict, slot_id: int) -> bool:
        """Start *job*'s thread; False (admission undone) if it can't start.

        _admit_job marks the job id in flight and only the thread's own
        finally clears it.  A thread that never starts (``RuntimeError: can't
        start new thread`` under a thread/process limit) would leave the mark
        set for the life of the process, and since ``next-job`` keeps handing
        that still-dispatched job back first, the poll loop would refuse it
        forever and take no other work while heartbeating "online".

        The failure is reported, not raised: every caller is a loop that must
        keep running -- the HTTP poll loop is the whole builder, and a BSD
        builder started from ``@reboot`` has no supervisor to restart it.  The
        job stays dispatched to us, so a later poll or sweep retries it.
        """
        t = threading.Thread(target=_run_job_guarded, args=(job, slot_id), daemon=True)
        try:
            t.start()
        except RuntimeError as exc:
            _release_slot(slot_id, job.get("id"))
            click.echo(f"  [{job.get('id')}] could not start a job thread: {exc}", err=True)
            return False
        except BaseException:
            _release_slot(slot_id, job.get("id"))
            raise
        return True

    def _apply_pending_update() -> None:
        """Run a deferred ``builder.update`` once no job is in flight.

        See ``pending_update``.  _self_update() does not return when it
        re-execs; when it does return (nothing to update from, or Windows
        without the supervisor) the builder resumes taking jobs.
        """
        nonlocal pending_update
        if not pending_update:
            return
        if shutdown:
            # Stopping: an update would re-exec into a builder that is not.
            click.echo(
                f"  self-update: the builder is stopping; dropping the update to {pending_update}"
            )
            pending_update = None
            return
        with jobs_lock:
            if current_jobs:
                return
        target, pending_update = pending_update, None
        click.echo(f"  self-update: no job in flight, updating to {target}")
        # Beat first -- the last one may be most of a minute old -- and keep
        # beating while git and pip run (_self_update's beat): the server marks
        # a builder silent for 180 s offline and fails the jobs it dispatched
        # to it meanwhile.
        _heartbeat()
        _self_update(token=token, beat=_heartbeat, extra_env=_reexec_env, stop=_stopping)

    # -- WebSocket helpers -----------------------------------

    def _ws_url() -> str:
        """Build WebSocket URL from the HTTP base URL.

        No ``?token=``: the bearer token goes in the handshake's Authorization
        header (see _run_ws_loop).  A query string lands in every access log on
        the way -- cvcpkg.org's Apache logs the full request line -- and the
        builder now retries the socket every few minutes for as long as it is
        down, so a token in the URL would be written there hundreds of times a
        day per builder.
        """
        scheme = "wss" if base.startswith("https") else "ws"
        rest = base.split("://", 1)[1] if "://" in base else base
        return f"{scheme}://{rest}/v1/builders/{builder_id}/ws"

    def _ws_error_text(exc: BaseException) -> str:
        """``str(exc)`` with the bearer token masked.

        The token now travels in the handshake's Authorization header, not
        the URL, but an error may still quote request headers; mask it
        wherever it appears.
        """
        text = str(exc) or type(exc).__name__
        return text.replace(token, "***") if token else text

    def _ws_catch_up() -> bool:
        """Start jobs dispatched to us that the socket will never push.

        ``job.dispatch`` is pushed exactly once, at dispatch time, and only if
        our socket is registered on the server at that moment.  A job
        dispatched while we were on long-poll or mid-handshake, or pushed while
        every slot here was full (the dispatch handler drops it), stays
        ``dispatched`` to this builder with nothing left to deliver it until
        the build timeout reaps it.  ``next-job`` still returns such a job, so
        ask it -- on every (re)connect and then periodically.  A job already
        running here (its claim not landed yet) comes back too; _admit_job
        refuses that one, so a job seen on both paths still runs once.

        Starts every such job there is room for.  ``next-job`` hands back the
        lowest-id dispatched job, so a job just started here hides the rest
        until its claim lands.  Returns True when it stopped there, and the
        socket loop runs it again on its next turns (handling pushes in
        between) rather than leaving the rest to the next sweep, a minute away.
        """
        while not shutdown and not pending_update:
            with jobs_lock:
                if current_jobs >= max_jobs:
                    return False
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(
                        f"{base}/v1/builders/{builder_id}/next-job",
                        headers=headers,
                        params={"timeout": "1"},
                    )
            except Exception as exc:
                click.echo(f"  catch-up poll error: {exc}", err=True)
                return False
            if resp.status_code != 200:
                return False  # 204: nothing waiting.  Errors: the next sweep retries.
            try:
                job = resp.json()
            except ValueError:
                # A 200 that is not JSON (a proxy error page): runs on the
                # socket's thread, so raising here would tear the session down.
                return False
            if not isinstance(job, dict) or job.get("id") is None:
                return False
            with jobs_lock:
                slot_id = _admit_job(job)
            if slot_id is None:
                # Already running it: our claim has not landed yet.
                return True
            click.echo(f"  [{job.get('id')}] picked up a dispatch the socket did not deliver")
            if not _start_job(job, slot_id):
                return False
        return False

    def _run_ws_loop() -> tuple[str, float]:
        """Run one WebSocket session: connect, then serve until it ends.

        While connected, sends heartbeats and receives dispatched jobs,
        recipe pushes and ``builder.update`` (fleet self-update, which only
        this path carries).  Returns ``(outcome, connected_seconds)``:

        - ``"unavailable"``: the websockets library is not installed.  Not
          worth retrying in this process.
        - ``"failed"``: the handshake failed -- refused, a 404 from a proxy or
          server that does not pass the upgrade, DNS, timeout.
        - ``"lost"``: connected, then the connection dropped.
        - ``"stopped"``: shutdown was requested or --max-runtime was reached.

        The caller covers every non-"stopped" outcome with HTTP long-poll and
        schedules the next attempt (see the main loop).
        """
        nonlocal shutdown, current_jobs, last_heartbeat, last_gc, pending_update
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            click.echo("  websockets not installed - using HTTP long-poll", err=True)
            return "unavailable", 0.0

        click.echo("Connecting via WebSocket...")
        connected_at: float | None = None
        lost = ""
        try:
            with ws_sync.connect(
                _ws_url(),
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                connected_at = time.time()
                click.echo("WebSocket connected.")
                # No ws.settimeout(): the sync client has no such method (that
                # call raised AttributeError right after every successful
                # handshake, so no session ever survived it).  Reads are
                # bounded by recv(timeout=...) below.
                last_sweep = 0.0  # catch up at once: see _ws_catch_up
                # A job.dispatch dropped because every slot was busy.  The
                # server frees our slot when the job reports complete/fail, but
                # this thread still holds it through the job's cleanup (rmtree
                # of the job tree), so the scheduler's next dispatch -- often
                # the dependent the completion just unblocked -- routinely lands
                # in that window.  Catch up as soon as a slot frees instead of
                # leaving it for the periodic sweep (up to ws_sweep_interval).
                missed_push = False
                # _ws_catch_up stopped behind a job whose claim had not landed;
                # run it again on the next turns (see catch_up_deadline).
                catch_up_again = False
                catch_up_deadline = 0.0
                while not shutdown:
                    # Wall-clock budget: stop claiming, drain in-flight, exit.
                    if _past_deadline():
                        click.echo(
                            f"Max runtime reached ({max_runtime:.0f}s) - "
                            "stopping claims, finishing in-flight jobs..."
                        )
                        shutdown = True
                        break

                    # Send heartbeat if due
                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        _hb: dict = {
                            "type": "heartbeat",
                            "status": "online",
                            "current_jobs": current_jobs,
                        }
                        # Re-measured per beat, same as the REST path — the
                        # WebSocket loop is the one a long-lived builder
                        # actually uses, so it must not carry a stale figure.
                        _free = _measure_free_disk()
                        if _free is not None:
                            _hb["free_disk_gb"] = _free
                        try:
                            ws.send(json.dumps(_hb))
                            last_heartbeat = now
                        except Exception as exc:
                            lost = _ws_error_text(exc)
                            break  # connection lost

                    if gc_interval > 0 and now - last_gc >= gc_interval:
                        _run_periodic_gc()
                        last_gc = now

                    _apply_pending_update()

                    with jobs_lock:
                        # Not while an update is pending: _ws_catch_up admits
                        # nothing then, and clearing missed_push here would
                        # leave the dropped push to the periodic sweep once
                        # the update returns without a restart.
                        slot_free = current_jobs < max_jobs and not pending_update
                    rerun = catch_up_again and slot_free
                    sweep_due = now - last_sweep >= ws_sweep_interval
                    if sweep_due or (missed_push and slot_free) or rerun:
                        missed_push = False
                        started_before = _job_seq
                        blocked = _ws_catch_up()
                        last_sweep = time.time()
                        # Re-run while blocked, for a while past the last time
                        # a run made progress (started a job).
                        if blocked and (not rerun or _job_seq != started_before):
                            catch_up_deadline = last_sweep + _CATCH_UP_RERUN_SECS
                        catch_up_again = blocked and last_sweep < catch_up_deadline

                    # Try to receive a message
                    try:
                        raw = ws.recv(timeout=_CATCH_UP_RERUN_RECV_TIMEOUT if catch_up_again else 2)
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        lost = _ws_error_text(exc)
                        break  # connection lost

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    msg_type = msg.get("type", "")

                    if msg_type == "job.dispatch":
                        job = msg.get("job")
                        if not isinstance(job, dict) or job.get("id") is None:
                            continue
                        with jobs_lock:
                            if current_jobs >= max_jobs or pending_update:
                                # Dropped here, but it stays dispatched to us:
                                # _ws_catch_up starts it once a slot frees
                                # (or the restarted builder's connect-time
                                # catch-up does, after a self-update).
                                missed_push = True
                                continue
                            slot_id = _admit_job(job)
                        if slot_id is None:
                            click.echo(
                                f"  [{job.get('id')}] already running here, "
                                "ignoring the repeated dispatch"
                            )
                            continue
                        if not _start_job(job, slot_id):
                            # Still dispatched to us: the catch-up retries it.
                            missed_push = True

                    elif msg_type == "recipe.push":
                        recipe = msg.get("recipe", {})
                        rname = recipe.get("name", "")
                        if rname:
                            # Note only.  This used to eagerly _fetch_recipe() to
                            # warm the cache, but every fetch re-downloads anyway
                            # ("server may have a newer version"), so the warm-up
                            # bought nothing while adding a second thread racing
                            # the extraction directory of any in-flight build.
                            click.echo(f"  Recipe updated: {rname}")

                    elif msg_type == "ping":
                        try:
                            ws.send(json.dumps({"type": "pong"}))
                        except Exception as exc:
                            lost = _ws_error_text(exc)
                            break

                    elif msg_type == "builder.update":
                        server_ver = msg.get("version", "")
                        from cvcpkg import __version__
                        from cvcpkg.selfexec import is_frozen

                        if shutdown:
                            click.echo(
                                f"  Server requests update to {server_ver}; ignored: "
                                "shutting down"
                            )
                        elif server_ver and server_ver != __version__:
                            if not _is_newer_version(server_ver, __version__):
                                click.echo(
                                    f"  Server is at {server_ver}, not newer than "
                                    f"this builder's {__version__}; update ignored"
                                )
                            elif is_frozen():
                                # _self_update() would skip it too (it cannot
                                # pip-install the single-file binary); decided
                                # here so the builder does not first stop taking
                                # work and drain for an update that never runs.
                                click.echo(
                                    f"  Server requests update: {__version__} -> "
                                    f"{server_ver}; ignored (single-file binary)",
                                    err=True,
                                )
                            elif sys.platform == "win32" and os.environ.get(
                                "CVCPKG_BUILDER_SUPERVISED"
                            ):
                                # The Windows supervisor wrapper owns the
                                # update: it pulls and installs before it
                                # relaunches us (see _self_update).
                                click.echo(
                                    f"  Server requests update: {__version__} -> "
                                    f"{server_ver} (once in-flight jobs finish; "
                                    "taking no new jobs meanwhile)"
                                )
                                pending_update = server_ver
                                _apply_pending_update()
                            else:
                                # Decide now whether there is anything to
                                # install, before the builder stops taking
                                # work: draining a busy builder (hours, behind
                                # an llvm build) only to find no newer source
                                # is pure loss.
                                try:
                                    src = _resolve_update_source(beat=_heartbeat, stop=_stopping)
                                    failed = ""
                                except Exception as exc:
                                    # Never at the socket's expense: an
                                    # exception here would end this session.
                                    src, failed = (
                                        None,
                                        f"{type(exc).__name__}: {_ws_error_text(exc)}",
                                    )
                                if shutdown:
                                    click.echo(
                                        f"  Server requests update to {server_ver}; "
                                        "ignored: shutting down"
                                    )
                                elif failed:
                                    click.echo(
                                        f"  Server requests update to {server_ver}; "
                                        f"ignored: the update check failed ({failed})",
                                        err=True,
                                    )
                                elif src is None:
                                    click.echo(
                                        f"  Server requests update: {__version__} -> "
                                        f"{server_ver}; ignored: no cvcpkg source "
                                        "checkout to update from (set "
                                        f"{_SELF_UPDATE_DIR_ENV})",
                                        err=True,
                                    )
                                elif not _is_newer_version(src[1], __version__):
                                    click.echo(
                                        f"  Server requests update: {__version__} -> "
                                        f"{server_ver}; ignored: {src[0]} has "
                                        f"{src[1]}, not newer than {__version__}"
                                    )
                                else:
                                    click.echo(
                                        f"  Server requests update: {__version__} -> "
                                        f"{server_ver}; installing {src[1]} from "
                                        f"{src[0]} once in-flight jobs finish "
                                        "(taking no new jobs meanwhile)"
                                    )
                                    pending_update = server_ver
                                    _apply_pending_update()

                    elif msg_type == "job.timeout":
                        job_id = msg.get("job_id")
                        click.echo(
                            f"  [{job_id}] Server timed out job",
                            err=True,
                        )

        except Exception as exc:
            if connected_at is None:
                click.echo(f"  WebSocket connection failed: {_ws_error_text(exc)}", err=True)
                return "failed", 0.0
            lost = lost or _ws_error_text(exc)

        connected_for = time.time() - connected_at
        if shutdown:
            return "stopped", connected_for
        click.echo(
            f"  WebSocket connection lost after {connected_for:.0f}s"
            + (f": {lost}" if lost else ""),
            err=True,
        )
        return "lost", connected_for

    # -- Main loop -------------------------------------------

    last_heartbeat = 0.0
    heartbeat_interval = 60.0
    poll_interval = 5.0  # seconds between next-job polls

    # WebSocket reconnect schedule.  A failed handshake or a dropped socket
    # puts the builder on HTTP long-poll, and the socket is retried on a
    # capped exponential backoff (jittered, so a fleet that lost the server
    # together does not come back in lockstep) until it connects again.  The
    # cap bounds how long a builder stays on long-poll after the server side
    # starts accepting upgrades again.  A session that stayed up for
    # ws_stable_secs resets the backoff; one that drops straight after
    # connecting keeps escalating it.
    ws_retry_min = float(os.environ.get("CVCPKG_BUILDER_WS_RETRY_MIN", "5"))
    ws_retry_max = float(os.environ.get("CVCPKG_BUILDER_WS_RETRY_MAX", "300"))
    ws_stable_secs = float(os.environ.get("CVCPKG_BUILDER_WS_STABLE_SECS", "60"))
    # How often a connected builder asks next-job for dispatches the socket
    # did not deliver (see _ws_catch_up).
    ws_sweep_interval = float(os.environ.get("CVCPKG_BUILDER_WS_SWEEP_INTERVAL", "60"))

    # Periodic disk reclamation.  The startup sweep above catches orphans from
    # a previous incarnation; this is the safety net for a builder that stays
    # up for weeks (a job thread wedged before its finally).  Age-gated well
    # past the longest real build (llvm ~2h) and skipping in-flight roots, so
    # it can never touch a live build.
    last_gc = time.time()  # not at 0: startup already swept
    gc_interval = float(os.environ.get("CVCPKG_BUILDER_GC_INTERVAL", "3600"))
    gc_max_age = float(os.environ.get("CVCPKG_BUILDER_GC_MAX_AGE", "21600"))  # 6h
    # Download cache: content-addressed, so pruning only ever costs a
    # re-download.  0 disables (matches the server's retention knobs).
    gc_cache_max_age = float(os.environ.get("CVCPKG_BUILDER_GC_CACHE_MAX_AGE", "1209600"))  # 14d

    def _run_periodic_gc() -> None:
        """Best-effort sweep; never let disk hygiene break the build loop."""
        from cvcpkg.builder_gc import sweep_cache, sweep_work_dir
        from cvcpkg.cache import default_cache_dir

        if work_root is None:
            return
        try:
            with jobs_lock:
                live = list(active_job_roots)
            total = sweep_work_dir(work_root, max_age_seconds=gc_max_age, keep=live)
            total.merge(sweep_cache(default_cache_dir(), max_age_seconds=gc_cache_max_age))
            if total:
                click.echo(
                    f"cvcpkg-builder: gc reclaimed {total.removed} item(s), "
                    f"{total.freed_mib:.0f} MiB"
                )
        except Exception as exc:  # noqa: BLE001 - hygiene must never kill the loop
            click.echo(f"cvcpkg-builder: gc error (ignored): {exc}", err=True)

    # Time-boxed / drain-mode controls for ephemeral (CI) runners.
    run_deadline = (time.time() + max_runtime) if max_runtime else None

    def _past_deadline() -> bool:
        return run_deadline is not None and time.time() >= run_deadline

    # Drain-mode settle window: ``next-job`` only returns jobs the server
    # scheduler has already *dispatched to this builder*, and that loop runs
    # on an interval (~10s).  A freshly-registered drain builder would
    # otherwise get one 204 and exit before its first dispatch, orphaning
    # pending jobs.  Require the queue to stay empty for this long before
    # concluding it is truly drained; reset whenever a job is received.
    drain_settle_secs = float(os.environ.get("CVCPKG_DRAIN_SETTLE_SECS", "20"))
    drain_empty_since: float | None = None

    # Prefer the WebSocket whenever it can be had (unless disabled).  Drain
    # mode (--exit-when-empty) needs the HTTP long-poll path: it returns 204 on
    # an empty queue, which is the signal to exit; the WebSocket path is
    # push-only and never tells us the queue is empty.  An unregistered
    # builder has no builder id to open a socket for.
    ws_wanted = not no_websocket and not exit_when_empty and builder_id is not None
    ws_backoff = _ReconnectBackoff(ws_retry_min, ws_retry_max)
    ws_next_attempt = 0.0  # first attempt straight away

    try:
        # One loop for both transports.  While a socket is up, _run_ws_loop
        # serves it and returns when it ends; every other iteration is one
        # HTTP long-poll round, so a builder whose socket is down keeps
        # taking work and retries the socket when its backoff comes due.
        # Earlier, one failed handshake (or one dropped connection) left the
        # builder on long-poll until it was restarted -- and long-poll never
        # sees builder.update, so fleet self-update could not reach it either.
        while not shutdown:
            # Wall-clock budget: stop claiming, drain in-flight, exit.
            if _past_deadline():
                click.echo(
                    f"Max runtime reached ({max_runtime:.0f}s) - "
                    "stopping claims, finishing in-flight jobs..."
                )
                break

            if ws_wanted and time.time() >= ws_next_attempt:
                outcome, connected_for = _run_ws_loop()
                if outcome == "stopped" or shutdown:
                    break
                if outcome == "unavailable":
                    ws_wanted = False
                else:
                    if outcome == "lost" and connected_for >= ws_stable_secs:
                        ws_backoff.reset()
                    delay = ws_backoff.next_delay()
                    ws_next_attempt = time.time() + delay
                    click.echo(
                        f"  using HTTP long-poll; retrying WebSocket in {delay:.0f}s",
                        err=True,
                    )
                # No `continue`: fall through to one long-poll round, so the
                # builder takes work between attempts whatever the backoff is
                # (CVCPKG_BUILDER_WS_RETRY_MIN=0 used to retry the handshake in
                # a hot loop and never poll at all).

            # Heartbeat
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                _heartbeat()
                last_heartbeat = now

            if gc_interval > 0 and now - last_gc >= gc_interval:
                _run_periodic_gc()
                last_gc = now

            # A builder.update received on a socket that has since dropped
            # still applies once the builder is idle.
            _apply_pending_update()

            # Check capacity
            with jobs_lock:
                available = max_jobs - current_jobs
            if available <= 0 or pending_update:
                time.sleep(poll_interval)
                continue

            # Poll for next job (short timeout so we stay responsive).  An
            # unregistered drainer has nothing to be dispatched *to*, so it
            # selects pending work by platform instead.
            try:
                with httpx.Client(timeout=35) as client:
                    if builder_id is None:
                        _params = {
                            "platform": platform,
                            "arch": arch,
                            # A drainer is anonymous, so it states its host
                            # capabilities per request; the server never
                            # hands it a job requiring anything more.
                            "capabilities": ",".join(sorted(advertised_caps)),
                        }
                        # Same story for disk, and re-measured per poll since a
                        # drainer has no heartbeat to carry it.
                        _free = _measure_free_disk()
                        if _free is not None:
                            _params["free_disk_gb"] = str(_free)
                        resp = client.get(
                            f"{base}/v1/builds/next-claimable",
                            headers=headers,
                            params=_params,
                        )
                    else:
                        resp = client.get(
                            f"{base}/v1/builders/{builder_id}/next-job",
                            headers=headers,
                            params={"timeout": "5"},
                        )
            except Exception as exc:
                click.echo(f"  poll error: {exc}", err=True)
                time.sleep(poll_interval)
                continue

            if resp.status_code == 204:
                # No job dispatched to us.  In drain mode, exit once nothing is
                # left to do: an empty queue AND no in-flight jobs (which could
                # still unlock dependent jobs when they finish).  But only after
                # the queue has stayed empty for the settle window, so we don't
                # exit before the scheduler has had a chance to dispatch pending
                # jobs to a just-registered builder.
                if exit_when_empty:
                    with jobs_lock:
                        inflight = current_jobs
                    if inflight == 0:
                        now = time.time()
                        if drain_empty_since is None:
                            drain_empty_since = now
                        elif now - drain_empty_since >= drain_settle_secs:
                            click.echo("Queue empty - exiting (--exit-when-empty).")
                            break
                    else:
                        drain_empty_since = None
                continue
            if resp.status_code >= 400:
                click.echo(
                    f"  poll failed: {resp.status_code}",
                    err=True,
                )
                time.sleep(poll_interval)
                continue

            try:
                job = resp.json()
            except ValueError:
                job = None
            if not isinstance(job, dict) or job.get("id") is None:
                # A 200 that is not a job (a proxy page, a truncated body).
                # _admit_job runs in this loop, so letting it through would
                # raise here and stop the whole builder.
                click.echo("  poll returned a 200 that is not a job; ignoring", err=True)
                time.sleep(poll_interval)
                continue
            drain_empty_since = None  # got work; restart the settle window
            with jobs_lock:
                # NOT `token`: this runs in builder_run's own scope, so binding
                # the slot id to that name replaced the bearer credential every
                # nested closure reads -- publishes then sent "Bearer 1".
                slot_id = _admit_job(job)
            if slot_id is None:
                # The server handed back a job we are already running: our own
                # claim, sent from the job thread, has not landed yet.  It will
                # within a round trip; don't spin on the poll meanwhile.
                time.sleep(1.0)
                continue

            # Run in a thread so we can keep heartbeating & polling.  The
            # guarded wrapper releases the slot on any exit path.  A thread
            # that cannot start leaves the job dispatched; poll again later
            # rather than spin on it.
            if not _start_job(job, slot_id):
                time.sleep(poll_interval)

    finally:
        if pending_update:
            # Deferred behind a job, and the builder stopped first: no update.
            click.echo(
                f"  self-update: the builder is stopping; dropping the update to {pending_update}"
            )
            pending_update = None
        # Wait for in-flight jobs
        deadline = time.time() + 300  # 5 min grace period
        while current_jobs > 0 and time.time() < deadline:
            click.echo(f"  Waiting for {current_jobs} in-flight job(s)...")
            time.sleep(5)

        if builder_id is None:
            click.echo("Drain finished (nothing registered, nothing to clean up).")
        else:
            click.echo("Shutting down - unregistering builder...")
            try:
                with httpx.Client(timeout=10) as client:
                    resp = client.delete(f"{base}/v1/builders/{builder_id}", headers=headers)
                # httpx does not raise on 4xx, so an unchecked delete reports
                # success while leaving the registration behind — that is how
                # ephemeral CI builders accumulated as dead entries (the
                # endpoint is admin-only and CI runs with a publisher token).
                if resp.status_code >= 400:
                    click.echo(
                        f"Warning: failed to unregister builder #{builder_id} "
                        f"({resp.status_code}: {resp.text[:120]}). "
                        "It will linger in the builder list.",
                        err=True,
                    )
                else:
                    click.echo("Builder unregistered.")
            except Exception as exc:
                click.echo(f"Warning: failed to unregister builder: {exc}", err=True)

        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass


@builder_group.command("unregister")
@click.argument("builder_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token (admin).  [env: CVCPKG_TOKEN]",
)
def builder_unregister(builder_id: int, server: str, token: str):
    """Unregister a builder by ID (admin-only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{builder_id} unregistered.")


@builder_group.command("logs")
@click.argument("builder_id", type=int, required=False)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--limit", type=int, default=20, show_default=True, help="Number of recent jobs to show."
)
@click.option(
    "--status", default=None, help="Filter by job status (e.g. running/failed/succeeded)."
)
@click.option(
    "--tail",
    type=int,
    default=0,
    metavar="LINES",
    help="Also print the last LINES of the most recent job's log.",
)
@click.option(
    "--job", type=int, default=None, help="Tail this specific job ID instead of the latest."
)
def builder_logs(
    builder_id: int | None,
    server: str,
    token: str,
    limit: int,
    status: str | None,
    tail: int,
    job: int | None,
):
    """Show recent build activity, optionally for a single builder.

    Lists the most recent build jobs (newest first) and, with ``--tail``,
    prints the tail of a job's log - a lightweight alternative to the full
    ``cvcpkg builds monitor`` view.
    """
    httpx = require_httpx("builder")

    params: dict[str, str] = {"limit": str(max(1, limit))}
    if builder_id is not None:
        params["builder_id"] = str(builder_id)
    if status:
        params["status"] = status

    base = server.rstrip("/")
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{base}/v1/builds",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")

    jobs = resp.json().get("jobs", [])
    # Newest first by submission time.
    jobs = sorted(jobs, key=lambda j: j.get("submitted_at") or "", reverse=True)

    scope = f" for builder #{builder_id}" if builder_id is not None else ""
    if not jobs:
        click.echo(f"No build jobs found{scope}.")
        return

    click.echo(f"Recent build activity{scope}:")
    click.echo(
        f"{'Job':>6}  {'Recipe':<24} {'Plat/Arch':<16} {'Status':<10} {'Builder':>7}  Submitted"
    )
    click.echo("-" * 90)
    for j in jobs:
        pa = f"{j.get('platform', '?')}/{j.get('arch', '?')}"
        bid = j.get("builder_id")
        click.echo(
            f"{j['id']:>6}  {j.get('recipe_name', '?'):<24} {pa:<16} "
            f"{j.get('status', '?'):<10} {('#' + str(bid)) if bid else '-':>7}  "
            f"{j.get('submitted_at', '')}"
        )

    if tail > 0:
        target = job if job is not None else jobs[0]["id"]
        with httpx.Client(timeout=30) as client:
            log_resp = client.get(
                f"{base}/v1/builds/{target}/log",
                headers={"Authorization": f"Bearer {token}"},
            )
        if log_resp.status_code == 404:
            click.echo(f"\n(no log available for job #{target})")
            return
        if log_resp.status_code >= 400:
            raise click.ClickException(
                f"server returned {log_resp.status_code} fetching log for job #{target}"
            )
        lines = log_resp.text.splitlines()
        click.echo(f"\n-- log tail: job #{target} (last {min(tail, len(lines))} lines) --")
        for line in lines[-tail:]:
            click.echo(line)
