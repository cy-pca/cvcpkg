# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Delegate Haiku-target builds to a Haiku host over SSH.

The Windows analogue of this module is :mod:`cvcpkg.winhost`, and the
split is the same: source fetch, patching, packaging, and publishing stay
on the Linux builder, only the compile runs elsewhere.  What differs is
*why* the compile has to move.  For Windows it is the toolchain; for
Haiku it is cvcpkg itself — HaikuPorts has no pip, no greenlet/httpx, and
only cryptography 3.4.8 against cvcpkg's ``>=41.0`` floor, so a native
``cvcpkg`` install on Haiku is currently impossible.  A Haiku box can
therefore never be a self-hosting builder; it can only be a build
*target* driven from a machine that does run cvcpkg.

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

Two Haiku facts shape the details.  Its run-time linker reads
``LIBRARY_PATH``, not ``LD_LIBRARY_PATH`` (hence
:func:`cvcpkg.platform.lib_path_var`), and rsync is not part of the base
system, so file exchange falls back to tar-over-ssh whenever either end
lacks rsync.

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
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

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


def _ssh_options() -> list[str]:
    """``ssh`` plus the connection options every invocation shares.

    ``BatchMode=yes`` is the important one: a builder is non-interactive,
    so a missing key or an unknown host key must fail the job instead of
    blocking forever on a prompt.  Host-key checking is deliberately left
    at the OpenSSH default — the Haiku box has to be in ``known_hosts``.
    """
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={_CONNECT_TIMEOUT}"]
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


def _run_ssh(remote_command: str, *, what: str, timeout: float = 300) -> str:
    """Run *remote_command* on the Haiku host, return stripped stdout."""
    try:
        r = subprocess.run(
            _ssh_cmd(remote_command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HaikuhostError(f"{what} failed on {ssh_target()}: {exc}") from exc
    if r.returncode != 0:
        raise HaikuhostError(
            f"{what} failed on {ssh_target()} (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace').strip()[:500]}"
        )
    return r.stdout.decode(errors="replace").strip()


def haikuhost_available() -> bool:
    """Best-effort check that the configured Haiku host answers."""
    if not ssh_target():
        return False
    try:
        r = subprocess.run(
            _ssh_cmd("true"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired, HaikuhostError):
        return False
    return r.returncode == 0


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
    """Run a remote command, teeing its output to stdout and the log."""
    proc = subprocess.Popen(
        _ssh_cmd(remote_command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _flush_bytes = 8192
    buf: list[str] = []
    buf_size = 0
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace")
        sys.stdout.write(line)
        if log_callback:
            buf.append(line)
            buf_size += len(line)
            if buf_size >= _flush_bytes:
                log_callback("".join(buf))
                buf.clear()
                buf_size = 0
    if log_callback and buf:
        log_callback("".join(buf))
    return proc.wait()


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


def _run_pipeline(producer: list[str], consumer: list[str], *, what: str) -> None:
    """Run ``producer | consumer``, raising on either side's failure."""
    try:
        src = subprocess.Popen(producer, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    except OSError as exc:
        raise HaikuhostError(f"{what} failed to start {producer[0]}: {exc}") from exc
    assert src.stdout is not None
    try:
        dst = subprocess.Popen(consumer, stdin=src.stdout, stderr=subprocess.PIPE)
    except OSError as exc:
        src.kill()
        raise HaikuhostError(f"{what} failed to start {consumer[0]}: {exc}") from exc
    # Close our copy so the producer sees EPIPE if the consumer dies.
    src.stdout.close()
    _, dst_err = dst.communicate()
    src_rc = src.wait()
    if dst.returncode != 0:
        raise HaikuhostError(
            f"{what} failed (rc={dst.returncode}): "
            f"{dst_err.decode(errors='replace').strip()[:500]}"
        )
    if src_rc != 0:
        raise HaikuhostError(f"{what} failed: {producer[0]} exited with code {src_rc}")


def _push_tree(local: Path, remote_dir: str, *, what: str) -> str:
    """Copy the CONTENTS of *local* into the remote *remote_dir*.

    Returns the transfer mode actually used, for the log.
    """
    mode = _transfer_mode()
    if mode == "rsync":
        argv = [*_rsync_argv(), f"{str(local).rstrip('/')}/", f"{ssh_target()}:{remote_dir}/"]
        r = subprocess.run(argv, capture_output=True)
        if r.returncode != 0:
            raise HaikuhostError(
                f"{what} failed (rsync rc={r.returncode}): "
                f"{r.stderr.decode(errors='replace').strip()[:500]}"
            )
        return mode
    _run_pipeline(
        ["tar", "-C", str(local), "-cf", "-", "."],
        _ssh_cmd(f"mkdir -p {shlex.quote(remote_dir)} && tar -C {shlex.quote(remote_dir)} -xf -"),
        what=what,
    )
    return mode


def _pull_tree(remote_dir: str, local: Path, *, what: str) -> str:
    """Copy the CONTENTS of the remote *remote_dir* into *local*."""
    local.mkdir(parents=True, exist_ok=True)
    mode = _transfer_mode()
    if mode == "rsync":
        argv = [*_rsync_argv(), f"{ssh_target()}:{remote_dir}/", f"{str(local).rstrip('/')}/"]
        r = subprocess.run(argv, capture_output=True)
        if r.returncode != 0:
            raise HaikuhostError(
                f"{what} failed (rsync rc={r.returncode}): "
                f"{r.stderr.decode(errors='replace').strip()[:500]}"
            )
        return mode
    _run_pipeline(
        _ssh_cmd(f"tar -C {shlex.quote(remote_dir)} -cf - ."),
        ["tar", "-C", str(local), "-xf", "-"],
        what=what,
    )
    return mode


def _put_text(text: str, remote_path: str, *, what: str) -> None:
    """Write *text* to *remote_path* on the Haiku host."""
    try:
        r = subprocess.run(
            _ssh_cmd(f"cat > {shlex.quote(remote_path)}"),
            input=text.encode(),
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HaikuhostError(f"{what} failed on {ssh_target()}: {exc}") from exc
    if r.returncode != 0:
        raise HaikuhostError(
            f"{what} failed on {ssh_target()} (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace').strip()[:500]}"
        )


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
    if script.suffix != ".sh":
        raise HaikuhostError(
            f"haiku delegation only supports .sh build scripts, got {script.name}. "
            "Add a 'platform: haiku' matrix entry with a shell script."
        )

    _log = _logger(log_callback)
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

    _log = _logger(log_callback)
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
