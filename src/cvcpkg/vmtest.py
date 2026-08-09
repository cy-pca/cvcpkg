# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Ephemeral-VM recipe tests — boot the artifact you just built, then assert.

WHAT ALREADY EXISTED, so this reads honestly: cvcpkg has had a test hook since
long before image packages.  A recipe writes::

    test:
      script: test.sh

and :func:`cvcpkg.builder.run_test` runs that script *on the builder*, after
``run_build`` and before packing, with ``CVC_PREFIX``/``CVC_INSTALL_DIR`` in the
environment.  That hook is exactly right for a library: compile a two-line
program against the headers you just staged and run it.

It is worth nothing for an image package.  ``share/haiku-image/disk.qcow2`` is
an opaque 50 GiB blob holding a foreign operating system; a bash script on the
Linux builder can check that the file exists and that ``qemu-img info`` parses
it, which is precisely the "trust me, it boots" that shipped a Haiku image
claiming ``disk_bus: virtio-blk`` for a revision — a value that general-
protection-faults the guest before userland, inferred rather than booted.  The
only assertion that would have caught it is *booting the thing*.

So this module adds a SECOND hook alongside the first, not a replacement::

    test:
      script: test.sh        # unchanged: runs on the builder
      vm:                    # new: runs INSIDE a throwaway VM
        requires_capabilities: [incus]
        connect: ssh
        script: vm-test.sh

Design rules, each of which is a requirement rather than a preference:

* **Capability-gated, never a hard failure.**  A builder with no hypervisor
  SKIPS.  The gate is :func:`cvcpkg.platform.host_capabilities` — the same
  probes (``incus``/``lxd``/``lxc``) the fleet already routes on.  A skip is
  reported loudly and distinguishably; it is not a pass, but it is not a red
  build on a machine that was never going to be able to run it.
* **The VM AND ITS IMAGE are ALWAYS destroyed.**  Assertion failure, timeout,
  an exception from the hypervisor CLI, ``SystemExit``, Ctrl-C and SIGTERM all
  land in the same ``finally``.  Cleanup is idempotent, never raises, has its
  own budget so a wedged daemon cannot make cleanup itself hang, and runs with
  SIGINT/SIGTERM DEFERRED so a second Ctrl-C cannot kill us between deleting
  the instance and deleting the image.  Both the instance and the imported
  image carry a :data:`INSTANCE_PREFIX` name so a run that died by SIGKILL
  (which no handler can catch) is reaped by the *next* run.  The image half
  matters as much as the instance half: an import copies a multi-gigabyte
  qcow2 into the daemon's store, so leaking one per run fills a builder's
  disk — the exact failure that motivated disk-aware scheduling.
* **A hard wall-clock deadline.**  One :class:`_Deadline` covers the whole
  phase and every subprocess call draws its timeout from it, so a hung boot
  costs a bounded number of minutes on the builder instead of the job slot.
* **Capabilities gate the TEST, not the install.**  ``test.vm`` carries its own
  ``requires_capabilities``; it is deliberately not the recipe-level key.  An
  image must stay installable on a laptop with no hypervisor — the whole point
  of the package is to *carry* the image to a machine that has one.

Only the incus/LXD REST CLIs can boot a virtual machine.  Classic LXC
(``lxc-create``/``lxc-start``, capability ``lxc``) is containers-only and
daemonless, so it can never satisfy a VM test; :func:`select_driver` says so in
those words rather than failing later with a confusing CLI error.

Nothing here has been run against a live Incus daemon in anger — the unit tests
drive a recorded fake.  The command surface is the documented split-VM-image
form (``incus image import metadata.tar.xz disk.qcow2``), which is exactly the
pair of files the image layout ships.
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cvcpkg import images as _images
from cvcpkg.errors import CvcpkgError

# ── Constants ───────────────────────────────────────────────────

#: Every instance and image alias this module creates starts with this.  It is
#: the ONLY handle :func:`reap_stale` has, and it is what makes the reaper
#: incapable of touching a pre-existing instance or image, so it must never be
#: shortened to something a human might plausibly name a real one.
INSTANCE_PREFIX = "cvcpkg-vmtest-"

#: Hard wall-clock ceiling for the whole VM phase when a recipe says nothing.
DEFAULT_TIMEOUT_SECONDS = 1800

#: Ceiling for "the guest came up and is reachable" specifically.  Separate
#: from the total because a boot that never completes is the failure mode we
#: are actually hunting, and it deserves its own, shorter, budget.
DEFAULT_BOOT_TIMEOUT_SECONDS = 600

#: Budget for a single hypervisor CLI call (import/init/start/delete).  Image
#: import copies a multi-gigabyte qcow2 into the daemon's store, so this is
#: generous; it is still bounded, and still clamped by the total deadline.
DEFAULT_STEP_TIMEOUT_SECONDS = 900

#: Budget for cleanup, drawn from a fresh clock rather than the deadline: by
#: the time we are tearing down the deadline has usually already expired, and
#: "we ran out of time" must not mean "we leak a VM".
CLEANUP_TIMEOUT_SECONDS = 120

#: Seconds between boot-readiness polls.
POLL_INTERVAL_SECONDS = 5.0

#: Hypervisors that can boot a VM, in default preference order.  Capability
#: names, matching :data:`cvcpkg.platform._CAPABILITY_PROBES`.
VM_CAPABLE = ("incus", "lxd")

#: The CLI binary each of those capabilities is driven through.  LXD's client
#: is literally named ``lxc`` (which is why the probe cross-checks the reported
#: server name); classic LXC ships no such binary and is absent from this map.
_BINARY = {"incus": "incus", "lxd": "lxc"}

#: Guest path the test script is delivered to in ``agent`` mode.
GUEST_SCRIPT_PATH = "/tmp/cvcpkg-vm-test.sh"

#: The two listings.  Instances and images are SEPARATE namespaces in
#: incus/LXD — ``incus list`` will never mention an image — so anything that
#: wants to know what this module left behind has to ask twice.  ``-c n`` is
#: the instance-name column; ``-c L`` is the all-aliases column.
_INSTANCE_LS = ("list", "--format", "csv", "-c", "n")
_IMAGE_LS = ("image", "list", "--format", "csv", "-c", "L")

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"


class VmTestError(CvcpkgError):
    """The VM test could not be set up or run (as opposed to failing)."""


class VmTestTimeoutError(VmTestError):
    """The wall-clock deadline for the VM phase expired."""


class _SignalReceivedError(VmTestError):
    """SIGINT/SIGTERM arrived while a VM was alive.

    Raised from the signal handler purely so the ``finally`` that destroys the
    instance runs.  Never escapes :func:`run_vm_test`.
    """


# ── The recipe-declared spec ────────────────────────────────────


@dataclass(frozen=True)
class VmTestSpec:
    """A recipe's ``test.vm`` block, parsed and defaulted.

    ``image`` is the package whose ``share/<name>/`` tree gets booted.  The
    default, ``"self"``, means "the image this recipe just built", which is the
    case that turns an image recipe from trust-me into self-testing.
    """

    requires_capabilities: tuple[str, ...] = ()
    hypervisors: tuple[str, ...] = VM_CAPABLE
    image: str = "self"
    script: str | None = None
    connect: str = "ssh"
    ssh_user: str | None = None
    ssh_key_env: str | None = None
    ssh_key_file: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    boot_timeout_seconds: int = DEFAULT_BOOT_TIMEOUT_SECONDS
    step_timeout_seconds: int = DEFAULT_STEP_TIMEOUT_SECONDS

    @classmethod
    def from_dict(cls, block: dict[str, Any] | None) -> VmTestSpec | None:
        """Parse a ``test.vm`` mapping.  ``None``/empty means "no VM test"."""
        if not block:
            return None
        if not isinstance(block, dict):
            raise VmTestError("test.vm must be a mapping")

        connect = str(block.get("connect", "ssh"))
        if connect not in ("ssh", "agent"):
            raise VmTestError(f"test.vm.connect must be 'ssh' or 'agent', not {connect!r}")

        hypervisors = tuple(str(h) for h in block.get("hypervisors", VM_CAPABLE))
        unknown = [h for h in hypervisors if h not in _BINARY]
        if unknown:
            raise VmTestError(
                f"test.vm.hypervisors: {', '.join(unknown)} cannot boot a virtual machine "
                f"(supported: {', '.join(VM_CAPABLE)})"
            )
        if not hypervisors:
            raise VmTestError("test.vm.hypervisors must not be empty")

        ssh = block.get("ssh") or {}
        if not isinstance(ssh, dict):
            raise VmTestError("test.vm.ssh must be a mapping")

        image = str(block.get("image", "self"))
        if image != "self" and not re.fullmatch(r"[a-z][a-z0-9-]*", image):
            raise VmTestError(f"test.vm.image must be 'self' or a package name, not {image!r}")

        return cls(
            requires_capabilities=tuple(str(c) for c in block.get("requires_capabilities", ())),
            hypervisors=hypervisors,
            image=image,
            script=str(block["script"]) if block.get("script") else None,
            connect=connect,
            ssh_user=str(ssh["user"]) if ssh.get("user") else None,
            ssh_key_env=str(ssh["key_env"]) if ssh.get("key_env") else None,
            ssh_key_file=str(ssh["key_file"]) if ssh.get("key_file") else None,
            timeout_seconds=int(block.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            boot_timeout_seconds=int(
                block.get("boot_timeout_seconds", DEFAULT_BOOT_TIMEOUT_SECONDS)
            ),
            step_timeout_seconds=int(
                block.get("step_timeout_seconds", DEFAULT_STEP_TIMEOUT_SECONDS)
            ),
        )


@dataclass
class VmTestResult:
    """Outcome of a VM test.

    ``skipped`` is a first-class outcome, not a flavour of pass: the caller
    prints it, the build goes green, and ``reason`` says exactly which gate
    stopped it so nobody has to guess whether the test ran.
    """

    status: str
    reason: str = ""
    instance: str = ""
    hypervisor: str = ""
    output: str = ""
    destroyed: bool = False

    @property
    def ok(self) -> bool:
        """True unless the guest actually told us something is wrong."""
        return self.status in (PASSED, SKIPPED)

    @property
    def leaked(self) -> bool:
        """True when a VM may still exist and needs a human.

        A skip creates nothing, so ``destroyed`` is meaningless there — without
        this distinction every skip on a hypervisor-less builder would print a
        scary "may not have been destroyed" warning about a VM that was never
        created, and the warning would stop meaning anything.
        """
        return self.status != SKIPPED and not self.destroyed


# ── Subprocess plumbing (the only thing the tests replace) ──────


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def summary(self) -> str:
        text = (self.stderr or self.stdout).strip()
        return text.splitlines()[-1] if text else f"exit {self.returncode}"


def subprocess_runner(
    argv: Sequence[str],
    *,
    timeout: float,
    stdin_path: Path | None = None,
) -> CommandResult:
    """Run *argv*, capturing output.  Never raises for a non-zero exit.

    A timeout, a missing binary and an EPERM all come back as a synthetic
    non-zero :class:`CommandResult` for the same reason the capability probes
    swallow them: the caller's job is to decide what a failure *means*, and it
    can only do that if every failure arrives in one shape.  stdin is a file or
    ``/dev/null`` — a test step can never sit waiting on a prompt.
    """
    argv = list(argv)
    try:
        with ExitStack() as stack:
            stdin: Any = subprocess.DEVNULL
            if stdin_path is not None:
                stdin = stack.enter_context(open(stdin_path, "rb"))
            proc = subprocess.run(
                argv,
                stdin=stdin,
                capture_output=True,
                timeout=max(timeout, 1.0),
                check=False,
            )
        return CommandResult(
            argv=tuple(argv),
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(tuple(argv), 124, "", f"timed out after {timeout:.0f}s")
    except OSError as exc:
        return CommandResult(tuple(argv), 127, "", str(exc))


#: What :func:`run_vm_test` calls to execute a command.  Swapped wholesale in
#: tests — CI has no incus, and a unit test that needs one is not a unit test.
Runner = Callable[..., CommandResult]


# ── Deadline ────────────────────────────────────────────────────


class _Deadline:
    """A monotonic wall-clock budget shared by every step of one VM test.

    Every subprocess call draws its timeout from :meth:`budget`, so the sum of
    the steps can never exceed the total no matter how many polls a slow boot
    costs.  The clock is injectable so tests can expire it instantly instead of
    sleeping.
    """

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + max(seconds, 0.0)

    def remaining(self) -> float:
        return self._end - self._clock()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def budget(self, step: float, *, what: str) -> float:
        """Timeout for one call: ``min(step, remaining)``, or raise."""
        left = self.remaining()
        if left <= 0:
            raise VmTestTimeoutError(f"VM test deadline expired before {what}")
        return min(step, left)


# ── Hypervisor driver ───────────────────────────────────────────


@dataclass
class InstanceDriver:
    """The incus/LXD command surface, as used by this module.

    Incus is a fork of LXD and the two clients still take the same verbs, so
    one driver with a different ``binary`` covers both.  This class holds *no*
    lifecycle policy — it is a thin, mockable translation from intent to argv,
    which keeps :func:`run_vm_test`'s ordering guarantees testable without a
    daemon.
    """

    capability: str
    binary: str
    runner: Runner
    deadline: _Deadline
    step_timeout: float = DEFAULT_STEP_TIMEOUT_SECONDS

    def _run(
        self, args: Sequence[str], *, what: str, timeout: float | None = None
    ) -> CommandResult:
        budget = self.deadline.budget(timeout or self.step_timeout, what=what)
        return self.runner([self.binary, *args], timeout=budget)

    # -- image ---------------------------------------------------

    def import_image(self, metadata: Path, disk: Path, alias: str) -> CommandResult:
        """``incus image import metadata.tar.xz disk.qcow2 --alias <alias>``.

        The split-VM-image form: a metadata tarball plus a qcow2 root disk,
        which is exactly the pair ``share/<pkg>/`` ships.  Import COPIES the
        disk into the daemon's image store, so the installed master is never
        opened read-write and ``cvcpkg image verify`` still passes afterwards.
        """
        return self._run(
            ["image", "import", str(metadata), str(disk), "--alias", alias],
            what="image import",
        )

    def delete_image(self, alias: str) -> CommandResult:
        return self._run(["image", "delete", alias], what="image delete")

    # -- instance ------------------------------------------------

    def init_vm(self, alias: str, instance: str, boot: dict[str, Any]) -> CommandResult:
        """Create (but do not start) a VM sized from the descriptor's ``boot``.

        Every ``-c``/``-d`` here replaces a constant a provisioning script
        would otherwise hardcode; they come from ``image.yaml`` so the test
        boots the guest under the sizes the recipe claims are the minimum.
        """
        args = ["init", alias, instance, "--vm"]
        cpu = boot.get("cpu_min")
        if cpu:
            args += ["-c", f"limits.cpu={int(cpu)}"]
        memory = boot.get("memory_min_mib")
        if memory:
            args += ["-c", f"limits.memory={int(memory)}MiB"]
        if "secureboot" in boot:
            args += ["-c", f"security.secureboot={str(bool(boot['secureboot'])).lower()}"]
        disk_gib = boot.get("disk_min_gib")
        if disk_gib:
            args += ["-d", f"root,size={int(disk_gib)}GiB"]
        return self._run(args, what="init")

    def set_disk_bus(self, instance: str, bus: str) -> CommandResult:
        """Pin the root device's bus.

        This is the single most load-bearing call in the file.  The hypervisor
        default is virtio-scsi, which some guests have no driver for at all;
        the descriptor's ``boot.disk_bus`` is the bisected truth, and a test
        that ignored it would test a configuration nobody deploys.
        """
        return self._run(
            ["config", "device", "set", instance, "root", f"io.bus={bus}"],
            what="config device set",
        )

    def start(self, instance: str) -> CommandResult:
        return self._run(["start", instance], what="start")

    def delete_instance(self, instance: str) -> CommandResult:
        return self._run(["delete", instance, "--force"], what="delete")

    def address(self, instance: str) -> str | None:
        """First non-loopback IPv4 the daemon reports for *instance*.

        Parsed out of ``list --format csv -c 4`` rather than JSON: the CSV
        column is stable across incus/LXD versions and needs no json import in
        the hot poll loop.  ``None`` while the guest has no lease yet, which is
        the normal state for most of a boot.
        """
        result = self._run(
            ["list", instance, "--format", "csv", "-c", "4"],
            what="list",
            timeout=30,
        )
        if not result.ok:
            return None
        for token in re.split(r"[\s,\"]+", result.stdout):
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", token) and not token.startswith("127."):
                return token
        return None

    def exec(self, instance: str, argv: Sequence[str], *, timeout: float) -> CommandResult:
        return self._run(["exec", instance, "--", *argv], what="exec", timeout=timeout)

    def push(self, instance: str, source: Path, dest: str) -> CommandResult:
        return self._run(["file", "push", str(source), f"{instance}{dest}"], what="file push")

    def _list_names(self, args: Sequence[str], prefix: str) -> list[str] | None:
        """Prefixed names from a CSV listing, or ``None`` if the daemon balked.

        ``None`` and ``[]`` mean genuinely different things and the two callers
        need different ones: the reaper DELETES on the strength of this and
        must treat "cannot tell" as "nothing to do", while teardown must treat
        it as "assume the multi-gigabyte thing is still there".  Collapsing
        both into ``[]`` is how a leak hides.
        """
        result = self._run(args, what="list", timeout=30)
        if not result.ok:
            return None
        names: list[str] = []
        for line in result.stdout.splitlines():
            name = line.split(",")[0].strip().strip('"')
            if name.startswith(prefix) and name not in names:
                names.append(name)
        return names

    def list_prefixed(self, prefix: str) -> list[str]:
        """Names of existing instances starting with *prefix*.  Never raises.

        An unanswerable daemon reads as empty here on purpose: the only caller
        deletes what this names.
        """
        return self._list_names(_INSTANCE_LS, prefix) or []

    def list_prefixed_images(self, prefix: str) -> list[str]:
        """Aliases of imported IMAGES starting with *prefix*.  Never raises.

        ``-c L`` is the all-aliases column, one alias per line, so an image
        this module imported (which carries exactly one alias, equal to the
        instance name) appears as its own line.  Images are a separate
        namespace from instances in incus/LXD, which is why enumerating them
        needs its own call: ``incus list`` will never mention an image, so a
        reaper built on it alone leaks every qcow2 it ever imported.
        """
        return self._list_names(_IMAGE_LS, prefix) or []

    def survived(self, name: str, *, image: bool) -> bool | None:
        """Is *name* still in the daemon?  ``None`` when it would not say.

        Asked by teardown about something this run may or may not have
        created, which is the one question an exit code cannot answer.
        """
        names = self._list_names(_IMAGE_LS if image else _INSTANCE_LS, INSTANCE_PREFIX)
        return None if names is None else name in names


def select_driver(
    spec: VmTestSpec,
    capabilities: set[str],
    runner: Runner,
    deadline: _Deadline,
) -> tuple[InstanceDriver | None, str]:
    """Pick a hypervisor, or explain why there is none.

    Returns ``(driver, reason)``; ``driver`` is ``None`` when the test must be
    skipped and ``reason`` is the human-readable why.  Classic LXC gets its own
    sentence because "you have lxc, why did it skip" is otherwise a genuinely
    confusing five minutes: ``lxc-*`` is daemonless and containers-only, so it
    can never boot a VM no matter how healthy it is.
    """
    for name in spec.hypervisors:
        if name in capabilities:
            return (
                InstanceDriver(
                    capability=name,
                    binary=_BINARY[name],
                    runner=runner,
                    deadline=deadline,
                    step_timeout=spec.step_timeout_seconds,
                ),
                "",
            )
    wanted = ", ".join(spec.hypervisors)
    if "lxc" in capabilities and not (capabilities & set(VM_CAPABLE)):
        return None, (
            "this builder advertises classic LXC only — liblxc is daemonless and "
            "containers-only, it cannot boot a virtual machine; a VM test needs "
            f"one of: {wanted}"
        )
    have = ", ".join(sorted(capabilities)) or "none"
    return None, f"no VM-capable hypervisor on this builder (need one of: {wanted}; have: {have})"


# ── Signal safety ───────────────────────────────────────────────


@contextmanager
def _interrupt_guard() -> Iterator[None]:
    """Turn SIGINT/SIGTERM into an exception so ``finally`` blocks run.

    Default SIGTERM disposition terminates the process outright — no
    ``finally``, no cleanup, an orphaned VM holding 4 GiB and a disk on a
    builder nobody is looking at.  Raising instead routes the signal through
    the same teardown as any other failure.

    ``signal.signal`` only works on the main thread; off it we restore nothing
    and rely on the ``finally``, which still covers every non-signal path.
    """
    installed: list[tuple[int, Any]] = []

    def handler(signum: int, frame: Any) -> None:
        raise _SignalReceivedError(f"received signal {signum} — destroying the test VM")

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            installed.append((signum, signal.signal(signum, handler)))
        except (ValueError, OSError):  # not the main thread / unsupported
            pass
    try:
        yield
    finally:
        for signum, previous in installed:
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass


@contextmanager
def _deferred_signals(log: Callable[[str], None]) -> Iterator[None]:
    """Hold SIGINT/SIGTERM until teardown finishes, then re-deliver them.

    :func:`_interrupt_guard` is unwound by the time the ``finally`` that
    destroys things runs — it lives INSIDE the ``try``, because its whole job
    is to convert a signal into an exception that reaches that ``finally``.
    Which means the teardown itself runs on the DEFAULT disposition: a second
    Ctrl-C, or the SIGTERM a CI runner sends after its grace period, lands
    between "deleted the instance" and "deleted the image" and kills the
    process outright — leaking the multi-gigabyte half.

    Deferring is not ignoring.  The signal is recorded, cleanup completes, the
    previous handlers are restored, and then the signal is raised again at this
    same process, so the operator gets the shutdown they asked for — just after
    the daemon has been cleaned up rather than instead of it.

    As with :func:`_interrupt_guard`, ``signal.signal`` only works on the main
    thread; off it nothing is installed and teardown runs as it did before.
    """
    pending: list[int] = []

    def handler(signum: int, frame: Any) -> None:
        pending.append(signum)
        log(f"cvcpkg: signal {signum} received during teardown — destroying the VM first")

    installed: list[tuple[int, Any]] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            installed.append((signum, signal.signal(signum, handler)))
        except (ValueError, OSError):  # not the main thread / unsupported
            pass
    try:
        yield
    finally:
        for signum, previous in installed:
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass
        for signum in pending:
            signal.raise_signal(signum)


def _safe_log(log: Callable[[str], None]) -> Callable[[str], None]:
    """A logger that cannot itself leak a VM.

    Teardown is the one place a caller's broken log callback (a closed file, a
    full pipe, a formatter that raises) must not become an escaping exception,
    because the statement right after it is the one that deletes the instance.
    """

    def emit(message: str) -> None:
        try:
            log(message)
        except Exception:  # noqa: BLE001 — a log line is never worth a leaked VM
            pass

    return emit


# ── SSH ─────────────────────────────────────────────────────────

_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "ConnectTimeout=10",
]


def _ssh_argv(key: Path, user: str, address: str, remote: Sequence[str]) -> list[str]:
    return ["ssh", "-i", str(key), *_SSH_OPTS, f"{user}@{address}", *remote]


def _resolve_ssh_key(
    spec: VmTestSpec,
    env: dict[str, str],
    workdir: Path,
) -> tuple[Path | None, str]:
    """Materialise the private key, or say why we cannot.

    Returns ``(path, reason)``.  A declared-but-absent key is a SKIP, not a
    failure, and deliberately so: on a public builder the fleet's private key
    is not present, and turning that into a red build would train everyone to
    ignore the signal.  It is resolved BEFORE anything is created, so this skip
    costs zero hypervisor state.
    """
    if spec.ssh_key_file:
        path = Path(os.path.expandvars(spec.ssh_key_file)).expanduser()
        if not path.is_file():
            return None, f"test.vm.ssh.key_file {path} does not exist"
        return path, ""
    if spec.ssh_key_env:
        material = env.get(spec.ssh_key_env, "")
        if not material.strip():
            return None, (
                f"${spec.ssh_key_env} is empty — no private key for the guest's baked "
                "public key, so the VM could be booted but never asserted against"
            )
        path = workdir / "id_vmtest"
        path.write_text(material if material.endswith("\n") else material + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path, ""
    return None, (
        "connect: ssh needs test.vm.ssh.key_env or test.vm.ssh.key_file "
        "(the private half of the key baked into the image)"
    )


# ── The lifecycle ───────────────────────────────────────────────


#: Instance-name grammar: prefix, package stem, OWNING PID, random suffix.
#: The imported image's alias is the SAME string, so one grammar covers both
#: namespaces.  The pid is what makes the reaper safe on a host where two
#: builds share one daemon — see :func:`reap_stale`.
_INSTANCE_RE = re.compile(rf"^{re.escape(INSTANCE_PREFIX)}.*-(\d+)-[0-9a-f]{{8}}$")

#: How much this run knows about something it asked the daemon to create.
#: ``_MAYBE`` is the window between "we issued the call" and "the daemon told
#: us how it went" — a signal, a deadline or a CLI that raises can all land in
#: it, and an image left behind in that window is a multi-gigabyte leak that
#: nothing else in the run will ever notice.  Teardown resolves ``_MAYBE`` by
#: ASKING the daemon rather than guessing, so a genuinely-nothing-happened
#: failure (a bad metadata tarball) stays quiet instead of crying wolf.
_ABSENT, _MAYBE, _PRESENT = "absent", "maybe", "present"


def _instance_name(package: str, pid: int | None = None) -> str:
    """A name that is unique per run, reapable after a SIGKILL, and attributable.

    Three parts after the prefix: a package stem (so a human reading
    ``incus list`` knows what it is), the OWNING PID (so the reaper can tell a
    corpse from a concurrent run), and random hex (so pid reuse or two runs in
    one process still cannot collide).

    Truncated because incus instance names are DNS labels — 63 characters, and
    some versions are stricter.
    """
    stem = re.sub(r"[^a-z0-9-]", "-", package.lower())[:20].strip("-") or "image"
    return f"{INSTANCE_PREFIX}{stem}-{os.getpid() if pid is None else pid}-{secrets.token_hex(4)}"


def _owner_pid(instance: str) -> int | None:
    """The pid embedded in *instance*, or ``None`` for a name we did not mint."""
    match = _INSTANCE_RE.match(instance)
    if not match:
        return None
    try:
        pid = int(match.group(1))
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_alive(pid: int) -> bool:
    """Whether *pid* is a live process.  Errs towards "yes".

    Every ambiguous answer (EPERM: exists but is not ours; anything unexpected)
    counts as alive, because the two mistakes are not symmetric.  Sparing a
    genuinely dead run's VM costs one more reap cycle; destroying a concurrent
    run's live VM destroys someone's build.
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


@dataclass(frozen=True)
class ReapReport:
    """What :func:`reap_stale` actually removed, by kind."""

    instances: tuple[str, ...] = ()
    images: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.instances) + len(self.images)


def _reapable(
    names: Sequence[str],
    skip: str,
    log: Callable[[str], None],
    kind: str,
) -> list[str]:
    """Filter *names* down to the ones no live process can still own.

    A builder host can be running two cvcpkg builds against ONE daemon, so
    "delete everything with our prefix" would have one build destroying the
    other's live VM — or, just as bad, deleting the image that VM was booted
    from out from under it.  Both instances and image aliases are minted by
    :func:`_instance_name`, so both carry the owning pid and both get the same
    test.  A name that predates the grammar has no owner and is safe to remove.
    """
    out = []
    for name in names:
        if name == skip:
            continue
        pid = _owner_pid(name)
        if pid is not None and _process_alive(pid):
            log(f"cvcpkg: leaving {kind} {name} alone — its owner (pid {pid}) is still running")
            continue
        out.append(name)
    return out


def reap_stale(
    driver: InstanceDriver,
    log: Callable[[str], None],
    *,
    skip: str = "",
) -> ReapReport:
    """Destroy leftovers from a run that died where no handler could run.

    SIGKILL, OOM and a yanked power cord all defeat :func:`_interrupt_guard`,
    and the prefixed name is the only durable record of what was left behind —
    which is why the prefix is a constant and not a nicety.

    Two namespaces, not one.  ``incus list`` enumerates INSTANCES; the imported
    image is a separate object in the daemon's store and no instance listing
    will ever mention it.  Reaping instances alone therefore leaves one
    multi-gigabyte qcow2 behind per killed run, forever, on exactly the
    builders that run image tests most often.

    Instances are reaped FIRST: a daemon refuses to delete an image an instance
    is still using, so the reverse order would fail every time an instance and
    its image were orphaned together — which is the normal case, since they
    share a name.
    """
    instances = []
    for name in _reapable(driver.list_prefixed(INSTANCE_PREFIX), skip, log, "VM"):
        log(f"cvcpkg: reaping stale VM test instance {name}")
        if driver.delete_instance(name).ok:
            instances.append(name)

    images = []
    for alias in _reapable(driver.list_prefixed_images(INSTANCE_PREFIX), skip, log, "image"):
        log(f"cvcpkg: reaping stale VM test image {alias}")
        if driver.delete_image(alias).ok:
            images.append(alias)

    return ReapReport(tuple(instances), tuple(images))


def _wait_for_boot(
    driver: InstanceDriver,
    instance: str,
    spec: VmTestSpec,
    key: Path | None,
    ssh_user: str,
    deadline: _Deadline,
    boot_deadline: _Deadline,
    log: Callable[[str], None],
    sleep: Callable[[float], None],
) -> str:
    """Block until the guest is reachable; return its address (or "" for agent).

    This IS the assertion, even for a recipe that ships no ``script``.  A guest
    that panics on the wrong disk bus, finds no root device, or comes up with
    no network never gets here, and those are the failures that actually ship.
    """
    last = "no reachability attempt completed"
    while True:
        if boot_deadline.expired():
            raise VmTestTimeoutError(
                f"guest did not become reachable within {spec.boot_timeout_seconds}s "
                f"({last}) — a guest that never reaches userland looks exactly like "
                "this, so check boot.disk_bus and boot.firmware in image.yaml"
            )
        if deadline.expired():
            raise VmTestTimeoutError(f"VM test deadline expired while waiting for boot ({last})")

        if spec.connect == "agent":
            probe = driver.exec(instance, ["true"], timeout=30)
            if probe.ok:
                return ""
            last = f"agent exec: {probe.summary}"
        else:
            address = driver.address(instance)
            if not address:
                last = "no DHCP lease yet"
            else:
                assert key is not None  # guaranteed by _resolve_ssh_key gate
                budget = deadline.budget(30, what="ssh probe")
                probe = driver.runner(
                    _ssh_argv(key, ssh_user, address, ["true"]),
                    timeout=budget,
                )
                if probe.ok:
                    return address
                last = f"ssh {address}: {probe.summary}"

        log(f"cvcpkg: waiting for guest ({last})")
        sleep(POLL_INTERVAL_SECONDS)


def _run_guest_script(
    driver: InstanceDriver,
    instance: str,
    script: Path,
    spec: VmTestSpec,
    key: Path | None,
    ssh_user: str,
    address: str,
    deadline: _Deadline,
) -> CommandResult:
    """Execute the recipe's guest-side script inside the VM.

    ``agent`` mode pushes the file and runs it — but that needs the incus agent
    running in the guest, which a non-Linux guest (Haiku, the BSDs) does not
    have.  ``ssh`` mode therefore streams the script to ``sh -s`` on stdin,
    which needs no writable remote path, no scp and no agent.
    """
    budget = deadline.budget(deadline.remaining(), what="guest script")
    if spec.connect == "agent":
        pushed = driver.push(instance, script, GUEST_SCRIPT_PATH)
        if not pushed.ok:
            raise VmTestError(f"could not push the test script into the guest: {pushed.summary}")
        return driver.exec(instance, ["sh", GUEST_SCRIPT_PATH], timeout=budget)
    assert key is not None
    return driver.runner(
        _ssh_argv(key, ssh_user, address, ["sh", "-s"]),
        timeout=budget,
        stdin_path=script,
    )


def run_vm_test(
    *,
    spec: VmTestSpec,
    image: _images.InstalledImage,
    script_path: Path | None = None,
    capabilities: set[str] | None = None,
    runner: Runner | None = None,
    log: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> VmTestResult:
    """Boot *image* in a throwaway VM, assert, and destroy it.

    Returns a :class:`VmTestResult` instead of raising for a test failure —
    skip/pass/fail are all normal outcomes the caller reports differently.
    Setup problems that are the *recipe's* fault (a declared script that does
    not exist, an image with no importer metadata) do raise
    :class:`VmTestError`, because those are bugs to fix, not environments to
    tolerate.

    The VM is destroyed on every exit path.  If you change this function, the
    invariant to preserve is that nothing between ``created = True`` and the
    ``finally`` may return early.
    """
    from cvcpkg.platform import host_capabilities

    log = log or (lambda _m: None)
    env = dict(os.environ) if env is None else dict(env)
    runner = runner or subprocess_runner
    caps = host_capabilities() if capabilities is None else set(capabilities)

    # -- Gate 1: the recipe's own capability requirement ----------
    missing = [c for c in spec.requires_capabilities if c not in caps]
    if missing:
        return VmTestResult(
            SKIPPED,
            reason=(
                f"builder lacks required capabilit{'y' if len(missing) == 1 else 'ies'}: "
                f"{', '.join(missing)}"
            ),
        )

    deadline = _Deadline(spec.timeout_seconds, clock=clock)

    # -- Gate 2: something that can actually boot a VM ------------
    driver, why = select_driver(spec, caps, runner, deadline)
    if driver is None:
        return VmTestResult(SKIPPED, reason=why)

    # -- Artifacts, resolved before anything is created -----------
    disk = image.role_path("disk")
    if disk is None or not disk.is_file():
        raise VmTestError(f"{image.name}: image.yaml declares no readable root disk")
    metadata = image.role_path(f"{driver.capability}-metadata")
    if metadata is None or not metadata.is_file():
        raise VmTestError(
            f"{image.name}: image.yaml has no importers.{driver.capability} entry — "
            f"`{driver.binary} image import` needs a metadata tarball beside the disk"
        )
    if spec.script and script_path is None:
        raise VmTestError("test.vm.script was declared but no script path was supplied")
    if script_path is not None and not script_path.is_file():
        raise VmTestError(f"test.vm.script not found: {script_path}")

    # The login account.  This used to fall back to the literal ``"root"``,
    # which is the same class of bug as the descriptor constants it reads: a
    # silent guess that produces an unexplained "Permission denied" ten minutes
    # into a boot, on a guest that may have no account by that name at all
    # (Haiku's is named from ``HAIKU_ROOT_USER_NAME``, default ``baron``).  A
    # recipe's build script OMITS ``access.ssh_user`` precisely when it could
    # not establish the account, so an absent key is a statement, not a gap —
    # answer it in ``test.vm.ssh.user`` rather than guessing here.
    ssh_user = spec.ssh_user or str(image.access.get("ssh_user") or "")
    if spec.connect == "ssh" and not ssh_user:
        raise VmTestError(
            f"{image.name}: image.yaml declares no access.ssh_user and "
            f"test.vm.ssh.user is unset — refusing to guess a login account "
            f"(set test.vm.ssh.user in the recipe)"
        )

    with tempfile.TemporaryDirectory(prefix="cvcpkg-vmtest-") as tmp:
        workdir = Path(tmp)
        key: Path | None = None
        if spec.connect == "ssh":
            key, why = _resolve_ssh_key(spec, env, workdir)
            if key is None:
                # Gate 3.  Same class as gate 1: an environment we cannot assert
                # in, discovered before a single byte of hypervisor state exists.
                return VmTestResult(SKIPPED, reason=why)

        instance = _instance_name(image.name)
        alias = instance
        instance_state = _ABSENT
        image_state = _ABSENT
        result = VmTestResult(FAILED, instance=instance, hypervisor=driver.capability)

        try:
            with _interrupt_guard():
                # Inside the guard and the try: a signal during the reap must
                # still reach the finally, and a reap that times out must be a
                # reported failure rather than an escaping exception.
                reap_stale(driver, log, skip=instance)

                log(f"cvcpkg: importing {image.name} into {driver.binary} as {alias}")
                # _MAYBE goes up BEFORE the call, not after: an import that
                # times out, is interrupted, or fails partway through can leave
                # a multi-gigabyte qcow2 in the daemon's store, and this
                # assignment is the only thing that will ever look for it.
                image_state = _MAYBE
                imported = driver.import_image(metadata, disk, alias)
                if not imported.ok:
                    raise VmTestError(f"image import failed: {imported.summary}")
                image_state = _PRESENT

                log(f"cvcpkg: creating throwaway VM {instance}")
                instance_state = _MAYBE  # init can half-create, or never return
                created = driver.init_vm(alias, instance, image.boot)
                if not created.ok:
                    raise VmTestError(f"instance init failed: {created.summary}")
                instance_state = _PRESENT

                bus = str(image.boot.get("disk_bus") or "")
                if bus:
                    log(f"cvcpkg: pinning root device to io.bus={bus}")
                    tuned = driver.set_disk_bus(instance, bus)
                    if not tuned.ok:
                        raise VmTestError(f"could not set io.bus={bus}: {tuned.summary}")

                started = driver.start(instance)
                if not started.ok:
                    raise VmTestError(f"start failed: {started.summary}")

                boot_deadline = _Deadline(spec.boot_timeout_seconds, clock=clock)
                address = _wait_for_boot(
                    driver, instance, spec, key, ssh_user, deadline, boot_deadline, log, sleep
                )
                log(f"cvcpkg: guest is up ({address or 'via agent'})")

                if script_path is None:
                    result = replace(result, status=PASSED, reason="guest booted and is reachable")
                else:
                    log(f"cvcpkg: running {script_path.name} in the guest")
                    ran = _run_guest_script(
                        driver, instance, script_path, spec, key, ssh_user, address, deadline
                    )
                    output = (ran.stdout + ran.stderr).strip()
                    result = replace(
                        result,
                        status=PASSED if ran.ok else FAILED,
                        output=output,
                        reason=(
                            "guest test script passed"
                            if ran.ok
                            else f"guest test script failed (exit {ran.returncode}): {ran.summary}"
                        ),
                    )
        except (VmTestError, OSError) as exc:
            # VmTestError covers VmTestTimeoutError and _SignalReceivedError; OSError covers
            # a runner that raised despite the contract.  Anything else escapes
            # deliberately — an unexpected exception is a bug worth seeing, and
            # the finally below has already destroyed the VM AND its imported
            # image by the time it reaches the caller.
            result = replace(result, status=FAILED, reason=str(exc))
        finally:
            # Signals are deferred across the whole teardown, not just one
            # delete: the instance and the image are two calls, and dying
            # between them leaks the expensive half.
            with _deferred_signals(_safe_log(log)):
                result.destroyed = _destroy(
                    driver, instance, alias, instance_state, image_state, log, clock
                )

        return result


def _survived(
    state: str,
    name: str,
    probe: Callable[[], bool | None],
    log: Callable[[str], None],
) -> bool:
    """Whether *name* still exists and therefore has to be deleted.

    ``_MAYBE`` is resolved by asking the daemon instead of assuming, because
    both assumptions are wrong in a way that matters.  Assuming "yes" makes
    every bad-metadata import print a scary leak warning about an image that
    was never created, which trains people to ignore the warning.  Assuming
    "no" is how a half-finished import leaks a multi-gigabyte qcow2.

    A daemon that will not answer is not a "no": attempting the delete costs
    one bounded call, and its failure is the honest "a human should look at
    this builder" that a silent leak never produces.
    """
    if state == _PRESENT:
        return True
    if state == _ABSENT:
        return False
    try:
        answer = probe()
    except Exception as exc:  # noqa: BLE001 — teardown asks; it never propagates
        log(f"cvcpkg: WARNING: could not ask about {name} ({exc}) — assuming it exists")
        return True
    if answer is None:
        log(f"cvcpkg: WARNING: the daemon would not say whether {name} exists — assuming it does")
        return True
    return answer


def _destroy(
    driver: InstanceDriver,
    instance: str,
    alias: str,
    instance_state: str,
    image_state: str,
    log: Callable[[str], None],
    clock: Callable[[], float],
) -> bool:
    """Tear down everything this run created.  Never raises.

    Cleanup gets a FRESH deadline: by the time we are here the run's deadline
    has usually expired, and "we ran out of time" must never be allowed to mean
    "we leaked a VM".  It is still bounded, so a wedged daemon cannot convert a
    timeout into an indefinite hang.

    "Never raises" is load-bearing rather than polite: this runs from a
    ``finally``, so an exception escaping it would both replace the real
    outcome with a confusing one and skip the ``destroyed`` bookkeeping the
    caller uses to warn a human.  Every step is therefore individually
    guarded, the logger is wrapped so a broken callback cannot abort teardown
    between two deletes, and the whole body has a backstop.
    """
    log = _safe_log(log)
    if instance_state == _ABSENT and image_state == _ABSENT:
        return True
    try:
        driver = replace(
            driver,
            deadline=_Deadline(CLEANUP_TIMEOUT_SECONDS, clock=clock),
            step_timeout=CLEANUP_TIMEOUT_SECONDS,
        )

        def attempt(what: str, delete: Callable[[], CommandResult]) -> bool:
            """Run one teardown step.  A non-zero exit and an exception are the
            same event here: something we made still exists."""
            try:
                result = delete()
            except Exception as exc:  # noqa: BLE001 — teardown must not mask the outcome
                log(f"cvcpkg: WARNING: could not delete {what}: {exc}")
                return False
            if not result.ok:
                log(f"cvcpkg: WARNING: could not delete {what}: {result.summary}")
                return False
            log(f"cvcpkg: destroyed {what}")
            return True

        had_instance = _survived(
            instance_state, instance, lambda: driver.survived(instance, image=False), log
        )
        had_image = _survived(image_state, alias, lambda: driver.survived(alias, image=True), log)

        # Instance first: a daemon refuses to delete an image an instance still
        # uses, so the reverse order would leak the (multi-gigabyte) image every
        # time.  Both are attempted even if the first fails — a leaked image costs
        # real disk on the builder, so it is worth trying and worth reporting.
        ok = True
        if had_instance:
            ok = attempt(f"VM {instance}", lambda: driver.delete_instance(instance)) and ok
        if had_image:
            ok = attempt(f"image {alias}", lambda: driver.delete_image(alias)) and ok
        return ok
    except Exception as exc:  # noqa: BLE001 — a raising finally hides the real failure
        log(
            f"cvcpkg: WARNING: teardown itself failed ({exc!r}) — "
            f"look for {INSTANCE_PREFIX}* instances and images"
        )
        return False


# ── Reporting ───────────────────────────────────────────────────


def format_result(image_name: str, result: VmTestResult) -> str:
    """One line a human can read in a build log."""
    if result.status == SKIPPED:
        return f"cvcpkg: VM test for {image_name}: SKIPPED — {result.reason}"
    if result.status == PASSED:
        where = f" on {result.hypervisor}" if result.hypervisor else ""
        return f"cvcpkg: VM test for {image_name}: PASSED{where} — {result.reason}"
    return f"cvcpkg: VM test for {image_name}: FAILED — {result.reason}"
