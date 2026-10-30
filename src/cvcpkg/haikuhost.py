# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Delegate Haiku-target builds to a Haiku host over SSH.

The Windows analogue of this module is :mod:`cvcpkg.winhost`, and the
split is the same: source fetch, patching, packaging, and publishing stay
on the Linux builder, only the compile runs elsewhere.  For both, the
reason is that the target toolchain does not exist on the Linux builder.

This module does **not** exist because cvcpkg cannot run on Haiku.  It
can: the client path (``install``, ``build``, ``haiku draft-recipe``)
imports only ``click`` and ``PyYAML``, both of which HaikuPorts ships, so
``pip install --no-deps cvcpkg`` works there today (the ``--no-deps`` is
needed only because the *declared* dependency list still names
``cryptography``/``sqlalchemy``/``greenlet``/``httpx``, which the client
never loads — see ``docs/haikuports-integration.md``).  A Haiku box
driven from here is therefore the *convenient* topology for a Linux CI
fleet, not the only possible one.

The transport is plain OpenSSH (Haiku ships sshd), which also rules the
mode out of the winhost-style "direct" file sharing: there is no shared
filesystem, so every job stages its inputs onto the Haiku side and copies
the install tree back.

Per job the remote layout under ``$CVCPKG_HAIKU_WORKDIR/jobs/<job>`` is::

    source/                 the staged, patched source tree
    build/                  the build dir (cwd of the recipe script)
    install/                CVC_INSTALL_DIR — copied back when the build wins
    deps/                   the resolved dependency prefix
    build-prefix/           the build closure, when it is kept separate
    recipe/<name>/build.sh  the recipe dir…
    recipe/_common/         …plus its sibling _common (env-haiku.sh et al),
                            because build.sh sources "$0/../../_common/…"
    run-job.sh              generated runner: exports the CVC_* env, cd's
                            into build/, exec's the recipe script
    .cvcpkg-job             liveness marker: present while a builder is using
                            this dir, so a concurrent job's housekeeping
                            cannot reap it (see _reap_old_jobs)

A recipe's test script is delegated the same way (job name
``<recipe>-test-<id>``), with only ``install/``, ``deps/`` and ``recipe/``
staged: the install tree that comes back holds Haiku binaries, so testing
it on the Linux builder would exercise nothing.

There is no checked-in remote runner (winhost's ``winhost-run-job.ps1``):
generating ``run-job.sh`` per job keeps delegation working against recipe
bundles that predate Haiku support, which matters far more here than the
"push recipes to update the runner" property does on Windows.

Four Haiku facts shape the details.  Its run-time linker reads
``LIBRARY_PATH``, not ``LD_LIBRARY_PATH`` (hence
:func:`cvcpkg.platform.lib_path_var`), and rsync is not part of the base
system, so file exchange falls back to tar-over-ssh whenever either end
lacks rsync.  Its sshd sometimes never ends a session whose command has
exited, so every remote command reports its own exit status and a stuck
ssh is stopped (see "SSH plumbing"); nothing waits on ssh without a
bound.  And BFS does not keep a whole-second timestamp as it is given,
while the VM's clock is not the builder's, so the pushed source tree is
re-stamped on the Haiku side (see "Source timestamps").

A Haiku job that cannot be delegated is a hard error, never a local
build: see :func:`ensure_delegatable`.

Environment knobs:

``CVCPKG_HAIKUHOST``        ``0``/``false`` makes this builder REFUSE Haiku
                            jobs.  It does not fall back to a local build —
                            there is no local Haiku toolchain, so the
                            fallback would package Linux binaries as
                            ``haiku/x86_64``.
``CVCPKG_HAIKU_SSH``        ``user@host`` of the Haiku box (required — a
                            Haiku job fails loudly until this is set).
``CVCPKG_HAIKU_SSH_KEY``    Identity file for the connection.
``CVCPKG_HAIKU_SSH_PORT``   Non-default SSH port.
``CVCPKG_HAIKU_WORKDIR``    Remote work root (default
                            ``/boot/home/cvcpkg-build``).
``CVCPKG_HAIKU_TRANSFER``   ``auto`` | ``rsync`` | ``tar``.
``CVCPKG_HAIKU_JOBS``       Overrides ``CVC_JOBS`` for the remote build.
``CVCPKG_HAIKU_KEEP_JOBS``  How many kept (failed / ``--keep-build-dir``)
                            remote job dirs to leave behind; the next job
                            reaps the rest.  ``0`` disables reaping.
``CVCPKG_HAIKU_JOB_TTL``    Seconds a job dir's liveness marker protects it
                            from the reaper (default 86400).  Only relevant
                            when several builders share one Haiku box: it
                            bounds how long a CRASHED builder's dir stays
                            unreapable.  Must exceed the longest build.
``CVCPKG_HAIKU_IDLE_TIMEOUT``  Seconds a file transfer or a remote command
                            may go with no byte moving before it is stopped
                            (default 900; ``0`` disables).
``CVCPKG_HAIKU_BUILD_IDLE_TIMEOUT``  The same for the remote build and test
                            scripts, measured on their output (default
                            10800; ``0`` disables).
``CVCPKG_HAIKU_TRANSFER_TIMEOUT``  Hard bound on one file transfer (default
                            14400; ``0`` disables).
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

from cvcpkg import proctree
from cvcpkg.errors import CvcpkgError
from cvcpkg.platform import lib_path_var

if TYPE_CHECKING:  # pragma: no cover - import cycle guard (builder imports us)
    from cvcpkg.builder import BuildContext, MatrixEntry


class HaikuhostError(CvcpkgError):
    """Delegation of a Haiku build to the Haiku host failed."""


# Haiku's home is /boot/home; there is no /home and no /usr, so the usual
# ``~/…`` default would be the one path guaranteed NOT to exist.
DEFAULT_WORKDIR = "/boot/home/cvcpkg-build"

# Keep control-connection setup snappy: an unreachable Haiku VM should fail
# the job in seconds, not hang the builder for the TCP default.
_CONNECT_TIMEOUT = "20"

# How many kept remote job dirs to leave on the Haiku box.  A job that wins
# deletes its own dir; a job that FAILS keeps it for inspection, which on a box
# with a few GiB of working room and no cron is an unbounded leak — a handful of
# failed llvm-sized builds fills the disk and every later job dies staging its
# source.  Each new job therefore reaps the oldest kept dirs down to this many.
DEFAULT_KEEP_JOBS = 3

# Name of the liveness marker a job writes into its own remote dir.  Its
# presence means "a builder is using this dir right now"; it is removed when the
# job finishes, so a KEPT (failed) dir is immediately reapable by count while a
# RUNNING one is not.  See _reap_old_jobs for why a count bound alone is unsafe.
JOB_MARKER = ".cvcpkg-job"

# How long a marker keeps protecting a dir, in seconds.  The marker records when
# the job started, and a builder that is SIGKILLed leaves it behind, so without
# an expiry a single crash would make a dir permanently unreapable — the leak
# the reaper exists to prevent, one level up.  A day is comfortably longer than
# any build this box can host and short enough that a crash costs one dir for
# one day.  Override with CVCPKG_HAIKU_JOB_TTL.
DEFAULT_JOB_TTL = 24 * 60 * 60

# Remote job dirs this process is using right now, by leaf name.  Belt to the
# marker's braces: this survives a failed marker write and never depends on the
# far end, so our own in-flight job is protected from our own reaper even if the
# Haiku box refuses the write.  Entries are discarded when the job finishes, so
# a dir this process KEPT after a failure is still reapable by a later job —
# otherwise a long-lived builder daemon would re-open the very leak the count
# bound closes.
_own_jobs: set[str] = set()

# Haiku's documented default library search path, from
# ``/boot/system/boot/SetupEnvironment``.  ``%A`` is expanded by
# runtime_loader to the directory of the running image; ``$HOME`` is expanded
# by the remote shell, so this string is emitted into the runner
# double-quoted and must stay free of every other shell metacharacter.
# [unverified] Taken from Haiku R1/beta5 documentation, NOT read off a live
# Haiku host (see recipes/_common/env-haiku.sh for the same caveat).  If a
# remote build dies resolving a *system* library, check this list first:
# `getenv LIBRARY_PATH` in a Terminal on the box is the ground truth.
HAIKU_DEFAULT_LIBRARY_PATH = (
    "%A/lib:"
    "$HOME/config/non-packaged/lib:"
    "$HOME/config/lib:"
    "/boot/system/non-packaged/lib:"
    "/boot/system/lib"
)

# Shell-safe subset for the parts of a remote path we synthesize from recipe
# metadata.  The recipe schema already constrains names, but a remote path is
# exactly the wrong place to trust a schema.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# POSIX name for an environment variable we are willing to `export`.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Results of remote `command -v <tool>` probes, cached per process (a probe
# is a full SSH round trip and the answer cannot change mid-run).
_remote_tool_cache: dict[str, bool] = {}


def _env_flag_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "off", "no")


def is_haiku() -> bool:
    """Return True when this process is running on Haiku.

    ``sys.platform`` is tri-valued on Haiku ("haiku", "haiku1", or
    "haikuR1~beta5" depending on whether the interpreter carries
    HaikuPorts' MACHDEP patch), so this must stay a prefix test — see
    :func:`cvcpkg.platform.detect_platform`.
    """
    return sys.platform.startswith("haiku")


def ssh_target() -> str:
    """``user@host`` of the configured Haiku box ("" when unconfigured)."""
    return os.environ.get("CVCPKG_HAIKU_SSH", "").strip()


def remote_workdir() -> str:
    """Remote work root, without a trailing slash."""
    return (os.environ.get("CVCPKG_HAIKU_WORKDIR", "").strip() or DEFAULT_WORKDIR).rstrip("/")


def should_delegate(platform: str, host_platform: str) -> bool:
    """True when this build must run on the Haiku host over SSH.

    True for every haiku target reached from a process that is not itself
    running on Haiku.  Unlike :func:`cvcpkg.winhost.should_delegate` an
    *empty* ``host_platform`` still delegates: a Windows target reached
    from an unqualified build is plausibly a native Windows builder,
    whereas a Haiku target from a non-Haiku host can never be native —
    nothing on the Linux side can compile for Haiku, so this is always a
    cross job.  For the same reason a matrix entry declaring
    ``host_platform: haiku`` does NOT opt out while this process is not
    on Haiku: the declaration cannot make the local toolchain emit Haiku
    binaries, it would only relabel Linux ones.

    Deliberately independent of whether a Haiku host is configured, and
    of ``CVCPKG_HAIKUHOST``.  Both used to be folded in here, so an
    unconfigured builder answered "no, don't delegate" and the caller
    happily ran the recipe's Haiku ``build.sh`` through the LOCAL
    toolchain and packaged Linux binaries as ``haiku/x86_64``.  Whether a
    job *must* be delegated is a property of the job; whether it *can* be
    is a property of the builder, and a builder that cannot must fail —
    see :func:`ensure_delegatable`.
    """
    if platform != "haiku":
        return False
    # On Haiku we ARE the target, so the build is native; anywhere else
    # nothing local can compile for Haiku, so the job must be delegated.
    return not is_haiku()


def ensure_delegatable(platform: str, host_platform: str) -> None:
    """Raise unless a Haiku job that must be delegated actually can be.

    The only two answers for a haiku target on a non-Haiku host are "run
    it on the Haiku box" and "fail"; there is no third answer that
    produces a correct ``haiku/x86_64`` bundle.  Raises
    :class:`HaikuhostError` (a :class:`~cvcpkg.errors.CvcpkgError`)
    naming the knob that is missing.
    """
    if not should_delegate(platform, host_platform):
        return
    where = f"a {host_platform} host" if host_platform else "a non-Haiku host"
    if _env_flag_disabled("CVCPKG_HAIKUHOST"):
        raise HaikuhostError(
            f"this job targets haiku from {where}, but Haiku delegation is "
            "disabled on this builder (CVCPKG_HAIKUHOST=0). Refusing to build: "
            "there is no local Haiku toolchain, so a local build would package "
            "this host's binaries as haiku/x86_64. Unset CVCPKG_HAIKUHOST (and "
            "set CVCPKG_HAIKU_SSH=user@host), or route the job to a builder "
            "that has a Haiku host."
        )
    if not ssh_target():
        raise HaikuhostError(
            f"this job targets haiku from {where}, but no Haiku host is "
            "configured: set CVCPKG_HAIKU_SSH=user@host on the builder (and "
            "CVCPKG_HAIKU_SSH_KEY if the key is not the default identity). "
            "Refusing to build: there is no local Haiku toolchain, so a local "
            "build would package this host's binaries as haiku/x86_64."
        )


# ── SSH plumbing ────────────────────────────────────────────────

# Haiku's sshd does not always finish a session.  In about one run in three
# (hrev57937, openssh 9.8p1, 2026-10-30) the remote command exits and the
# server sends EOF on the channel, but neither ``exit-status`` nor ``close``
# follows: the client waits forever, and an ``sshd-session`` waits with it on
# the Haiku side.  A prod job sat 70 minutes that way in its recipe push.  So
# ssh exiting is not how this module learns that a command finished: every
# remote command reports its own exit status, as a marker line on stderr
# (:func:`_with_rc_marker`).  Once the marker is in, ssh gets
# :data:`EXIT_GRACE_SECS` to exit by itself and is then stopped, and the
# marker's status is the command's.  Stopping the client ends the stuck
# sshd-session too.
_RC_MARKER = "__CVCPKG_RC_"

# How long ssh gets to exit by itself after its remote command reported its
# exit status (and, for a pull, after its stdout reached EOF).  A healthy
# session is gone within milliseconds of that.
EXIT_GRACE_SECS = 5.0

# Seconds with no byte moving (out of ssh, or into it) before a file transfer
# or a remote command is given up.  Override with CVCPKG_HAIKU_IDLE_TIMEOUT;
# 0 disables.
DEFAULT_IDLE_TIMEOUT = 15 * 60

# The same bound for the remote build and test scripts, whose output is their
# only sign of life, and a configure check or a link can be quiet for a long
# time.  Override with CVCPKG_HAIKU_BUILD_IDLE_TIMEOUT; 0 disables.
DEFAULT_BUILD_IDLE_TIMEOUT = 3 * 60 * 60

# Hard bound on one file transfer, however busy.  It is what bounds an rsync
# transfer once rsync's own --timeout no longer applies (see _rsync).
# Override with CVCPKG_HAIKU_TRANSFER_TIMEOUT; 0 disables.
DEFAULT_TRANSFER_TIMEOUT = 4 * 60 * 60

# Copy size of the relay threads.
_CHUNK = 1 << 20

# How often the waiting thread looks at the clock and the process.
_POLL_SECS = 0.1

# How long to wait for a relay thread or a transfer's local tar once ssh is gone.
_DRAIN_SECS = 30.0

# Where notices from this plumbing go: the job log while run_haiku_build or
# run_haiku_test runs on this thread (see _notices_to), stderr otherwise.
_job_log = threading.local()


def _notice(msg: str) -> None:
    log = getattr(_job_log, "log", None)
    if log is not None:
        log(msg)
    else:
        print(msg, file=sys.stderr)


@contextmanager
def _notices_to(log: Callable[[str], None]) -> Iterator[None]:
    previous = getattr(_job_log, "log", None)
    _job_log.log = log
    try:
        yield
    finally:
        _job_log.log = previous


def _seconds_knob(name: str, default: float) -> float | None:
    """A timeout knob, in seconds; None when it is set to 0 (disabled)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise HaikuhostError(f"invalid {name}: {raw!r}") from None
    return value if value > 0 else None


def _idle_timeout() -> float | None:
    return _seconds_knob("CVCPKG_HAIKU_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT)


def _transfer_timeout() -> float | None:
    return _seconds_knob("CVCPKG_HAIKU_TRANSFER_TIMEOUT", DEFAULT_TRANSFER_TIMEOUT)


def _ssh_options() -> list[str]:
    """``ssh`` plus the connection options every invocation shares.

    ``BatchMode=yes`` is the important one: a builder is non-interactive,
    so a missing key or an unknown host key must fail the job instead of
    blocking forever on a prompt.  Host-key checking is deliberately left
    at the OpenSSH default — the Haiku box has to be in ``known_hosts``.

    The keepalives end the session in about a minute when the VM stops
    answering (paused, rebooted, network gone).  They cannot catch the sshd
    hang described above, where the server is alive and answers them.
    """
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={_CONNECT_TIMEOUT}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
    ]
    key = os.environ.get("CVCPKG_HAIKU_SSH_KEY", "").strip()
    if key:
        argv += ["-i", key]
    port = os.environ.get("CVCPKG_HAIKU_SSH_PORT", "").strip()
    if port:
        argv += ["-p", port]
    return argv


def _ssh_cmd(remote_command: str) -> list[str]:
    """Full argv running *remote_command* on the Haiku host.

    ``-T`` because there is no terminal to allocate and a pty would
    mangle the tar streams that share this transport.
    """
    target = ssh_target()
    if not target:
        raise HaikuhostError(
            "no Haiku host configured: set CVCPKG_HAIKU_SSH=user@host (and "
            "CVCPKG_HAIKU_SSH_KEY if the key is not the default identity)"
        )
    return [*_ssh_options(), "-T", target, remote_command]


def _with_rc_marker(remote_command: str, nonce: str) -> str:
    """*remote_command*, followed by a stderr line that reports its exit status.

    The command runs in a subshell, so an ``exit`` in it ends only the
    subshell and the report still runs.  printf puts the marker together from
    its parts, so the whole marker never appears in the command line, and
    nothing that echoes commands can report a completion early.  The leading
    newline puts the marker on a line of its own when the command's last
    stderr line was unterminated; the reader drops the blank line it leaves
    otherwise.
    """
    return f"(\n{remote_command}\n)\nprintf '\\n{_RC_MARKER}%s=%d\\n' {nonce} \"$?\" >&2"


@dataclass
class _Outcome:
    """How a supervised process ended."""

    rc: int
    stdout: bytes = b""
    stderr: bytes = b""
    lingered: bool = False  # ssh had to be stopped after its command was done

    def errtext(self) -> str:
        return self.stderr.decode(errors="replace").strip()[:500]


class _Watch:
    """What the relay threads of one supervised process have seen."""

    def __init__(self) -> None:
        self.last_activity = time.monotonic()
        self.rc: int | None = None  # the remote command's exit status, from its marker

    def moved(self) -> None:
        self.last_activity = time.monotonic()


def _pump_lines(
    pipe: IO[bytes],
    emit: Callable[[bytes], object],
    marker: bytes | None,
    watch: _Watch,
) -> None:
    """Hand *pipe*'s lines to *emit*, all but the completion marker, which goes to *watch*."""
    held: bytes | None = None  # a blank line, perhaps the marker's own leading newline
    try:
        for line in iter(pipe.readline, b""):
            watch.moved()
            if marker is not None and line.startswith(marker):
                try:
                    watch.rc = int(line[len(marker) :].strip())
                    held = None
                    continue
                except ValueError:
                    pass
            if held is not None:
                emit(held)
                held = None
            if line in (b"\n", b"\r\n"):
                held = line
            else:
                emit(line)
    except (OSError, ValueError):
        pass  # the pipe went away with its process
    if held is not None:
        emit(held)


def _pump_chunks(pipe: IO[bytes], dest: IO[bytes] | list[bytes], watch: _Watch) -> None:
    """Copy *pipe* into *dest*: a list to collect into, or a binary file closed at EOF.

    A file that stops taking data (a local tar that died) is not a reason to
    stop reading: draining lets ssh finish, and the tar's own exit status
    reports the failure.
    """
    broken = False
    try:
        while chunk := pipe.read1(_CHUNK):  # type: ignore[attr-defined]
            watch.moved()
            if isinstance(dest, list):
                dest.append(chunk)
            elif not broken:
                try:
                    dest.write(chunk)
                except (OSError, ValueError):
                    broken = True
    except (OSError, ValueError):
        pass
    finally:
        if not isinstance(dest, list):
            try:
                dest.close()
            except (OSError, ValueError):
                pass


def _feed(src: bytes | IO[bytes], pipe: IO[bytes], watch: _Watch) -> None:
    """Write *src* (bytes, or a binary file read to EOF) into *pipe*, then close it."""
    try:
        if isinstance(src, bytes):
            view = memoryview(src)
            for i in range(0, len(view), _CHUNK):
                pipe.write(view[i : i + _CHUNK])
                watch.moved()
        else:
            while chunk := src.read1(_CHUNK):  # type: ignore[attr-defined]
                pipe.write(chunk)
                watch.moved()
    except (OSError, ValueError):
        pass  # the process went away; its exit status says why
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _start_process(argv: list[str], *, what: str, in_tree: bool, **kwargs) -> subprocess.Popen:
    """Start a helper process that a timeout here can stop with all it started.

    On POSIX it leads a process group of its own.  With *in_tree* it is also
    in the job's process tree, so a job the builder stops (job.cancel, the
    build timeout, the offline reaper) stops it too: see cvcpkg.proctree.
    The short commands stay outside the tree, each bounded by its own
    timeout, so that a stopped job can still clean up its remote dir.
    """
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    try:
        if in_tree:
            return proctree.popen(argv, **kwargs)
        return subprocess.Popen(argv, **kwargs)  # noqa: S603 - our own fixed argv
    except OSError as exc:
        raise HaikuhostError(f"{what}: could not start {argv[0]}: {exc}") from exc


def _supervise(
    proc: subprocess.Popen,
    *,
    what: str,
    marker: bytes | None,
    stdin: bytes | IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
    on_output: Callable[[bytes], None] | None = None,
    timeout: float | None = None,
    idle_timeout: float | None = None,
) -> _Outcome:
    """Wait for *proc*, relaying its streams, and never without a bound.

    *proc* was started with stdout piped and, unless *on_output* is given, a
    separate stderr pipe.  *stdin* (bytes, or a binary file such as a local
    tar's stdout) is fed into its stdin pipe; its stdout is copied into
    *stdout* (a binary file such as a local tar's stdin) or collected; with
    *on_output* stdout carries stderr too, and each line goes to *on_output*
    on this thread.

    The wait ends when the process exits, or when the remote command's
    *marker* line has arrived, stdout is at EOF and the process still has not
    exited after :data:`EXIT_GRACE_SECS` (the Haiku sshd hang), or when
    *idle_timeout* passes with no byte moving or *timeout* passes overall.
    The process is then stopped with everything it started
    (:func:`cvcpkg.proctree.stop`).  A timeout raises :class:`HaikuhostError`;
    otherwise the marker's status, or else the process's own, is the result.
    """
    watch = _Watch()
    threads: list[threading.Thread] = []
    out: list[bytes] = []
    err: list[bytes] = []
    lines: queue.SimpleQueue[bytes] = queue.SimpleQueue()

    def _spawn(target: Callable[..., None], *args: object) -> threading.Thread:
        t = threading.Thread(target=target, args=args, name=f"cvcpkg-haikuhost{target.__name__}")
        t.daemon = True
        t.start()
        threads.append(t)
        return t

    assert proc.stdout is not None
    data: threading.Thread | None = None
    if on_output is not None:
        _spawn(_pump_lines, proc.stdout, lines.put, marker, watch)
    else:
        assert proc.stderr is not None
        data = _spawn(_pump_chunks, proc.stdout, out if stdout is None else stdout, watch)
        _spawn(_pump_lines, proc.stderr, err.append, marker, watch)
    if stdin is not None:
        assert proc.stdin is not None
        _spawn(_feed, stdin, proc.stdin, watch)

    def _drain(wait: float) -> None:
        if on_output is None:
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                pass
            return
        try:
            on_output(lines.get(timeout=wait) if wait else lines.get_nowait())
            while True:
                on_output(lines.get_nowait())
        except queue.Empty:
            pass

    def _reap() -> None:
        if proc.poll() is None:
            proctree.stop(proc)
        try:
            proc.wait(timeout=_DRAIN_SECS)
        except subprocess.TimeoutExpired:
            pass
        for t in threads:
            t.join(timeout=_DRAIN_SECS)

    started = time.monotonic()
    done_since: float | None = None
    lingered = False
    gave_up = ""
    try:
        while True:
            _drain(_POLL_SECS)
            if proc.poll() is not None:
                break
            now = time.monotonic()
            if watch.rc is not None and (data is None or not data.is_alive()):
                # The command is done and all its output is here; only ssh's
                # own exit is missing.
                if done_since is None:
                    done_since = now
                elif now - done_since >= EXIT_GRACE_SECS:
                    lingered = True
                    break
                continue
            if idle_timeout is not None and now - watch.last_activity > idle_timeout:
                gave_up = f"nothing moved for {idle_timeout:g}s"
                break
            if timeout is not None and now - started > timeout:
                gave_up = f"still running after {timeout:g}s"
                break
    except BaseException:
        _reap()
        raise
    _reap()
    _drain(0)

    rc = watch.rc if watch.rc is not None else proc.returncode
    result = _Outcome(
        rc=-1 if rc is None else rc,
        stdout=b"".join(out),
        stderr=b"".join(err),
        lingered=lingered,
    )
    if gave_up:
        detail = result.errtext()
        raise HaikuhostError(
            f"{what} on {ssh_target()}: {gave_up}, so it was stopped"
            + (f": {detail}" if detail else "")
        )
    if lingered:
        _notice(
            f"cvcpkg-haikuhost: {what}: ssh had not exited {EXIT_GRACE_SECS:g}s after the "
            f"remote command finished (rc={result.rc}), so it was stopped"
        )
    return result


def _ssh_exec(
    remote_command: str,
    *,
    what: str,
    stdin: bytes | IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
    on_output: Callable[[bytes], None] | None = None,
    timeout: float | None = None,
    idle_timeout: float | None = None,
    in_tree: bool = False,
) -> _Outcome:
    """Run *remote_command* on the Haiku host behind a completion marker.

    See :func:`_supervise` for the streams and the bounds, and
    :func:`_start_process` for *in_tree*.
    """
    nonce = uuid.uuid4().hex
    proc = _start_process(
        _ssh_cmd(_with_rc_marker(remote_command, nonce)),
        what=what,
        in_tree=in_tree,
        stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if on_output is not None else subprocess.PIPE,
    )
    return _supervise(
        proc,
        what=what,
        marker=f"{_RC_MARKER}{nonce}=".encode(),
        stdin=stdin,
        stdout=stdout,
        on_output=on_output,
        timeout=timeout,
        idle_timeout=idle_timeout,
    )


def _run_ssh(remote_command: str, *, what: str, timeout: float = 300) -> str:
    """Run a short *remote_command* on the Haiku host, return stripped stdout."""
    r = _ssh_exec(remote_command, what=what, timeout=timeout)
    if r.rc != 0:
        raise HaikuhostError(f"{what} failed on {ssh_target()} (rc={r.rc}): {r.errtext()}")
    return r.stdout.decode(errors="replace").strip()


def haikuhost_available() -> bool:
    """Best-effort check that the configured Haiku host answers."""
    if not ssh_target():
        return False
    try:
        return _ssh_exec("true", what="probing the Haiku host", timeout=60).rc == 0
    except HaikuhostError:
        return False


def _remote_has(tool: str) -> bool:
    """Is *tool* on the Haiku host's PATH?  Cached per process."""
    if tool not in _remote_tool_cache:
        try:
            _run_ssh(f"command -v {shlex.quote(tool)} >/dev/null 2>&1", what=f"{tool} probe")
            _remote_tool_cache[tool] = True
        except HaikuhostError:
            _remote_tool_cache[tool] = False
    return _remote_tool_cache[tool]


def _stream_ssh(
    remote_command: str,
    log_callback: Callable[[str], None] | None,
) -> int:
    """Run a remote command, teeing its output to stdout and the log.

    In the job's process tree, so a stopped job stops it, and bounded by
    CVCPKG_HAIKU_BUILD_IDLE_TIMEOUT.
    """
    _flush_bytes = 8192
    buf: list[str] = []
    buf_size = 0

    def _emit(raw_line: bytes) -> None:
        nonlocal buf_size
        line = raw_line.decode("utf-8", errors="replace")
        sys.stdout.write(line)
        if log_callback:
            buf.append(line)
            buf_size += len(line)
            if buf_size >= _flush_bytes:
                log_callback("".join(buf))
                buf.clear()
                buf_size = 0

    try:
        r = _ssh_exec(
            remote_command,
            what="the remote job",
            on_output=_emit,
            idle_timeout=_seconds_knob(
                "CVCPKG_HAIKU_BUILD_IDLE_TIMEOUT", DEFAULT_BUILD_IDLE_TIMEOUT
            ),
            in_tree=True,
        )
    finally:
        if log_callback and buf:
            log_callback("".join(buf))
    return r.rc


# ── File exchange ───────────────────────────────────────────────


def _transfer_mode() -> str:
    """Which file-exchange mechanism to use: ``rsync`` or ``tar``.

    rsync is preferable (incremental, preserves modes) but is a
    HaikuPorts package, not part of the base system, so it may well be
    missing on the far end; tar-over-ssh needs nothing that is not
    already there.  Both ends must have rsync for it to be usable.
    """
    mode = os.environ.get("CVCPKG_HAIKU_TRANSFER", "auto").strip().lower() or "auto"
    if mode not in ("auto", "rsync", "tar"):
        raise HaikuhostError(f"invalid CVCPKG_HAIKU_TRANSFER: {mode!r}")
    if mode != "auto":
        return mode
    if shutil.which("rsync") and _remote_has("rsync"):
        return "rsync"
    return "tar"


def _rsync_argv() -> list[str]:
    """``rsync`` with the SSH transport and options this module needs.

    ``-s`` (``--protect-args``) stops the remote shell from re-splitting
    the remote path: rsync otherwise hands it to a shell, so a space in
    the work dir would silently split into two transfers.
    """
    ssh_e = " ".join(shlex.quote(a) for a in _ssh_options())
    return ["rsync", "-a", "-s", "-e", ssh_e]


def _rsync(src: str, dst: str, *, what: str) -> None:
    """Run one rsync transfer, in the job's process tree and bounded.

    rsync starts its own ssh and prints nothing while it works, so neither the
    completion marker nor the idle watch can see into it.  Its own
    ``--timeout`` bounds a stalled transfer; the hard transfer timeout bounds
    the rest, which includes the Haiku sshd hang, since rsync waits for its ssh
    to exit.  The Haiku VMs have no rsync, so ``auto`` picks tar there.
    """
    argv = _rsync_argv()
    idle = _idle_timeout()
    if idle is not None:
        argv.append(f"--timeout={max(1, int(idle))}")
    proc = _start_process(
        [*argv, src, dst],
        what=what,
        in_tree=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    r = _supervise(proc, what=what, marker=None, timeout=_transfer_timeout())
    if r.rc != 0:
        raise HaikuhostError(f"{what} failed (rsync rc={r.rc}): {r.errtext()}")


def _finish_local(proc: subprocess.Popen, *, wait: bool) -> int:
    """Reap a transfer's local tar: let it finish if the transfer worked, else stop it.

    A stopped tar has its pipes closed only after it is gone: a relay thread
    may still be blocked on one of them, and only the tar's exit releases it.
    """

    def _close_pipes() -> None:
        # EOF for a tar that reads, EPIPE for one that writes to a dead ssh.
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass

    rc: int | None = None
    if wait:
        _close_pipes()
        try:
            rc = proc.wait(timeout=_DRAIN_SECS)
        except subprocess.TimeoutExpired:
            pass
    if rc is None:
        proctree.stop(proc)
        try:
            rc = proc.wait(timeout=_DRAIN_SECS)
        except subprocess.TimeoutExpired:
            rc = -1
        _close_pipes()
    return rc


def _tar_push(local: Path, remote_dir: str, *, what: str) -> None:
    """``tar -c`` here, piped through ssh into ``tar -x`` on the Haiku side."""
    q = shlex.quote(remote_dir)
    tar = _start_process(
        ["tar", "-C", str(local), "-cf", "-", "."],
        what=what,
        in_tree=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    assert tar.stdout is not None
    finished = False
    try:
        r = _ssh_exec(
            f"mkdir -p {q} && tar -C {q} -xf -",
            what=what,
            stdin=tar.stdout,
            timeout=_transfer_timeout(),
            idle_timeout=_idle_timeout(),
            in_tree=True,
        )
        finished = True
    finally:
        tar_rc = _finish_local(tar, wait=finished)
    if r.rc != 0:
        raise HaikuhostError(f"{what} failed (rc={r.rc}): {r.errtext()}")
    if tar_rc != 0:
        raise HaikuhostError(f"{what} failed: tar exited with code {tar_rc}")


def _tar_pull(remote_dir: str, local: Path, *, what: str) -> None:
    """``tar -c`` on the Haiku side, piped through ssh into ``tar -x`` here.

    The marker goes to stderr, apart from the archive.  The server sends EOF
    after the last byte even when it then hangs, so the local tar has the
    whole archive by the time the stuck ssh is stopped.
    """
    tar = _start_process(
        ["tar", "-C", str(local), "-xf", "-"],
        what=what,
        in_tree=True,
        stdin=subprocess.PIPE,
    )
    assert tar.stdin is not None
    finished = False
    try:
        r = _ssh_exec(
            f"tar -C {shlex.quote(remote_dir)} -cf - .",
            what=what,
            stdout=tar.stdin,
            timeout=_transfer_timeout(),
            idle_timeout=_idle_timeout(),
            in_tree=True,
        )
        finished = True
    finally:
        tar_rc = _finish_local(tar, wait=finished)
    if r.rc != 0:
        raise HaikuhostError(f"{what} failed (rc={r.rc}): {r.errtext()}")
    if tar_rc != 0:
        raise HaikuhostError(f"{what} failed: tar exited with code {tar_rc}")


def _push_tree(local: Path, remote_dir: str, *, what: str) -> str:
    """Copy the CONTENTS of *local* into the remote *remote_dir*.

    Returns the transfer mode actually used, for the log.
    """
    mode = _transfer_mode()
    if mode == "rsync":
        _rsync(f"{str(local).rstrip('/')}/", f"{ssh_target()}:{remote_dir}/", what=what)
    else:
        _tar_push(local, remote_dir, what=what)
    return mode


def _pull_tree(remote_dir: str, local: Path, *, what: str) -> str:
    """Copy the CONTENTS of the remote *remote_dir* into *local*."""
    local.mkdir(parents=True, exist_ok=True)
    mode = _transfer_mode()
    if mode == "rsync":
        _rsync(f"{ssh_target()}:{remote_dir}/", f"{str(local).rstrip('/')}/", what=what)
    else:
        _tar_pull(remote_dir, local, what=what)
    return mode


def _put_text(text: str, remote_path: str, *, what: str) -> None:
    """Write *text* to *remote_path* on the Haiku host."""
    r = _ssh_exec(f"cat > {shlex.quote(remote_path)}", what=what, stdin=text.encode(), timeout=120)
    if r.rc != 0:
        raise HaikuhostError(f"{what} failed on {ssh_target()} (rc={r.rc}): {r.errtext()}")


# ── Path fix-ups ────────────────────────────────────────────────


def _stage_prefix_rewrites(root: Path, old_prefix: str, new_prefix: str, stage: Path) -> int:
    """Stage rewritten ``.pc``/``.cmake`` files for an overlay push.

    The builder extracts dependency packages locally and rewrites their
    ``prefix=`` lines to the local prefix path; the Haiku host needs the
    same files pointing at the remote staging location instead.  Rather
    than copy the whole (multi-gigabyte) prefix locally just to edit a
    handful of text files — what winhost's exchange mode does, because
    there the "remote" side is a local mount — this collects ONLY the
    files that mention the old prefix into *stage*, at their paths
    relative to *root*, so the caller can push a tiny overlay on top of
    the already-transferred prefix.

    Returns the number of files staged.
    """
    if not root.is_dir():
        return 0
    count = 0
    for f in list(root.rglob("*.pc")) + list(root.rglob("*.cmake")):
        if not f.is_file() or f.is_symlink():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old_prefix not in text:
            continue
        out = stage / f.relative_to(root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text.replace(old_prefix, new_prefix), encoding="utf-8")
        count += 1
    return count


# ── Source timestamps ───────────────────────────────────────────

# BFS keeps the sub-second part of a timestamp on a grid of about 262 µs (it
# stores (((ns + 16383) >> 14) & 0xfff0) << 14, and uses the low bits to keep
# its time index keys unique), and it does not store a ZERO part as zero: it
# fills it in from the moment of the utime call.  Every tar member has a whole-second
# time, so `tar -x` on Haiku leaves files that shared a time with different
# ones, ordered by extraction.  make then sees shipped outputs as older than
# their inputs and regenerates them with tools the box lacks: texi2html for
# gperf, automake-1.15 for flex.  A nonzero part is stored the same way every
# time (.5 always reads back as .499908608).  Measured on hrev57937,
# 2026-10-30.
#
# The Haiku clock is also not the builder's.  phm's VM ran about 21 s behind
# its host, so a tree stamped with the builder's "now" (_settle_mtimes, or a
# recipe patch) was in the Haiku future, and automake's configure stopped
# with "newly created file is older than distributed files!" (m4, iconv,
# libtasn1).
#
# So after the source push, every entry is re-stamped on the Haiku side from
# its local time: see _restamp_plan.

# The newest re-stamped time stays at least this many seconds below the Haiku
# clock.
_RESTAMP_MARGIN_SECS = 2

# Bytes of path arguments per remote touch.  Haiku's ARG_MAX is 128 KiB,
# environment included.
_RESTAMP_ARG_BYTES = 32 * 1024

# Hard bound on the re-stamp of one tree, which prints nothing while it runs.
_RESTAMP_TIMEOUT = 15 * 60


@dataclass
class _RestampPlan:
    """The ``touch -d`` times that re-stamp a pushed tree on the Haiku side."""

    shift: int  # seconds every time moved back, to stay below the Haiku clock
    groups: list[tuple[str, list[str]]]  # (``@sec.frac``, relative paths), oldest first
    entries: int


def _tree_mtimes(root: Path) -> list[tuple[str, int]]:
    """``(relative path, mtime in ns)`` of *root* and every entry under it.

    Regular files, directories and symlinks (the link itself), which is what
    the transfer carries.  Paths are POSIX-style, ``.`` for the root.
    """
    out = [(".", os.lstat(root).st_mtime_ns)]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()  # in place, so the walk itself goes in name order
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            mode = st.st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                continue
            out.append((name if rel_dir == "." else f"{rel_dir}/{name}", st.st_mtime_ns))
    return out


def _restamp_plan(entries: Sequence[tuple[str, int]], remote_now: int) -> _RestampPlan:
    """Plan the re-stamp of a pushed tree from its local times and the Haiku clock.

    *entries* are ``(relative path, mtime in ns)`` as on the builder, and
    *remote_now* is the Haiku clock in whole seconds.

    Each entry keeps its own whole second, moved back by one shift for the
    whole tree if its newest entry would otherwise be less than
    :data:`_RESTAMP_MARGIN_SECS` older than the Haiku clock.  The shift is the
    same for every entry, so the order between seconds is kept, and a tree
    that is old enough keeps its times.

    The sub-second part is never zero (see above).  A second that holds one
    distinct local time gets .5; one that holds k gets (i+1)/(k+1), in their
    local order.  Equal times stay equal, and a file that a recipe patch
    changed in the same second as the tree was settled still comes out
    newer.  Times closer than BFS's grid tie; their order never flips.
    """
    by_sec: dict[int, set[int]] = {}
    for _, ns in entries:
        sec, sub = divmod(ns, 1_000_000_000)
        by_sec.setdefault(sec, set()).add(sub)
    if not by_sec:
        return _RestampPlan(shift=0, groups=[], entries=0)
    shift = max(0, max(by_sec) + 1 + _RESTAMP_MARGIN_SECS - remote_now)
    target: dict[tuple[int, int], tuple[int, int]] = {}
    for sec, subs in by_sec.items():
        ordered = sorted(subs)
        for i, sub in enumerate(ordered):
            target[(sec, sub)] = (sec - shift, (i + 1) * 1_000_000_000 // (len(ordered) + 1))
    grouped: dict[tuple[int, int], list[str]] = {}
    for path, ns in entries:
        grouped.setdefault(target[divmod(ns, 1_000_000_000)], []).append(path)
    groups = [(f"@{sec}.{frac:09d}", paths) for (sec, frac), paths in sorted(grouped.items())]
    return _RestampPlan(shift=shift, groups=groups, entries=len(entries))


def _restamp_script(plan: _RestampPlan) -> bytes:
    """The ``sh`` script that applies *plan*, run in the tree's root.

    One ``touch`` per distinct time, and another each time its paths pass
    :data:`_RESTAMP_ARG_BYTES`, rather than one per file: process start-up is
    the slow part on Haiku.  ``-h`` stamps a symlink itself, ``-c`` creates
    nothing, and a failed touch fails the script after the rest have run.
    """
    lines = ["rc=0"]
    for stamp, paths in plan.groups:
        chunk: list[str] = []
        size = 0
        for path in paths:
            quoted = shlex.quote(path)
            if chunk and size + len(quoted) + 1 > _RESTAMP_ARG_BYTES:
                lines.append(f"touch -c -h -d {stamp} -- {' '.join(chunk)} || rc=1")
                chunk, size = [], 0
            chunk.append(quoted)
            size += len(quoted) + 1
        if chunk:
            lines.append(f"touch -c -h -d {stamp} -- {' '.join(chunk)} || rc=1")
    lines.append('exit "$rc"')
    return ("\n".join(lines) + "\n").encode("utf-8", "surrogateescape")


def _restamp_source(local: Path, remote_dir: str, log: Callable[[str], None]) -> None:
    """Re-stamp the pushed source tree on the Haiku side (see :func:`_restamp_plan`).

    Best effort: a tree that cannot be re-stamped is built as it is, which
    many builds survive; the log says so first.
    """
    try:
        entries = _tree_mtimes(local)
        before = time.time()
        remote_now = int(_run_ssh("date -u +%s", what="reading the Haiku clock", timeout=60))
        skew = remote_now - (before + time.time()) / 2
        plan = _restamp_plan(entries, remote_now)
        started = time.monotonic()
        r = _ssh_exec(
            f"cd {shlex.quote(remote_dir)} && sh",
            what="re-stamping the source tree",
            stdin=_restamp_script(plan),
            timeout=_RESTAMP_TIMEOUT,
            in_tree=True,
        )
    except (HaikuhostError, OSError, ValueError) as exc:
        log(
            "cvcpkg-haikuhost: could not re-stamp the source tree on the Haiku side "
            f"({exc}); building it with the timestamps BFS gave it"
        )
        return
    if r.rc != 0:
        log(
            f"cvcpkg-haikuhost: re-stamping the source tree failed (rc={r.rc}): "
            f"{r.errtext()}; building it with what timestamps it has"
        )
        return
    log(
        f"cvcpkg-haikuhost: re-stamped {plan.entries} source entries "
        f"({len(plan.groups)} distinct times) in {time.monotonic() - started:.1f}s; "
        f"the Haiku clock is {skew:+.0f}s from this builder's, times moved back {plan.shift}s"
    )


# ── Remote housekeeping ─────────────────────────────────────────


def _keep_jobs() -> int:
    """How many kept remote job dirs to leave behind (<= 0 disables reaping)."""
    raw = os.environ.get("CVCPKG_HAIKU_KEEP_JOBS", "").strip()
    if not raw:
        return DEFAULT_KEEP_JOBS
    try:
        return int(raw)
    except ValueError:
        raise HaikuhostError(f"invalid CVCPKG_HAIKU_KEEP_JOBS: {raw!r}") from None


def _job_ttl() -> float:
    """Seconds a liveness marker keeps protecting its job dir from the reaper."""
    raw = os.environ.get("CVCPKG_HAIKU_JOB_TTL", "").strip()
    if not raw:
        return float(DEFAULT_JOB_TTL)
    try:
        return float(raw)
    except ValueError:
        raise HaikuhostError(f"invalid CVCPKG_HAIKU_JOB_TTL: {raw!r}") from None


def _marker_text() -> str:
    """Contents of a job's liveness marker: start time and who owns it.

    One line, whitespace-separated, epoch first — ``_list_remote_jobs`` reads
    it back with ``cat`` (the only text tool a stock Haiku box is guaranteed to
    have) and parses the first field.  The owner half is for a human reading
    the box, never for a decision.

    The clock is the BUILDER's, and it is compared against the builder's clock
    again in :func:`_reap_old_jobs` — never against the Haiku box's — so a VM
    with a wrong RTC cannot make a live job look expired.  Two builders sharing
    one box do compare across machines; that is a normal NTP-grade assumption
    and only affects the expiry of a crashed builder's marker.
    """
    who = _SAFE_NAME_RE.sub("-", f"pid{os.getpid()}@{socket.gethostname()}")
    return f"{int(time.time())} {who}"


def _claim_job(job: str, job_name: str, log: Callable[[str], None]) -> None:
    """Mark a freshly created remote job dir as live.

    Registers the dir locally first: the local set is what protects this
    process's own job, and it must hold even if the remote write fails.
    """
    _own_jobs.add(job_name)
    try:
        _put_text(_marker_text(), f"{job}/{JOB_MARKER}", what="writing the job marker")
    except HaikuhostError as exc:
        # Not fatal: only a CONCURRENT builder reads this marker, and the
        # far end is about to be exercised much harder than one small write.
        log(f"cvcpkg-haikuhost: could not mark {job} as live ({exc})")


def _release_job(job: str, job_name: str, log: Callable[[str], None]) -> None:
    """Drop a job's liveness claim (remote marker + local registration).

    A dir kept after a failure must become reapable again, or the count bound
    that caps disk use never applies to the dirs it exists for.
    """
    _own_jobs.discard(job_name)
    try:
        _run_ssh(
            f"rm -f {shlex.quote(f'{job}/{JOB_MARKER}')}",
            what="clearing the job marker",
            timeout=120,
        )
    except HaikuhostError as exc:
        log(f"cvcpkg-haikuhost: could not clear the job marker in {job} ({exc})")


def _finish_job(
    job: str,
    job_name: str,
    *,
    ok: bool,
    keep: bool,
    kind: str,
    log: Callable[[str], None],
) -> None:
    """Tear a finished job down: remove the dir, or hand it back to the reaper.

    Best effort throughout — a build that SUCCEEDED must not be failed by a
    janitorial SSH hiccup, so everything here reports and moves on.
    """
    if ok and not keep:
        _own_jobs.discard(job_name)
        try:
            _run_ssh(f"rm -rf {shlex.quote(job)}", what=f"cleaning the remote {kind} dir")
        except HaikuhostError as exc:
            log(f"cvcpkg-haikuhost: could not remove {ssh_target()}:{job} ({exc})")
            # The dir outlived its job: drop the claim so the reaper can have it.
            _release_job(job, job_name, log)
        return
    # Kept — for inspection after a failure, or because --keep-build-dir asked.
    # Either way nothing is using it any more, so it must stop looking live or
    # the count bound would never apply to the dirs it exists for.
    _release_job(job, job_name, log)
    if not ok:
        log(f"cvcpkg-haikuhost: remote {kind} dir kept for inspection: {ssh_target()}:{job}")


def _list_remote_jobs(jobs_root: str) -> list[tuple[str, float | None]]:
    """Remote job dirs as ``(name, started_epoch)``, newest first.

    ``started_epoch`` is ``None`` when the dir carries no readable marker —
    a dir from an older cvcpkg, or one whose marker was cleared because its
    job finished.  Ordering comes from ``ls -1t`` (POSIX: newest mtime first)
    rather than ``find``/``stat``, neither of which a stock Haiku box is
    guaranteed to have; ``ls`` and ``cat`` are all this needs.

    Output is ``<name>|<marker>`` so a name may contain anything except the
    delimiter — and names that are not of the shape we mint are dropped by the
    caller anyway, well before any ``rm``.
    """
    listing_cmd = "\n".join(
        (
            f"cd {shlex.quote(jobs_root)} 2>/dev/null || exit 0",
            "for d in $(ls -1t 2>/dev/null); do",
            '    [ -d "$d" ] || continue',
            "    m=",
            f'    if [ -f "$d/{JOB_MARKER}" ]; then m=$(cat "$d/{JOB_MARKER}" 2>/dev/null); fi',
            '    printf \'%s|%s\\n\' "$d" "$m"',
            "done",
        )
    )
    listing = _run_ssh(listing_cmd, what="listing kept remote job dirs", timeout=120)
    out: list[tuple[str, float | None]] = []
    for line in listing.splitlines():
        name, sep, marker = line.partition("|")
        name = name.strip()
        if not sep or not name:
            continue
        fields = marker.split()
        started: float | None = None
        if fields:
            try:
                started = float(fields[0])
            except ValueError:
                # A marker we cannot read is still a marker: treat the dir as
                # freshly claimed rather than guessing it is dead.
                started = time.time()
        out.append((name, started))
    return out


def _reap_old_jobs(log: Callable[[str], None]) -> None:
    """Trim kept remote job dirs, never touching one that is in use.

    Successful jobs remove their own dir, but a FAILED job keeps its dir
    "for inspection" forever, and the Haiku box is a VM with a few GiB of
    working room and no cron to sweep it — so three failed builds of
    anything large is a disk-full outage that then fails every later job
    while staging its source.  Each new job trims the tail here first.

    The trim used to be purely count-based ("delete everything past the
    newest N"), which is only safe while one builder owns the box: two
    concurrent Haiku jobs against one host, or a second builder pointed at the
    same ``CVCPKG_HAIKU_WORKDIR``, and the newer job's reap deletes the older
    job's SOURCE AND BUILD TREE out from under a running compile.  ``ls -1t``
    orders by mtime, and a long build barely touches its own top-level dir, so
    a live job sorts steadily further down the list the longer it runs — the
    reaper was most likely to destroy exactly the builds that cost the most.

    Liveness now comes first, and from two independent sources:

    * ``_own_jobs`` — the dirs THIS process is using.  Purely local, so it
      holds even when the far end is refusing writes.
    * the per-dir marker file (:data:`JOB_MARKER`), written when a job claims
      its dir and removed when the job ends.  This is what makes a
      *concurrent* builder's dir off limits.  It expires after
      :func:`_job_ttl` so a builder that is killed mid-job costs one
      unreapable dir for a day, not forever.

    Only what neither source claims is a candidate, and the count bound then
    applies to those — so "keep the newest N" now means N *finished* dirs,
    which is what the knob was always meant to mean.

    Best effort throughout: housekeeping must never fail a build.
    """
    try:
        keep = _keep_jobs()
        ttl = _job_ttl()
    except HaikuhostError as exc:
        log(f"cvcpkg-haikuhost: not reaping remote job dirs ({exc})")
        return
    if keep <= 0:
        return
    jobs_root = f"{remote_workdir()}/jobs"
    try:
        entries = _list_remote_jobs(jobs_root)
    except HaikuhostError as exc:
        log(f"cvcpkg-haikuhost: could not list {jobs_root} for reaping ({exc})")
        return

    now = time.time()
    live = 0
    candidates: list[str] = []
    for name, started in entries:
        # Only ever consider names of the shape we mint ourselves: a listing is
        # remote input, and `rm -rf` is the wrong place to be relaxed about it.
        if name != _safe_name(name):
            continue
        if name in _own_jobs:
            live += 1
            continue
        # A negative age (marker stamped in the "future") means a clock-skewed
        # peer, not a dead job, and stays on the live side of this test.
        if started is not None and now - started < ttl:
            live += 1
            continue
        candidates.append(name)
    stale = candidates[keep:]
    if not stale:
        return
    reaped = 0
    for i in range(0, len(stale), 25):  # chunked: a long tail must not blow argv
        chunk = stale[i : i + 25]
        victims = " ".join(shlex.quote(f"{jobs_root}/{n}") for n in chunk)
        try:
            _run_ssh(f"rm -rf {victims}", what="reaping kept remote job dirs")
        except HaikuhostError as exc:
            log(f"cvcpkg-haikuhost: could not reap old job dirs under {jobs_root} ({exc})")
            break
        reaped += len(chunk)
    if reaped:
        spared = f", sparing {live} in use" if live else ""
        log(
            f"cvcpkg-haikuhost: reaped {reaped} finished job dir(s) under "
            f"{ssh_target()}:{jobs_root} (keeping the newest {keep}{spared})"
        )


# ── Job construction ────────────────────────────────────────────


def _safe_name(name: str) -> str:
    """Sanitize *name* for use as a remote path component."""
    cleaned = _SAFE_NAME_RE.sub("-", name).strip("-.")
    return cleaned or "recipe"


def _logger(log_callback: Callable[[str], None] | None) -> Callable[[str], None]:
    """A ``_log(msg)`` that both prints and feeds the server log stream."""

    def _log(msg: str) -> None:
        print(msg)
        if log_callback:
            log_callback(msg + "\n")

    return _log


def _push_recipe(ctx: BuildContext, job: str, recipe_remote: str) -> None:
    """Stage the recipe dir and its ``_common`` sibling onto the Haiku host."""
    _push_tree(ctx.recipe.recipe_dir, recipe_remote, what="pushing the recipe dir")
    common_src = ctx.recipe.recipe_dir.parent / "_common"
    if common_src.is_dir():
        # build.sh resolves env-haiku.sh as "$0/../../_common", so _common has
        # to land as a SIBLING of the recipe dir, not inside it.
        _push_tree(common_src, f"{job}/recipe/_common", what="pushing recipes/_common")


def _push_prefix(
    local_prefix: Path,
    remote_dir: str,
    *,
    key: str,
    stage_root: Path,
    log: Callable[[str], None],
) -> None:
    """Stage a local prefix remotely, with its .pc/.cmake paths rewritten."""
    if not local_prefix.is_dir():
        return  # nothing built yet — the remote dir stays empty
    _push_tree(local_prefix, remote_dir, what=f"pushing the {key} prefix")
    # Dependency .pc/.cmake files still name the LOCAL prefix; overlay the
    # handful that do with copies naming the remote one.
    stage = stage_root / key
    n = _stage_prefix_rewrites(local_prefix, str(local_prefix.resolve()), remote_dir, stage)
    if n:
        _push_tree(stage, remote_dir, what=f"pushing rewritten {key} metadata")
        log(f"cvcpkg-haikuhost: rewrote {n} {key} metadata file(s) to remote paths")


def _job_env(ctx: BuildContext, matrix: MatrixEntry, paths: dict[str, str]) -> dict[str, str]:
    """Environment for the remote build script (remote path forms).

    Mirrors what ``_build_env`` gives a local build, with the directory
    variables pointing at the Haiku-side staging paths.  PATH and the
    library search path are assembled by the runner, where the remote's
    own values are known.
    """
    build_type = "Release" if ctx.config == "release" else "Debug"
    env: dict[str, str] = {
        "CVC_PREFIX": paths["install"],
        "CVC_SOURCE_DIR": paths["source"],
        "CVC_BUILD_DIR": paths["build"],
        "CVC_INSTALL_DIR": paths["install"],
        "CVC_DEPS_PREFIX": paths["deps"],
        "CVC_RECIPE_DIR": paths["recipe"],
        # The build closure (host tools, staged source packages) lives in a
        # separate prefix that must also be visible remotely.  Falls back to
        # the deps prefix when the build prefix is not separated.
        "CVC_BUILD_PREFIX": paths.get("build_prefix") or paths["deps"],
        "CVC_PLATFORM": "haiku",
        "CVC_CONFIG": ctx.config,
        "CVC_BUILD_TYPE": build_type,
        "CVC_LINK": ctx.link,
        "CVC_COMPONENT": ctx.recipe.name,
        "CVC_VERSION": ctx.recipe.upstream_version,
        "CMAKE_BUILD_TYPE": build_type,
        "BUILD_SHARED_LIBS": "ON" if ctx.link == "shared" else "OFF",
        # Marker so recipes/tests can detect host-delegated builds.
        "CVC_HAIKUHOST": "1",
    }
    # Per-interpreter python recipes: _common/python-wheel.sh reads these to
    # pick the interpreter in the prefix to install into and test under, so
    # dropping them here silently built every python column against whatever
    # python3 the Haiku box happens to have.
    if ctx.recipe.python is not None:
        env["CVC_PYTHON_ABI"] = ctx.recipe.python.abi
        env["CVC_PYTHON_INTERPRETER"] = ctx.recipe.python.interpreter
        if ctx.recipe.python.manylinux_min:
            env["CVC_PYTHON_MANYLINUX_MIN"] = ctx.recipe.python.manylinux_min
        if ctx.recipe.python.free_threaded:
            # Belt and braces, as in _build_env: a free-threaded child must not
            # silently re-enable the GIL because some extension asked for it.
            env["PYTHON_GIL"] = "0"
    if matrix.host_platform:
        env["CVC_HOST_PLATFORM"] = matrix.host_platform
    # Cross-toolchain templates (CVC_EMSDK_DIR and friends) name the LOCAL
    # prefix; point them at the staged remote one, exactly as _build_env points
    # them at the local build prefix — and as _test_env does for a test job.
    # Dropping them here left a recipe that consumes a toolchain from the build
    # closure resolving it against a path that does not exist on the Haiku side.
    for var, tpl in (ctx.cross_toolchain_env or {}).items():
        env[var] = tpl.replace("${PREFIX}", env["CVC_BUILD_PREFIX"])
    jobs = os.environ.get("CVCPKG_HAIKU_JOBS", "").strip()
    if jobs:
        env["CVC_JOBS"] = jobs
    env.update(matrix.env)
    return env


def _test_env(ctx: BuildContext, paths: dict[str, str]) -> dict[str, str]:
    """Environment for the remote test script (remote path forms).

    Mirrors what ``builder.run_test`` gives a local test run.  ``CVC_JOBS``
    and the build-tree variables are deliberately absent: the build dir does
    not travel with a test job, only the install tree it produced.
    """
    env: dict[str, str] = {
        "CVC_PREFIX": paths["install"],
        "CVC_INSTALL_DIR": paths["install"],
        "CVC_DEPS_PREFIX": paths["deps"],
        # No separate build closure for a test run; keep the variable defined so
        # the runner's PATH/library-path layering has a real root to expand.
        "CVC_BUILD_PREFIX": paths["deps"],
        "CVC_RECIPE_DIR": paths["recipe"],
        "CVC_PLATFORM": "haiku",
        "CVC_COMPONENT": ctx.recipe.name,
        "CVC_VERSION": ctx.recipe.upstream_version,
        "CVC_HAIKUHOST": "1",
    }
    # Cross-toolchain templates name the LOCAL prefix; point them at the staged
    # remote one, as run_test does with ctx.prefix locally.
    for var, tpl in (ctx.cross_toolchain_env or {}).items():
        env[var] = tpl.replace("${PREFIX}", paths["deps"])
    return env


def _runner_script(
    recipe: str,
    script_name: str,
    env: dict[str, str],
    *,
    kind: str = "build",
    workdir_var: str = "CVC_BUILD_DIR",
    mkdir_vars: tuple[str, ...] = ("CVC_BUILD_DIR", "CVC_INSTALL_DIR"),
) -> str:
    """Generate the per-job ``run-job.sh`` executed on the Haiku host.

    Drives a build script (*kind* ``build``, cwd ``$CVC_BUILD_DIR``) or a
    test script (*kind* ``test``, cwd ``$CVC_INSTALL_DIR``) — the two
    differ only in which staged directory is the cwd and which ones are
    created first, so they share one generator.

    Plain ``/bin/sh`` so it runs before anything is known about the box,
    but the recipe script itself is handed to ``bash``: recipe scripts and
    ``_common/env-haiku.sh`` are bash (``[[ ]]``, ``BASH_SOURCE``, arrays)
    and Haiku's /bin/sh is bash only by convention, not by contract.

    Everything recipe-supplied that lands in this script — name, script
    file name, env values — is sanitized or ``shlex.quote``d: the file is
    generated text that a remote shell then executes, which is the one
    place a stray quote in a recipe would become remote code execution.
    """
    lines = [
        "#!/bin/sh",
        f"# cvcpkg haikuhost {kind} runner for {_safe_name(recipe)} — "
        "generated per job, do not edit.",
        "set -e",
        "",
    ]
    for name, value in env.items():
        if not _ENV_NAME_RE.match(name):
            # A matrix entry can carry anything; refuse to emit something
            # that is not a variable assignment rather than injecting it.
            continue
        lines.append(f"export {name}={shlex.quote(str(value))}")
    lib_var = lib_path_var("haiku")
    mkdirs = " ".join(f'"${v}"' for v in mkdir_vars)
    lines += [
        "",
        f"mkdir -p {mkdirs}",
        "",
        "# Same search-path layering as builder._build_env: the build closure's",
        "# tools win over the deps prefix, which wins over whatever the box has.",
        'PATH="$CVC_BUILD_PREFIX/bin:$CVC_DEPS_PREFIX/bin:$CVC_INSTALL_DIR/bin:$PATH"',
        "export PATH",
        "",
        f"# Haiku's runtime_loader takes {lib_var} as the WHOLE search path — it",
        "# appends no implicit system default, so this must not be built by",
        f"# appending to an inherited {lib_var}: an `ssh host 'sh run-job.sh'`",
        "# exec channel gets none of the login environment (SetupEnvironment is",
        f"# sourced for login/Terminal sessions), so {lib_var} is empty here and",
        "# the layered value would end up naming ONLY our prefixes — every",
        "# system library then unresolvable.  Spell the defaults out instead,",
        "# and still honour an inherited value if the far end does set one.",
        f'if [ -n "${{{lib_var}:-}}" ]; then',
        f'    _cvc_sys_libs="${lib_var}"',
        "else",
        # Double quotes, not shlex.quote: $HOME has to expand on the FAR end
        # (see HAIKU_DEFAULT_LIBRARY_PATH — a module constant, not recipe
        # input, and free of every other shell metacharacter).
        f'    _cvc_sys_libs="{HAIKU_DEFAULT_LIBRARY_PATH}"',
        "fi",
        f'{lib_var}="$CVC_BUILD_PREFIX/lib:$CVC_DEPS_PREFIX/lib:$CVC_INSTALL_DIR/lib'
        ':$_cvc_sys_libs"',
        f"export {lib_var}",
        "",
        "if ! command -v bash >/dev/null 2>&1; then",
        '    echo "haikuhost: bash not found on the Haiku host (recipe scripts are bash)" >&2',
        "    exit 3",
        "fi",
        "",
        # Quoted-concatenation, not interpolation: the script name comes from
        # the recipe's matrix entry and must not be able to close the string.
        f'_script="$CVC_RECIPE_DIR/"{shlex.quote(script_name)}',
        'if [ ! -f "$_script" ]; then',
        f'    echo "haikuhost: {kind} script not found: $_script" >&2',
        "    exit 3",
        "fi",
        "",
        f'echo "== haikuhost: {_safe_name(recipe)} ({kind}) on '
        '$(hostname 2>/dev/null || echo haiku) =="',
        f"# Recipe scripts expect ${workdir_var} as cwd, exactly like the local path.",
        f'cd "${workdir_var}"',
        'exec bash "$_script"',
        "",
    ]
    return "\n".join(lines)


def run_haiku_build(
    ctx: BuildContext,
    matrix: MatrixEntry,
    script: Path,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Execute *script* (the recipe's Haiku build script) on the Haiku host.

    Stages the source tree, the recipe (with its ``_common`` sibling) and
    the dependency prefixes onto the Haiku box, runs the generated runner
    there while streaming its output, and copies the install tree back so
    the normal pack/publish path runs unchanged on this side.  The remote
    work dir is removed on success and KEPT on failure (the error names
    it) so the wreckage can be inspected.  Raises :class:`HaikuhostError`.
    """
    _log = _logger(log_callback)
    with _notices_to(_log):
        _run_haiku_build(ctx, matrix, script, log_callback, _log)


def _run_haiku_build(
    ctx: BuildContext,
    matrix: MatrixEntry,
    script: Path,
    log_callback: Callable[[str], None] | None,
    _log: Callable[[str], None],
) -> None:
    if script.suffix != ".sh":
        raise HaikuhostError(
            f"haiku delegation only supports .sh build scripts, got {script.name}. "
            "Add a 'platform: haiku' matrix entry with a shell script."
        )

    ensure_delegatable(ctx.platform, ctx.host_platform)
    target = ssh_target()
    if not target:  # defensive: ensure_delegatable only speaks for haiku targets
        raise HaikuhostError(
            "no Haiku host configured: set CVCPKG_HAIKU_SSH=user@host on the "
            "builder (see cvcpkg.haikuhost for the full knob list)"
        )
    # Before minting a new job dir, trim the tail of kept (failed) ones.
    _reap_old_jobs(_log)

    job_name = f"{_safe_name(ctx.recipe.name)}-{uuid.uuid4().hex[:12]}"
    job = f"{remote_workdir()}/jobs/{job_name}"
    recipe_leaf = _safe_name(ctx.recipe.recipe_dir.name)
    paths = {
        "source": f"{job}/source",
        "build": f"{job}/build",
        "install": f"{job}/install",
        "deps": f"{job}/deps",
        "recipe": f"{job}/recipe/{recipe_leaf}",
    }
    # The build closure (host tools, staged source packages) is a separate
    # prefix only when the caller separated it; otherwise it IS the deps
    # prefix and staging it twice would just double the transfer.
    separated = ctx.build_prefix is not None and ctx.build_prefix != ctx.prefix
    if separated:
        paths["build_prefix"] = f"{job}/build-prefix"

    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    ctx.install_dir.mkdir(parents=True, exist_ok=True)

    _log(
        f"cvcpkg-haikuhost: building {ctx.recipe.name} {ctx.recipe.full_version} "
        f"on {target} (job={job}, script={matrix.script})"
    )

    mkdirs = " ".join(shlex.quote(p) for p in (*paths.values(), f"{job}/recipe"))
    _run_ssh(f"mkdir -p {mkdirs}", what="creating the remote work dir")
    # Claim the dir before anything is staged into it: from here on another
    # builder's reaper must leave it alone (see _reap_old_jobs).
    _claim_job(job, job_name, _log)

    ok = False
    stage_root = ctx.work_dir / f".haikuhost-stage-{job_name}"
    try:
        mode = _push_tree(ctx.source_dir, paths["source"], what="pushing the source tree")
        _log(f"cvcpkg-haikuhost: staging the job on {target} via {mode}")
        # The push left BFS's timestamps on the tree, not the builder's.
        _restamp_source(ctx.source_dir, paths["source"], _log)

        _push_recipe(ctx, job, paths["recipe"])

        prefixes = [("deps", Path(ctx.prefix))]
        if separated and ctx.build_prefix is not None:
            prefixes.append(("build_prefix", Path(ctx.build_prefix)))
        for key, local_prefix in prefixes:
            _push_prefix(local_prefix, paths[key], key=key, stage_root=stage_root, log=_log)

        runner = f"{job}/run-job.sh"
        _put_text(
            _runner_script(ctx.recipe.name, matrix.script, _job_env(ctx, matrix, paths)),
            runner,
            what="writing the remote job runner",
        )

        rc = _stream_ssh(f"sh {shlex.quote(runner)}", log_callback)
        if rc != 0:
            raise HaikuhostError(
                f"haiku build of {ctx.recipe.name} failed with exit code {rc}; "
                f"remote work dir kept for inspection: {target}:{job}"
            )

        # Clear the destination first: the pull MERGES the remote tree into
        # whatever is already here (rsync without --delete, tar -x over an
        # existing dir), so a file this recipe no longer installs — a renamed
        # library, a dropped header, an artefact of an earlier incremental run
        # into the same work dir — would survive the sync and be packed into
        # the bundle as if the Haiku build had produced it.
        #
        # Guarded on the install dir being this recipe's OWN isolated dir,
        # which is what every path into run_build gives us (build_recipe and
        # build_all both pass <work>/install).  If some future caller ever
        # points it at the shared prefix, skipping the clear is a stale file;
        # not skipping it would delete every dependency built so far.
        if ctx.install_dir.resolve() not in (Path(ctx.prefix).resolve(), ctx.work_dir.resolve()):
            shutil.rmtree(ctx.install_dir, ignore_errors=True)
        ctx.install_dir.mkdir(parents=True, exist_ok=True)
        mode = _pull_tree(paths["install"], ctx.install_dir, what="fetching the install tree")
        file_count = sum(1 for p in ctx.install_dir.rglob("*") if p.is_file())
        _log(f"cvcpkg-haikuhost: synced install tree back via {mode} ({file_count} files)")
        if file_count == 0:
            raise HaikuhostError(
                f"haiku build of {ctx.recipe.name} installed no files; "
                f"remote work dir kept for inspection: {target}:{job}"
            )
        ok = True
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        _finish_job(job, job_name, ok=ok, keep=ctx.keep_build_dir, kind="work", log=_log)


def run_haiku_test(
    ctx: BuildContext,
    test_path: Path,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Execute the recipe's test script on the Haiku host.

    A delegated build leaves a tree of Haiku ELF on the Linux builder;
    running the test script *here* would exercise nothing — every binary
    it tried to run would be an exec format error at best, and at worst
    the script would happily test the builder's own Linux tools and
    report success, which is how an untested bundle gets published.  The
    Haiku host is still reachable at this point in the build, so the test
    goes where the binaries can actually run.

    Stages the install tree (the artifact under test), the dependency
    prefix and the recipe dir, then runs the script with the install dir
    as cwd — the same contract ``builder.run_test`` gives a local run.
    Nothing is copied back: a test produces no deliverable.  Raises
    :class:`HaikuhostError` when the remote test fails.
    """
    _log = _logger(log_callback)
    with _notices_to(_log):
        _run_haiku_test(ctx, test_path, log_callback, _log)


def _run_haiku_test(
    ctx: BuildContext,
    test_path: Path,
    log_callback: Callable[[str], None] | None,
    _log: Callable[[str], None],
) -> None:
    if test_path.suffix != ".sh":
        raise HaikuhostError(
            f"haiku delegation only supports .sh test scripts, got {test_path.name}"
        )
    # Only the recipe dir travels, so the script has to live inside it — and it
    # is named RELATIVE to that dir, because a recipe may keep tests in a
    # subdirectory and only the leaf would then resolve to nothing remotely.
    try:
        script_rel = test_path.resolve().relative_to(ctx.recipe.recipe_dir.resolve()).as_posix()
    except ValueError:
        raise HaikuhostError(
            f"haiku test script {test_path} is outside the recipe dir "
            f"{ctx.recipe.recipe_dir}, so it cannot be staged on the Haiku host"
        ) from None

    ensure_delegatable(ctx.platform, ctx.host_platform)
    target = ssh_target()
    if not target:  # defensive: ensure_delegatable only speaks for haiku targets
        raise HaikuhostError(
            "no Haiku host configured: set CVCPKG_HAIKU_SSH=user@host on the "
            "builder (see cvcpkg.haikuhost for the full knob list)"
        )

    job_name = f"{_safe_name(ctx.recipe.name)}-test-{uuid.uuid4().hex[:12]}"
    job = f"{remote_workdir()}/jobs/{job_name}"
    recipe_leaf = _safe_name(ctx.recipe.recipe_dir.name)
    paths = {
        "install": f"{job}/install",
        "deps": f"{job}/deps",
        "recipe": f"{job}/recipe/{recipe_leaf}",
    }

    _log(
        f"cvcpkg-haikuhost: testing {ctx.recipe.name} {ctx.recipe.full_version} "
        f"on {target} (job={job}, script={test_path.name})"
    )

    mkdirs = " ".join(shlex.quote(p) for p in (*paths.values(), f"{job}/recipe"))
    _run_ssh(f"mkdir -p {mkdirs}", what="creating the remote test dir")
    _claim_job(job, job_name, _log)

    ok = False
    stage_root = ctx.work_dir / f".haikuhost-stage-{job_name}"
    try:
        _push_prefix(
            ctx.install_dir, paths["install"], key="install", stage_root=stage_root, log=_log
        )
        _push_prefix(Path(ctx.prefix), paths["deps"], key="deps", stage_root=stage_root, log=_log)
        _push_recipe(ctx, job, paths["recipe"])

        runner = f"{job}/run-job.sh"
        _put_text(
            _runner_script(
                ctx.recipe.name,
                script_rel,
                _test_env(ctx, paths),
                kind="test",
                workdir_var="CVC_INSTALL_DIR",
                mkdir_vars=("CVC_INSTALL_DIR",),
            ),
            runner,
            what="writing the remote test runner",
        )

        rc = _stream_ssh(f"sh {shlex.quote(runner)}", log_callback)
        if rc != 0:
            raise HaikuhostError(
                f"haiku test of {ctx.recipe.name} failed with exit code {rc}; "
                f"remote work dir kept for inspection: {target}:{job}"
            )
        ok = True
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        _finish_job(job, job_name, ok=ok, keep=ctx.keep_build_dir, kind="test", log=_log)
