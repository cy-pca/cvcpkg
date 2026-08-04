"""Ephemeral-VM recipe tests (``test.vm``) with the hypervisor mocked.

CI has no incus, and a unit test that needed one would not be a unit test.
Every test here drives :class:`FakeHypervisor`, a recorded stand-in for the
``incus``/``lxc``/``ssh`` command surface, so the properties under test are the
ones cvcpkg is actually responsible for:

* the capability gate skips rather than fails;
* the VM **and the imported image** are destroyed on EVERY exit path — pass,
  guest failure, boot timeout, a hypervisor CLI that explodes, a half-finished
  import, SIGTERM during the run AND SIGTERM during the teardown itself, and an
  exception raised inside the cleanup path;
* the reaper covers BOTH namespaces, so a run killed by SIGKILL does not leave
  a multi-gigabyte qcow2 in the daemon's store forever;
* the wall-clock deadline is real and bounds every subprocess call;
* cleanup still runs after the deadline has expired.

The fake models the daemon's state (two namespaces, instances and images)
rather than answering from a static table, because the resource leak this file
now guards against is invisible to a stateless mock: ``incus list`` never
mentions an image, so a reaper built on it alone passes every stateless test
while filling the builder's disk one qcow2 per run.
"""

from __future__ import annotations

import contextlib
import os
import signal
import stat
import threading
from pathlib import Path

import pytest
import yaml

from cvcpkg import images, vmtest

# ── Fixtures: a staged image on disk ────────────────────────────

DESCRIPTOR = {
    "schema_version": 1,
    "image": {
        "package": "haiku-image",
        "version": "1.0.0-beta.5+cvc.1",
        "guest_os": "haiku",
        "guest_arch": "x86_64",
        "guest_release": "r1beta5",
        "variant": "builder",
    },
    "disks": [{"file": "disk.qcow2", "format": "qcow2", "role": "root"}],
    "boot": {
        "firmware": "uefi",
        "disk_bus": "nvme",
        "console": "none",
        "secureboot": False,
        "cpu_min": 4,
        "memory_min_mib": 4096,
        "disk_min_gib": 50,
    },
    "access": {"ssh_user": "user", "ssh_pubkey_baked": True},
    "importers": {"incus": "incus/metadata.tar.xz", "lxd": "incus/metadata.tar.xz"},
    "writable": False,
    "docs": "README.md",
}


def _write_image(root: Path, descriptor: dict | None = None) -> images.InstalledImage:
    """Stage a minimal but structurally real image package under *root*."""
    doc = descriptor if descriptor is not None else DESCRIPTOR
    image_dir = root / "share" / doc["image"]["package"]
    (image_dir / "incus").mkdir(parents=True, exist_ok=True)
    (image_dir / "disk.qcow2").write_bytes(b"QFI\xfb" + b"\0" * 64)
    (image_dir / "incus" / "metadata.tar.xz").write_bytes(b"\xfd7zXZ\x00")
    (image_dir / "README.md").write_text("docs\n")
    (image_dir / "image.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return images.load_image(image_dir)


@pytest.fixture
def image(tmp_path: Path) -> images.InstalledImage:
    return _write_image(tmp_path / "prefix")


@pytest.fixture
def guest_script(tmp_path: Path) -> Path:
    path = tmp_path / "vm-test.sh"
    path.write_text("#!/bin/sh\nexit 0\n")
    return path


# ── The mocked hypervisor ───────────────────────────────────────


class FakeHypervisor:
    """Recorded stand-in for the ``incus``/``lxc``/``ssh`` command surface.

    Classifies each argv into a *kind*, records it, and answers from a table.
    ``overrides`` maps a kind to either a :class:`~cvcpkg.vmtest.CommandResult`
    or an exception instance to raise, which is how the destroy-always tests
    inject failures at a chosen point in the lifecycle.

    It also keeps a small amount of real STATE, because the resource-leak bug
    this file now guards against is invisible to a stateless mock: incus keeps
    instances and images in two separate namespaces, ``incus list`` never
    mentions an image, and a fake that answered both listings from one static
    tuple would happily "pass" a reaper that leaks every qcow2 it imports.  So
    ``image import``/``init`` add to the two listings and the deletes remove
    from them.

    ``partial`` names kinds that FAIL but still leave their object behind —
    the half-finished ``incus init`` and the interrupted multi-gigabyte
    ``image import``, which are the states an exit code alone cannot describe.
    """

    def __init__(
        self,
        *,
        address: str = "10.0.0.5",
        leases_after: int = 0,
        ssh_ready_after: int = 0,
        guest_rc: int = 0,
        guest_stdout: str = "PASS: haiku-image guest checks completed\n",
        stale: tuple[str, ...] = (),
        stale_images: tuple[str, ...] = (),
        overrides: dict | None = None,
        partial: tuple[str, ...] = (),
        on_call=None,
    ) -> None:
        self.address = address
        self.leases_after = leases_after
        self.ssh_ready_after = ssh_ready_after
        self.guest_rc = guest_rc
        self.guest_stdout = guest_stdout
        self.stale = stale
        self.stale_images = stale_images
        self.overrides = overrides or {}
        self.partial = partial
        self.on_call = on_call
        self.calls: list[tuple[str, ...]] = []
        self.kinds: list[str] = []
        self.timeouts: list[float] = []
        self.stdins: list[Path | None] = []
        self.instances: list[str] = list(stale)
        self.images: list[str] = list(stale_images)
        self._address_polls = 0
        self._ssh_probes = 0

    # -- classification ------------------------------------------

    @staticmethod
    def classify(argv: list[str]) -> str:
        if argv[0] == "ssh":
            return "ssh-probe" if argv[-1] == "true" else "ssh-script"
        rest = argv[1:]
        if rest[:2] == ["image", "import"]:
            return "image-import"
        if rest[:2] == ["image", "delete"]:
            return "image-delete"
        if rest[:2] == ["image", "list"]:
            return "image-list"
        if rest[0] == "init":
            return "init"
        if rest[:3] == ["config", "device", "set"]:
            return "device-set"
        if rest[0] == "start":
            return "start"
        if rest[0] == "delete":
            return "delete"
        if rest[0] == "exec":
            return "exec-probe" if rest[-1] == "true" else "exec-script"
        if rest[:2] == ["file", "push"]:
            return "push"
        if rest[0] == "list":
            return "list-names" if "-c" in rest and rest[rest.index("-c") + 1] == "n" else "list-ip"
        raise AssertionError(f"FakeHypervisor got an unclassifiable argv: {argv}")

    # -- the daemon's two namespaces -----------------------------

    def _apply(self, kind: str, argv: list[str], *, effective: bool) -> None:
        """Mutate the fake daemon's state the way the real one would."""
        if not effective:
            return
        if kind == "image-import":
            self.images.append(argv[argv.index("--alias") + 1])
        elif kind == "init":
            self.instances.append(argv[3])
        elif kind == "delete" and argv[2] in self.instances:
            self.instances.remove(argv[2])
        elif kind == "image-delete" and argv[3] in self.images:
            self.images.remove(argv[3])

    # -- runner --------------------------------------------------

    def __call__(self, argv, *, timeout: float, stdin_path: Path | None = None):
        argv = list(argv)
        kind = self.classify(argv)
        self.calls.append(tuple(argv))
        self.kinds.append(kind)
        self.timeouts.append(timeout)
        self.stdins.append(stdin_path)
        if self.on_call is not None:
            self.on_call(kind, argv)

        override = self.overrides.get(kind)
        if isinstance(override, BaseException):
            # A CLI that explodes may still have done its work first.
            self._apply(kind, argv, effective=kind in self.partial)
            raise override
        if override is not None:
            self._apply(kind, argv, effective=override.ok or kind in self.partial)
            return override
        self._apply(kind, argv, effective=True)

        if kind == "list-names":
            body = "".join(f"{n}\n" for n in self.instances)
            return vmtest.CommandResult(tuple(argv), 0, body)
        if kind == "image-list":
            body = "".join(f"{n}\n" for n in self.images)
            return vmtest.CommandResult(tuple(argv), 0, body)
        if kind == "list-ip":
            self._address_polls += 1
            ready = self._address_polls > self.leases_after
            return vmtest.CommandResult(tuple(argv), 0, f'"{self.address}"\n' if ready else "\n")
        if kind in ("ssh-probe", "exec-probe"):
            self._ssh_probes += 1
            if self._ssh_probes > self.ssh_ready_after:
                return vmtest.CommandResult(tuple(argv), 0)
            return vmtest.CommandResult(tuple(argv), 255, "", "Connection refused")
        if kind in ("ssh-script", "exec-script"):
            return vmtest.CommandResult(tuple(argv), self.guest_rc, self.guest_stdout)
        return vmtest.CommandResult(tuple(argv), 0)

    # -- assertions helpers --------------------------------------

    def argv_for(self, kind: str) -> list[str]:
        for argv, k in zip(self.calls, self.kinds, strict=False):
            if k == kind:
                return list(argv)
        raise AssertionError(f"no {kind} call was made; got {self.kinds}")

    @property
    def destroyed(self) -> bool:
        return "delete" in self.kinds


class FakeClock:
    """A monotonic clock the test drives explicitly.

    Time only moves when a test says so — usually from ``on_call``, so "the
    guest took another 30 seconds to not answer" is expressed where it happens
    instead of hidden in a tick rate.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def advance_on(self, kind: str, seconds: float):
        """An ``on_call`` hook that ages the clock whenever *kind* runs."""

        def hook(called_kind: str, argv) -> None:
            if called_kind == kind:
                self.advance(seconds)

        return hook


def _spec(**kw) -> vmtest.VmTestSpec:
    """A spec with an ssh key already resolved out of the way."""
    kw.setdefault("hypervisors", ("incus",))
    kw.setdefault("ssh_key_env", "CVCPKG_TEST_KEY")
    return vmtest.VmTestSpec(**kw)


def _run(image, fake, spec=None, *, caps=("incus",), env=None, script=None, **kw):
    return vmtest.run_vm_test(
        spec=spec or _spec(),
        image=image,
        script_path=script,
        capabilities=set(caps),
        runner=fake,
        env={"CVCPKG_TEST_KEY": "-----BEGIN KEY-----\nabc\n", **(env or {})},
        sleep=lambda _s: None,
        **kw,
    )


# ── 1. Spec parsing ─────────────────────────────────────────────


class TestSpecParsing:
    def test_absent_block_is_no_test(self):
        assert vmtest.VmTestSpec.from_dict(None) is None
        assert vmtest.VmTestSpec.from_dict({}) is None

    def test_defaults(self):
        spec = vmtest.VmTestSpec.from_dict({"requires_capabilities": ["incus"]})
        assert spec is not None
        assert spec.image == "self"
        assert spec.connect == "ssh"
        assert spec.hypervisors == vmtest.VM_CAPABLE
        assert spec.timeout_seconds == vmtest.DEFAULT_TIMEOUT_SECONDS
        assert spec.boot_timeout_seconds == vmtest.DEFAULT_BOOT_TIMEOUT_SECONDS

    def test_full_block(self):
        spec = vmtest.VmTestSpec.from_dict(
            {
                "requires_capabilities": ["incus"],
                "hypervisors": ["incus"],
                "image": "freebsd-image",
                "script": "vm-test.sh",
                "connect": "agent",
                "ssh": {"user": "root", "key_env": "K"},
                "timeout_seconds": 60,
                "boot_timeout_seconds": 30,
            }
        )
        assert spec is not None
        assert spec.image == "freebsd-image"
        assert spec.script == "vm-test.sh"
        assert spec.connect == "agent"
        assert spec.ssh_user == "root"
        assert spec.ssh_key_env == "K"
        assert spec.timeout_seconds == 60

    def test_classic_lxc_is_rejected_as_a_hypervisor(self):
        # Classic LXC cannot boot a VM at all, so accepting it here would only
        # move the failure to a confusing runtime error.
        with pytest.raises(vmtest.VmTestError, match="cannot boot a virtual machine"):
            vmtest.VmTestSpec.from_dict({"hypervisors": ["lxc"]})

    def test_bad_connect_is_rejected(self):
        with pytest.raises(vmtest.VmTestError, match="connect"):
            vmtest.VmTestSpec.from_dict({"connect": "telnet"})

    def test_bad_image_name_is_rejected(self):
        with pytest.raises(vmtest.VmTestError, match="package name"):
            vmtest.VmTestSpec.from_dict({"image": "../../etc"})


# ── 2. The capability gate: skip, never fail ────────────────────


class TestGating:
    def test_missing_required_capability_skips_without_touching_anything(self, image):
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(requires_capabilities=("incus",)), caps=())
        assert result.status == vmtest.SKIPPED
        assert "incus" in result.reason
        assert fake.calls == [], "a skipped test must not create hypervisor state"
        assert not result.leaked

    def test_no_vm_capable_hypervisor_skips(self, image):
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(hypervisors=("incus", "lxd")), caps=("cuda",))
        assert result.status == vmtest.SKIPPED
        assert "no VM-capable hypervisor" in result.reason
        assert fake.calls == []

    def test_classic_lxc_only_skips_with_an_explanation(self, image):
        # "I have lxc, why did it skip" is otherwise a genuinely confusing five
        # minutes: the binary is there and healthy, it just cannot boot a VM.
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(hypervisors=("incus", "lxd")), caps=("lxc",))
        assert result.status == vmtest.SKIPPED
        assert "containers-only" in result.reason
        assert fake.calls == []

    def test_missing_ssh_key_skips_before_creating_anything(self, image):
        fake = FakeHypervisor()
        result = _run(image, fake, env={"CVCPKG_TEST_KEY": "  "})
        assert result.status == vmtest.SKIPPED
        assert "CVCPKG_TEST_KEY" in result.reason
        assert fake.calls == []

    def test_ssh_key_file_that_does_not_exist_skips(self, image, tmp_path):
        fake = FakeHypervisor()
        spec = vmtest.VmTestSpec(
            hypervisors=("incus",), ssh_key_file=str(tmp_path / "nope"), ssh_key_env=None
        )
        result = _run(image, fake, spec)
        assert result.status == vmtest.SKIPPED
        assert "does not exist" in result.reason

    def test_lxd_is_driven_through_the_lxc_binary(self, image):
        # LXD's client is literally named `lxc`; getting this backwards would
        # make every LXD builder fail with "command not found".
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(hypervisors=("lxd",)), caps=("lxd",))
        assert result.status == vmtest.PASSED
        assert fake.argv_for("image-import")[0] == "lxc"


# ── 3. Happy path ───────────────────────────────────────────────


class TestHappyPath:
    def test_lifecycle_order(self, image):
        fake = FakeHypervisor()
        result = _run(image, fake)
        assert result.status == vmtest.PASSED, result.reason
        assert result.hypervisor == "incus"
        # list-names is the stale reaper; it must come first, before we add an
        # instance of our own that it would otherwise match.
        assert fake.kinds[0] == "list-names"
        ordered = [k for k in fake.kinds if k in ("image-import", "init", "device-set", "start")]
        assert ordered == ["image-import", "init", "device-set", "start"]
        assert fake.kinds[-2:] == ["delete", "image-delete"]
        assert result.destroyed and not result.leaked

    def test_boot_only_is_a_real_assertion(self, image):
        # No guest script: "it booted and answered" already catches the failure
        # mode this whole feature exists for (a wrong disk_bus never gets here).
        fake = FakeHypervisor()
        result = _run(image, fake)
        assert result.status == vmtest.PASSED
        assert "booted" in result.reason
        assert "ssh-script" not in fake.kinds

    def test_guest_script_is_streamed_over_ssh_stdin(self, image, guest_script):
        # scp and `incus file push` both need something the guest does not have
        # (a writable path chosen by us / the incus agent); `sh -s` needs neither.
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(script="vm-test.sh"), script=guest_script)
        assert result.status == vmtest.PASSED
        argv = fake.argv_for("ssh-script")
        assert argv[-2:] == ["sh", "-s"]
        assert fake.stdins[fake.kinds.index("ssh-script")] == guest_script

    def test_boot_facts_come_from_the_descriptor(self, image):
        fake = FakeHypervisor()
        _run(image, fake)
        init = fake.argv_for("init")
        assert "--vm" in init
        assert "limits.cpu=4" in init
        assert "limits.memory=4096MiB" in init
        assert "security.secureboot=false" in init
        assert "root,size=50GiB" in init
        # The single most load-bearing line: the hypervisor default is
        # virtio-scsi, which this guest has no driver for at all.
        assert fake.argv_for("device-set")[-1] == "io.bus=nvme"

    def test_ssh_uses_the_descriptors_user_and_batch_mode(self, image):
        fake = FakeHypervisor()
        _run(image, fake)
        argv = fake.argv_for("ssh-probe")
        assert "user@10.0.0.5" in argv
        assert "BatchMode=yes" in argv, "a probe must never sit waiting on a prompt"

    def test_waits_through_polls_for_a_lease_and_for_sshd(self, image):
        # Three polls with no DHCP lease, then three more while sshd starts.
        fake = FakeHypervisor(leases_after=3, ssh_ready_after=2)
        result = _run(image, fake)
        assert result.status == vmtest.PASSED
        assert fake.kinds.count("list-ip") == 6
        assert fake.kinds.count("ssh-probe") == 3

    def test_agent_mode_pushes_and_execs(self, image, guest_script):
        fake = FakeHypervisor()
        spec = vmtest.VmTestSpec(
            hypervisors=("incus",), connect="agent", script="vm-test.sh", ssh_key_env=None
        )
        result = _run(image, fake, spec, script=guest_script)
        assert result.status == vmtest.PASSED
        assert "push" in fake.kinds
        assert fake.argv_for("exec-script")[-2:] == ["sh", vmtest.GUEST_SCRIPT_PATH]
        assert "ssh-probe" not in fake.kinds

    def test_instance_name_is_prefixed_and_unique(self, image):
        first, second = FakeHypervisor(), FakeHypervisor()
        a = _run(image, first)
        b = _run(image, second)
        assert a.instance.startswith(vmtest.INSTANCE_PREFIX)
        assert a.instance != b.instance, "two concurrent builders must not collide"


# ── 4. Destroy-always ───────────────────────────────────────────


class TestDestroyAlways:
    def test_guest_script_failure_still_destroys(self, image, guest_script):
        fake = FakeHypervisor(guest_rc=1, guest_stdout="FAIL: root volume is not writable\n")
        result = _run(image, fake, _spec(script="vm-test.sh"), script=guest_script)
        assert result.status == vmtest.FAILED
        assert "exit 1" in result.reason
        assert "not writable" in result.output
        assert fake.destroyed and result.destroyed and not result.leaked
        assert fake.instances == [] and fake.images == []

    def test_boot_timeout_still_destroys(self, image):
        # The guest never gets a lease: exactly what a wrong disk_bus looks
        # like from outside.  A hung boot must cost minutes, not a builder.
        clock = FakeClock()
        fake = FakeHypervisor(leases_after=10**6, on_call=clock.advance_on("list-ip", 30))
        result = _run(
            image,
            fake,
            _spec(boot_timeout_seconds=120, timeout_seconds=3600),
            clock=clock,
        )
        assert result.status == vmtest.FAILED
        assert "did not become reachable" in result.reason
        assert "disk_bus" in result.reason, "the error must point at the usual cause"
        assert fake.kinds.count("list-ip") <= 6, "polling must stop at the boot deadline"
        assert fake.destroyed
        assert fake.instances == [] and fake.images == [], "a hung boot must not cost disk"

    def test_total_deadline_still_destroys(self, image):
        # boot_timeout is enormous here, so only the TOTAL deadline can stop it.
        clock = FakeClock()
        fake = FakeHypervisor(leases_after=10**6, on_call=clock.advance_on("list-ip", 30))
        result = _run(
            image,
            fake,
            _spec(timeout_seconds=90, boot_timeout_seconds=10_000),
            clock=clock,
        )
        assert result.status == vmtest.FAILED
        assert "deadline expired" in result.reason
        assert fake.destroyed
        assert fake.instances == [] and fake.images == []

    def test_start_failure_still_destroys(self, image):
        fake = FakeHypervisor(
            overrides={"start": vmtest.CommandResult(("incus", "start"), 1, "", "no such profile")}
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "start failed" in result.reason
        assert fake.destroyed

    def test_init_failure_still_destroys_the_half_created_instance(self, image):
        # `incus init` can fail AFTER creating the instance record, so cleanup
        # must run for a failed init too.
        fake = FakeHypervisor(
            overrides={"init": vmtest.CommandResult(("incus", "init"), 1, "", "disk full")},
            partial=("init",),
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert fake.destroyed, "a failed init may still have created the instance"
        assert fake.instances == [], "and it must actually be gone afterwards"

    def test_import_failure_creates_nothing_and_does_not_warn(self, image):
        fake = FakeHypervisor(
            overrides={"image-import": vmtest.CommandResult(("incus",), 1, "", "bad metadata")}
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "delete" not in fake.kinds, "nothing was created, so nothing to delete"
        assert "image-delete" not in fake.kinds
        assert not result.leaked

    def test_hypervisor_cli_exception_still_destroys(self, image):
        fake = FakeHypervisor(overrides={"start": OSError("incusd went away")})
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "incusd went away" in result.reason
        assert fake.destroyed
        assert fake.instances == [] and fake.images == []

    def test_unexpected_exception_propagates_but_destroys_first(self, image):
        # An exception the module did not anticipate is a bug worth seeing; the
        # VM and its image must still be gone by the time it reaches the caller.
        fake = FakeHypervisor(overrides={"start": RuntimeError("boom")})
        with pytest.raises(RuntimeError, match="boom"):
            _run(image, fake)
        assert fake.destroyed
        assert fake.instances == [] and fake.images == []

    # signal.signal() only works on the main thread, so a test that raises a
    # real signal would kill the process rather than exercise the guard if a
    # plugin ever ran tests off-main.  Skip rather than take the whole run down.
    @pytest.mark.skipif(
        threading.current_thread() is not threading.main_thread(),
        reason="raising a real signal is only meaningful on the main thread",
    )
    def test_sigterm_destroys_the_vm(self, image):
        # Default SIGTERM disposition terminates the process outright: no
        # finally, no cleanup, an orphaned VM on a builder nobody watches.
        def on_call(kind, argv):
            if kind == "start":
                signal.raise_signal(signal.SIGTERM)

        fake = FakeHypervisor(on_call=on_call)
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "signal" in result.reason
        assert fake.destroyed

    @pytest.mark.skipif(
        threading.current_thread() is not threading.main_thread(),
        reason="raising a real signal is only meaningful on the main thread",
    )
    def test_sigint_destroys_the_vm(self, image):
        def on_call(kind, argv):
            if kind == "ssh-probe":
                signal.raise_signal(signal.SIGINT)

        fake = FakeHypervisor(on_call=on_call)
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert fake.destroyed

    def test_a_failed_delete_is_reported_as_a_leak(self, image):
        fake = FakeHypervisor(
            overrides={"delete": vmtest.CommandResult(("incus",), 1, "", "instance is busy")}
        )
        result = _run(image, fake)
        assert result.leaked, "a delete that failed must be visible, not swallowed"

    def test_cleanup_runs_even_though_the_deadline_expired(self, image):
        # "We ran out of time" must never be allowed to mean "we leaked a VM",
        # so cleanup draws on a fresh clock rather than the run's deadline.
        clock = FakeClock()
        fake = FakeHypervisor(leases_after=10**6, on_call=clock.advance_on("list-ip", 60))
        result = _run(image, fake, _spec(timeout_seconds=90), clock=clock)
        assert result.status == vmtest.FAILED
        assert clock() > 90, "the run really did outlive its deadline"
        assert fake.destroyed and result.destroyed

    def test_interrupt_guard_restores_previous_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        with vmtest._interrupt_guard():
            assert signal.getsignal(signal.SIGTERM) is not previous
        assert signal.getsignal(signal.SIGTERM) is previous


class _Aborted(BaseException):
    """Stands in for "the default disposition ended the process right here".

    A ``BaseException`` on purpose: default SIGTERM kills the process outright
    and default SIGINT raises ``KeyboardInterrupt``, and neither is catchable
    by an ``except Exception``.  A test handler that raised a plain
    ``Exception`` would be caught by the teardown's own per-step guard and so
    would pass whether or not the deferral exists.
    """


@contextlib.contextmanager
def _fatal_handler(signum: int):
    """Install an ABORTING handler for *signum*, like the default disposition.

    Two things are being proved at once.  Undeferred, a signal arriving
    between the two deletes must be shown to strand the image — which needs a
    handler that actually stops execution.  Deferred, the signal must still be
    RE-DELIVERED after cleanup rather than eaten, which shows up as the abort
    arriving late instead of never.
    """
    fired: list[int] = []

    def handler(signum_: int, _frame) -> None:
        fired.append(signum_)
        raise _Aborted(f"signal {signum_} ended the process")

    previous = signal.signal(signum, handler)
    try:
        yield fired
    finally:
        signal.signal(signum, previous)


_MAIN_THREAD_ONLY = pytest.mark.skipif(
    threading.current_thread() is not threading.main_thread(),
    reason="raising a real signal is only meaningful on the main thread",
)


class TestTeardownIsItselfProtected:
    """The teardown is two deletes and it runs with the guard already unwound.

    :func:`~cvcpkg.vmtest._interrupt_guard` lives INSIDE the ``try`` — its job
    is to turn a signal into an exception that reaches the ``finally``.  So by
    the time the ``finally`` is deleting things, SIGINT/SIGTERM are back on
    their default disposition, and a CI runner's post-grace-period SIGTERM
    landing between "deleted the VM" and "deleted the image" leaks the
    expensive half.  Same for an exception thrown by the cleanup path itself.
    """

    @_MAIN_THREAD_ONLY
    def test_sigterm_during_teardown_still_deletes_the_image(self, image):
        # The signal lands right after the instance delete and before the image
        # delete — the window a CI runner's post-grace-period SIGTERM hits.
        def on_call(kind, argv):
            if kind == "delete":
                signal.raise_signal(signal.SIGTERM)

        fake = FakeHypervisor(on_call=on_call)
        with _fatal_handler(signal.SIGTERM) as fired:
            with pytest.raises(_Aborted):
                _run(image, fake)
        assert "image-delete" in fake.kinds, "a signal must not end us between the deletes"
        assert fake.instances == [] and fake.images == [], "the daemon must be clean"
        assert fired == [signal.SIGTERM], "re-delivered once, AFTER cleanup — deferred, not eaten"

    @_MAIN_THREAD_ONLY
    def test_sigint_during_teardown_still_deletes_the_image(self, image):
        # The second Ctrl-C: the first one already unwound the interrupt guard.
        def on_call(kind, argv):
            if kind == "delete":
                signal.raise_signal(signal.SIGINT)

        fake = FakeHypervisor(on_call=on_call)
        with _fatal_handler(signal.SIGINT) as fired:
            with pytest.raises(_Aborted):
                _run(image, fake)
        assert fake.instances == [] and fake.images == []
        assert fired == [signal.SIGINT]

    @_MAIN_THREAD_ONLY
    def test_deferred_signals_restores_the_previous_handler(self):
        with _fatal_handler(signal.SIGTERM) as fired:
            previous = signal.getsignal(signal.SIGTERM)
            with vmtest._deferred_signals(lambda _m: None):
                assert signal.getsignal(signal.SIGTERM) is not previous
            assert signal.getsignal(signal.SIGTERM) is previous
            assert fired == [], "nothing was raised, so nothing may be re-delivered"

    def test_a_logger_that_raises_mid_teardown_still_deletes_the_image(self, image):
        # The cleanup path throwing from inside itself: the log line between
        # the two deletes is a caller-supplied callback, and a closed build-log
        # pipe must not be able to strand a multi-gigabyte image.
        def log(message: str) -> None:
            if "destroyed VM" in message:
                raise RuntimeError("the build log pipe closed")

        fake = FakeHypervisor()
        result = _run(image, fake, log=log)
        assert "image-delete" in fake.kinds
        assert fake.instances == [] and fake.images == []
        assert result.destroyed and not result.leaked

    def test_an_exception_from_the_instance_delete_still_deletes_the_image(self, image):
        fake = FakeHypervisor(overrides={"delete": OSError("incusd went away")})
        result = _run(image, fake)
        assert "image-delete" in fake.kinds, "one exploded step must not skip the other"
        assert fake.images == []
        assert result.leaked, "the instance really did survive, and that must be visible"

    def test_teardown_never_raises_even_if_its_own_clock_does(self):
        # `_destroy` runs from a `finally`; anything escaping it both replaces
        # the real outcome and skips the bookkeeping that warns a human.
        def broken_clock() -> float:
            raise RuntimeError("monotonic went backwards")

        lines: list[str] = []
        driver = vmtest.InstanceDriver(
            "incus", "incus", FakeHypervisor(), vmtest._Deadline(60, FakeClock())
        )
        destroyed = vmtest._destroy(
            driver,
            "cvcpkg-vmtest-x",
            "cvcpkg-vmtest-x",
            vmtest._PRESENT,
            vmtest._PRESENT,
            lines.append,
            broken_clock,
        )
        assert destroyed is False, "we could not clean up, and must say so"
        assert any("teardown itself failed" in line for line in lines)

    def test_teardown_does_nothing_when_nothing_was_created(self):
        # A skip must not print warnings about a VM that never existed.
        lines: list[str] = []
        fake = FakeHypervisor()
        driver = vmtest.InstanceDriver("incus", "incus", fake, vmtest._Deadline(60, FakeClock()))
        assert vmtest._destroy(
            driver, "i", "a", vmtest._ABSENT, vmtest._ABSENT, lines.append, FakeClock()
        )
        assert fake.calls == []
        assert lines == []


# ── 5. Timeouts are real ────────────────────────────────────────


CLEANUP_KINDS = ("delete", "image-delete")


class TestDeadline:
    def test_budget_is_clamped_by_what_remains(self):
        deadline = vmtest._Deadline(10, clock=FakeClock())
        assert deadline.budget(900, what="x") == pytest.approx(10)

    def test_budget_raises_once_expired(self):
        clock = FakeClock()
        deadline = vmtest._Deadline(0, clock=clock)
        clock.advance(1)
        with pytest.raises(vmtest.VmTestTimeoutError, match="before import"):
            deadline.budget(10, what="import")

    def test_no_subprocess_call_may_exceed_the_total(self, image):
        # A per-step ceiling of 900s must not survive a 30s total budget.
        fake = FakeHypervisor()
        _run(image, fake, _spec(timeout_seconds=30, step_timeout_seconds=900), clock=FakeClock())
        work = [
            t for t, k in zip(fake.timeouts, fake.kinds, strict=False) if k not in CLEANUP_KINDS
        ]
        assert work, "expected at least one call"
        assert max(work) <= 30

    def test_cleanup_has_its_own_bounded_budget(self, image):
        # Bounded, but drawn fresh: a wedged daemon must not turn a timeout
        # into an indefinite hang, and an expired run must still tear down.
        clock = FakeClock()
        fake = FakeHypervisor(leases_after=10**6, on_call=clock.advance_on("list-ip", 60))
        _run(image, fake, _spec(timeout_seconds=90), clock=clock)
        delete_timeout = fake.timeouts[fake.kinds.index("delete")]
        assert 0 < delete_timeout <= vmtest.CLEANUP_TIMEOUT_SECONDS


# ── 6. Reaping what a SIGKILL left behind ───────────────────────


@pytest.fixture
def dead_pid() -> int:
    """A pid that is definitely not running: a child we started and reaped."""
    import subprocess

    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class TestReaper:
    def test_a_dead_runs_instance_is_destroyed_first(self, image, dead_pid):
        # SIGKILL, OOM and a yanked power cord all defeat the signal guard; the
        # instance name is the only durable record, so the NEXT run cleans up.
        stale = vmtest._instance_name("haiku-image", pid=dead_pid)
        fake = FakeHypervisor(stale=(stale,))
        result = _run(image, fake)
        assert _deleted(fake) == [stale, result.instance]

    def test_a_live_runs_instance_is_left_alone(self, image):
        # THE reason the pid is in the name: one builder host can run two cvcpkg
        # builds against one daemon, and "delete everything with our prefix"
        # would have each destroying the other's live VM mid-boot.
        live = vmtest._instance_name("freebsd-image", pid=os.getpid())
        fake = FakeHypervisor(stale=(live,))
        result = _run(image, fake)
        assert _deleted(fake) == [result.instance]

    def test_unrelated_instances_are_left_alone(self, image):
        # An operator's own instance on a shared host must never be collateral.
        fake = FakeHypervisor(stale=("production-db", "someones-laptop"))
        result = _run(image, fake)
        assert _deleted(fake) == [result.instance]

    def test_a_name_predating_the_pid_grammar_is_still_reaped(self, image):
        # No pid means nobody can own it, so it is safe to remove.
        legacy = f"{vmtest.INSTANCE_PREFIX}haiku-image-deadbeef"
        fake = FakeHypervisor(stale=(legacy,))
        result = _run(image, fake)
        assert _deleted(fake) == [legacy, result.instance]

    def test_our_own_instance_is_never_reaped(self, image):
        # The daemon lists our instance too once it exists; reaping it would
        # destroy the VM we are about to boot.
        fake = FakeHypervisor()
        result = _run(image, fake)
        assert _deleted(fake).count(result.instance) == 1, "deleted once, at teardown"

    def test_process_alive_errs_towards_leaving_it_alone(self, dead_pid):
        # Sparing a dead run's VM costs one reap cycle; destroying a live run's
        # VM destroys someone's build.  The mistakes are not symmetric.
        assert vmtest._process_alive(os.getpid()) is True
        assert vmtest._process_alive(dead_pid) is False
        assert vmtest._process_alive(0) is True, "pid 0 would signal the whole group"
        assert vmtest._process_alive(-1) is True

    def test_instance_name_fits_a_dns_label(self, image):
        name = vmtest._instance_name("some-rather-long-guest-image-name-x86-64")
        assert len(name) <= 63
        assert vmtest._owner_pid(name) == os.getpid()


def _deleted(fake: FakeHypervisor) -> list[str]:
    """Instance names passed to ``delete``, in order (argv: bin delete NAME)."""
    return [a[2] for a, k in zip(fake.calls, fake.kinds, strict=False) if k == "delete"]


def _deleted_images(fake: FakeHypervisor) -> list[str]:
    """Aliases passed to ``image delete`` (argv: bin image delete ALIAS)."""
    return [a[3] for a, k in zip(fake.calls, fake.kinds, strict=False) if k == "image-delete"]


# ── 6b. The imported IMAGE is a resource too ────────────────────


class TestImageIsReapedToo:
    """The leak: every run imports a multi-gigabyte qcow2 into the daemon.

    Instances and images are two separate namespaces in incus/LXD.  ``incus
    list`` enumerates instances and will NEVER mention an image, so a reaper
    built on it alone removes the cheap half of what a killed run left behind
    and silently accumulates the expensive half — on precisely the builders
    that run image tests most often, which is how a builder runs out of disk.
    """

    def test_repeated_runs_leave_nothing_behind(self, image):
        # The headline property, stated as the operator would: run the image
        # test over and over on one builder and the daemon's store stays empty.
        fake = FakeHypervisor()
        for _ in range(5):
            assert _run(image, fake).status == vmtest.PASSED
        assert fake.images == [], "each run imports a multi-GB qcow2; none may survive"
        assert fake.instances == []

    def test_the_reaper_queries_the_image_namespace(self, image):
        fake = FakeHypervisor()
        _run(image, fake)
        argv = fake.argv_for("image-list")
        assert argv[1:3] == ["image", "list"], "`incus list` never mentions an image"
        assert "csv" in argv

    def test_a_dead_runs_image_is_reaped(self, image, dead_pid):
        # SIGKILL defeats every handler; the alias is the only durable record,
        # and it is the one the previous reaper ignored.
        orphan = vmtest._instance_name("haiku-image", pid=dead_pid)
        fake = FakeHypervisor(stale_images=(orphan,))
        result = _run(image, fake)
        assert _deleted_images(fake) == [orphan, result.instance]
        assert fake.images == []

    def test_a_killed_run_leaves_both_halves_and_both_are_reaped(self, image, dead_pid):
        # The realistic corpse: one SIGKILLed run left an instance AND the
        # image it was booted from, sharing a name.
        orphan = vmtest._instance_name("haiku-image", pid=dead_pid)
        fake = FakeHypervisor(stale=(orphan,), stale_images=(orphan,))
        _run(image, fake)
        assert fake.instances == [] and fake.images == []

    def test_instances_are_reaped_before_images(self, image, dead_pid):
        # A daemon refuses to delete an image an instance still uses, and an
        # orphaned pair shares a name, so the reverse order fails every time.
        orphan = vmtest._instance_name("haiku-image", pid=dead_pid)
        fake = FakeHypervisor(stale=(orphan,), stale_images=(orphan,))
        _run(image, fake)
        assert fake.kinds.index("delete") < fake.kinds.index("image-delete")

    def test_a_live_runs_image_is_left_alone(self, image):
        # Deleting the image a concurrent run just booted from is as
        # destructive as deleting its VM, and the pid rule must cover both.
        live = vmtest._instance_name("freebsd-image", pid=os.getpid())
        fake = FakeHypervisor(stale_images=(live,))
        result = _run(image, fake)
        assert _deleted_images(fake) == [result.instance]
        assert fake.images == [live]

    def test_a_pre_existing_image_is_never_touched(self, image):
        # The reaper runs on a shared builder whose daemon holds base images
        # other people depend on.  The prefix is the only thing between it and
        # deleting somebody's golden image; nothing else may be consulted.
        others = ("ubuntu-24.04-container", "haiku-r1beta5-golden", "txwtf-ci-img")
        fake = FakeHypervisor(stale_images=others)
        result = _run(image, fake)
        assert fake.images == list(others)
        assert _deleted_images(fake) == [result.instance]

    def test_a_prefixed_image_predating_the_pid_grammar_is_reaped(self, image):
        # No pid in the name means nobody can own it, so it is safe to remove —
        # same rule as the instance half.
        legacy = f"{vmtest.INSTANCE_PREFIX}haiku-image-deadbeef"
        fake = FakeHypervisor(stale_images=(legacy,))
        result = _run(image, fake)
        assert _deleted_images(fake) == [legacy, result.instance]

    def test_our_own_alias_is_never_reaped(self, image):
        fake = FakeHypervisor()
        result = _run(image, fake)
        assert _deleted_images(fake).count(result.instance) == 1, "deleted once, at teardown"

    def test_reap_stale_reports_both_kinds(self, dead_pid):
        # The report is what a caller would log; it must not describe an image
        # deletion as an instance deletion.
        orphan = vmtest._instance_name("haiku-image", pid=dead_pid)
        fake = FakeHypervisor(stale=(orphan,), stale_images=(orphan,))
        driver = vmtest.InstanceDriver("incus", "incus", fake, vmtest._Deadline(60, FakeClock()))
        report = vmtest.reap_stale(driver, lambda _m: None)
        assert report.instances == (orphan,)
        assert report.images == (orphan,)
        assert report.total == 2

    def test_a_listing_that_fails_deletes_nothing(self):
        # The reaper only ever deletes what a listing named, so a daemon that
        # cannot answer must produce no deletions rather than a wild guess.
        fake = FakeHypervisor(
            overrides={
                "list-names": vmtest.CommandResult(("incus",), 1, "", "daemon unreachable"),
                "image-list": vmtest.CommandResult(("incus",), 1, "", "daemon unreachable"),
            }
        )
        driver = vmtest.InstanceDriver("incus", "incus", fake, vmtest._Deadline(60, FakeClock()))
        report = vmtest.reap_stale(driver, lambda _m: None)
        assert report.total == 0
        assert "delete" not in fake.kinds and "image-delete" not in fake.kinds


# ── 6c. A partially-failed import is still an import ────────────


class TestPartialImport:
    """``incus image import`` copies gigabytes and can fail after doing it.

    Trusting the exit code alone means the run walks away from an alias that is
    already in the daemon's store.  Teardown therefore ASKS the daemon when it
    does not know, which is also what keeps the common case (a bad metadata
    tarball, where nothing was created) from printing a false leak warning.
    """

    def test_a_half_finished_import_is_still_deleted(self, image):
        fake = FakeHypervisor(
            overrides={"image-import": vmtest.CommandResult(("incus",), 1, "", "no space left")},
            partial=("image-import",),
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "image import failed" in result.reason
        assert _deleted_images(fake) == [result.instance]
        assert fake.images == []
        assert not result.leaked

    def test_an_import_that_raises_is_still_deleted(self, image):
        fake = FakeHypervisor(
            overrides={"image-import": OSError("connection reset by incusd")},
            partial=("image-import",),
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert fake.images == [], "a CLI that died mid-copy still left gigabytes behind"

    def test_a_signal_during_the_import_is_still_deleted(self, image):
        # The exact window the leak lived in: the daemon has already copied the
        # qcow2, we have not yet been told it succeeded, and SIGTERM arrives.
        fake = FakeHypervisor(
            overrides={"image-import": vmtest._SignalReceivedError("received signal 15")},
            partial=("image-import",),
        )
        result = _run(image, fake)
        assert result.status == vmtest.FAILED
        assert "signal 15" in result.reason
        assert fake.images == []

    def test_an_import_that_created_nothing_stays_quiet(self, image):
        # A bad metadata tarball is a recipe bug people hit often; if it also
        # printed "your builder may have leaked an image" every time, the
        # warning would stop meaning anything.
        lines: list[str] = []
        fake = FakeHypervisor(
            overrides={"image-import": vmtest.CommandResult(("incus",), 1, "", "bad metadata")}
        )
        result = _run(image, fake, log=lines.append)
        assert _deleted_images(fake) == []
        assert not any("WARNING" in line for line in lines)
        assert not result.leaked

    def test_an_unanswerable_daemon_errs_towards_deleting(self, image):
        # If we cannot even ask, we cannot rule out gigabytes in the store.
        # One bounded delete call is cheap; a silent multi-GB leak is not.
        lines: list[str] = []
        fake = FakeHypervisor(
            overrides={
                "image-import": vmtest.CommandResult(("incus",), 1, "", "timed out"),
                "image-list": vmtest.CommandResult(("incus",), 1, "", "daemon unreachable"),
            }
        )
        result = _run(image, fake, log=lines.append)
        assert _deleted_images(fake) == [result.instance], "assume it exists and try"
        assert any("would not say whether" in line for line in lines)

    def test_a_delete_we_could_not_confirm_is_reported_as_a_leak(self, image):
        # ...and when that speculative delete fails too, the run says so rather
        # than shrugging: a human has to go look at that builder's disk.
        fake = FakeHypervisor(
            overrides={
                "image-import": vmtest.CommandResult(("incus",), 1, "", "timed out"),
                "image-list": vmtest.CommandResult(("incus",), 1, "", "daemon unreachable"),
                "image-delete": vmtest.CommandResult(("incus",), 1, "", "daemon unreachable"),
            }
        )
        assert _run(image, fake).leaked


# ── 7. Setup errors are the recipe's fault, and raise ───────────


class TestSetupErrors:
    def test_missing_importer_metadata_raises(self, tmp_path):
        doc = {**DESCRIPTOR, "importers": {}}
        image = _write_image(tmp_path / "prefix", doc)
        with pytest.raises(vmtest.VmTestError, match="importers.incus"):
            _run(image, FakeHypervisor())

    def test_missing_disk_raises(self, tmp_path):
        image = _write_image(tmp_path / "prefix")
        image.role_path("disk").unlink()
        with pytest.raises(vmtest.VmTestError, match="no readable root disk"):
            _run(image, FakeHypervisor())

    def test_declared_script_that_does_not_exist_raises(self, image, tmp_path):
        with pytest.raises(vmtest.VmTestError, match="not found"):
            _run(image, FakeHypervisor(), _spec(script="vm-test.sh"), script=tmp_path / "gone.sh")

    def test_absent_ssh_user_raises_instead_of_guessing_root(self, tmp_path):
        """An omitted ``access.ssh_user`` is a statement, not a gap.

        A recipe's build script drops the key precisely when it could not
        establish the guest's login account.  Silently substituting ``root``
        (the old behaviour) turns that into an unexplained "Permission denied"
        ten minutes into a boot, on a guest that may have no such account —
        Haiku's is named from ``HAIKU_ROOT_USER_NAME``, default ``baron``.
        """
        doc = {**DESCRIPTOR, "access": {"ssh_pubkey_baked": True}}
        image = _write_image(tmp_path / "prefix", doc)
        with pytest.raises(vmtest.VmTestError, match="no access.ssh_user"):
            _run(image, FakeHypervisor())

    def test_absent_ssh_user_is_satisfied_by_the_recipe(self, tmp_path):
        """...and the recipe can answer it explicitly."""
        doc = {**DESCRIPTOR, "access": {"ssh_pubkey_baked": True}}
        image = _write_image(tmp_path / "prefix", doc)
        fake = FakeHypervisor()
        result = _run(image, fake, _spec(ssh_user="baron"))
        assert result.status != vmtest.FAILED
        assert any("baron@" in " ".join(c) for c in fake.calls if "ssh" in " ".join(c))


# ── 8. The private key never outlives the run ───────────────────


class TestSshKeyHandling:
    def test_key_from_env_is_written_0600_and_removed(self, image):
        seen: dict[str, Path] = {}

        def on_call(kind, argv):
            if kind == "ssh-probe":
                seen["key"] = Path(argv[argv.index("-i") + 1])
                seen["mode"] = stat.S_IMODE(seen["key"].stat().st_mode)

        fake = FakeHypervisor(on_call=on_call)
        result = _run(image, fake)
        assert result.status == vmtest.PASSED
        assert seen["mode"] == 0o600, "ssh refuses a group/world-readable key anyway"
        assert not seen["key"].exists(), "the private key must not outlive the run"

    def test_key_file_is_used_verbatim(self, image, tmp_path):
        key = tmp_path / "id_ed25519"
        key.write_text("KEY\n")
        os.chmod(key, 0o600)
        spec = vmtest.VmTestSpec(hypervisors=("incus",), ssh_key_file=str(key), ssh_key_env=None)
        fake = FakeHypervisor()
        result = _run(image, fake, spec)
        assert result.status == vmtest.PASSED
        assert fake.argv_for("ssh-probe")[2] == str(key)
        assert key.exists(), "a user-supplied key file must not be deleted"


# ── 9. Reporting ────────────────────────────────────────────────


class TestFormatResult:
    def test_skip_says_why(self):
        line = vmtest.format_result("haiku-image", vmtest.VmTestResult(vmtest.SKIPPED, "no incus"))
        assert "SKIPPED" in line and "no incus" in line

    def test_pass_names_the_hypervisor(self):
        result = vmtest.VmTestResult(vmtest.PASSED, "booted", hypervisor="incus")
        assert "PASSED on incus" in vmtest.format_result("haiku-image", result)

    def test_fail_says_why(self):
        line = vmtest.format_result("haiku-image", vmtest.VmTestResult(vmtest.FAILED, "exit 1"))
        assert "FAILED" in line and "exit 1" in line


# ── 10. Recipe plumbing ─────────────────────────────────────────


RECIPES = Path(__file__).resolve().parents[2] / "recipes"


class TestRecipeIntegration:
    def test_recipe_load_carries_the_vm_block(self, tmp_path):
        from cvcpkg.builder import Recipe

        (tmp_path / "recipe.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "recipe": {"name": "demo-image", "upstream_version": "1", "cvc_revision": 1},
                    "source": {"type": "prebuilt", "url": "https://example.invalid"},
                    "test": {"script": "test.sh", "vm": {"requires_capabilities": ["incus"]}},
                }
            )
        )
        recipe = Recipe.load(tmp_path)
        # Both hooks coexist: the host-side script is unchanged, the VM block
        # is additive.
        assert recipe.test_script == "test.sh"
        assert recipe.vm_test == {"requires_capabilities": ["incus"]}

    def test_a_recipe_without_a_vm_block_gets_none(self, tmp_path):
        from cvcpkg.builder import Recipe

        (tmp_path / "recipe.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "recipe": {"name": "demo", "upstream_version": "1", "cvc_revision": 1},
                    "source": {"type": "prebuilt", "url": "https://example.invalid"},
                    "test": {"script": "test.sh"},
                }
            )
        )
        assert Recipe.load(tmp_path).vm_test is None

    def test_haiku_image_recipe_validates_and_parses(self):
        from cvcpkg.builder import Recipe
        from cvcpkg.validation import validate_recipe_dir

        recipe_dir = RECIPES / "haiku-image"
        assert validate_recipe_dir(recipe_dir) == []
        spec = vmtest.VmTestSpec.from_dict(Recipe.load(recipe_dir).vm_test)
        assert spec is not None
        assert spec.requires_capabilities == ("incus",)
        assert spec.connect == "ssh"
        assert spec.script == "vm-test.sh"
        assert (recipe_dir / spec.script).is_file(), "the declared guest script must exist"

    def test_the_vm_capability_gate_is_not_the_install_gate(self):
        # An image must stay INSTALLABLE on a host with no hypervisor —
        # carrying it to such a host is the entire point of the package.
        from cvcpkg.builder import Recipe

        recipe = Recipe.load(RECIPES / "haiku-image")
        assert recipe.requires_capabilities == []
        assert recipe.vm_test["requires_capabilities"] == ["incus"]


class TestBuilderHook:
    def _ctx(self, tmp_path, vm_test, *, name="haiku-image"):
        from cvcpkg.builder import BuildContext, Recipe

        install_dir = tmp_path / "install"
        _write_image(install_dir)
        recipe = Recipe(
            name=name,
            upstream_version="1.0.0",
            cvc_revision=1,
            source=None,
            patches=[],
            build_matrix=[],
            package_files=[],
            test_script=None,
            raw={},
            recipe_dir=tmp_path,
            vm_test=vm_test,
        )
        return BuildContext(
            recipe=recipe,
            platform="linux",
            config="Release",
            link="shared",
            prefix=install_dir,
            source_dir=tmp_path,
            build_dir=tmp_path,
            install_dir=install_dir,
            work_dir=tmp_path,
        )

    def test_no_block_is_a_no_op(self, tmp_path):
        from cvcpkg import builder

        builder.run_vm_test(self._ctx(tmp_path, None))  # must not raise or probe

    def test_skip_does_not_fail_the_build(self, tmp_path, monkeypatch, capsys):
        from cvcpkg import builder

        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        builder.run_vm_test(self._ctx(tmp_path, {"requires_capabilities": ["incus"]}))
        assert "SKIPPED" in capsys.readouterr().out

    def test_failure_becomes_a_build_error(self, tmp_path, monkeypatch):
        from cvcpkg import builder
        from cvcpkg.errors import CvcpkgError

        monkeypatch.setenv("CVCPKG_CAPABILITIES", "incus")
        monkeypatch.setattr(
            vmtest,
            "subprocess_runner",
            FakeHypervisor(overrides={"start": vmtest.CommandResult(("incus",), 1, "", "nope")}),
        )
        monkeypatch.setenv("VMKEY", "-----BEGIN KEY-----\n")
        ctx = self._ctx(tmp_path, {"hypervisors": ["incus"], "ssh": {"key_env": "VMKEY"}})
        with pytest.raises(CvcpkgError, match="VM test for haiku-image failed"):
            builder.run_vm_test(ctx)

    def test_self_resolves_to_the_tree_this_build_staged(self, tmp_path, monkeypatch):
        # Not "whatever is installed in the prefix": the artifact under test is
        # the one this build just produced.
        from cvcpkg import builder

        ctx = self._ctx(tmp_path, {"image": "self"})
        image = builder.resolve_vm_test_image(ctx, "haiku-image")
        assert image.directory == ctx.install_dir / "share" / "haiku-image"

    def test_a_missing_image_is_a_build_error(self, tmp_path):
        from cvcpkg import builder
        from cvcpkg.errors import CvcpkgError

        ctx = self._ctx(tmp_path, {"image": "freebsd-image"})
        with pytest.raises(CvcpkgError, match="freebsd-image"):
            builder.resolve_vm_test_image(ctx, "freebsd-image")


# ── 11. `cvcpkg image test` ─────────────────────────────────────


class TestImageTestCommand:
    def _invoke(self, args):
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        return CliRunner().invoke(cli, ["image", "test", *args])

    def test_skip_exits_zero(self, tmp_path, monkeypatch):
        # A builder (or laptop) with no hypervisor must not fail the command.
        _write_image(tmp_path / "prefix")
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        result = self._invoke(["haiku-image", "--prefix", str(tmp_path / "prefix")])
        assert result.exit_code == 0, result.output
        assert "SKIPPED" in result.output

    def test_failure_exits_six(self, tmp_path, monkeypatch):
        from cvcpkg.cli import _image

        _write_image(tmp_path / "prefix")
        key = tmp_path / "id"
        key.write_text("KEY\n")
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "incus")
        monkeypatch.setattr(
            vmtest,
            "subprocess_runner",
            FakeHypervisor(guest_rc=3, guest_stdout="FAIL: gcc missing\n"),
        )
        script = tmp_path / "vm-test.sh"
        script.write_text("exit 3\n")
        result = self._invoke(
            [
                "haiku-image",
                "--prefix",
                str(tmp_path / "prefix"),
                "--ssh-key",
                str(key),
                "--script",
                str(script),
            ]
        )
        assert result.exit_code == _image.EXIT_VM_TEST_FAILED
        assert "gcc missing" in result.output

    def test_unknown_image_exits_three(self, tmp_path):
        (tmp_path / "prefix").mkdir()
        result = self._invoke(["nope-image", "--prefix", str(tmp_path / "prefix")])
        assert result.exit_code == 3


# ── 12. Validate-time enforcement ───────────────────────────────


class TestValidation:
    """`cvcpkg validate` must catch a broken test.vm before a builder does.

    A typo here is otherwise only discovered by the one builder in the fleet
    with a hypervisor, 90 minutes into an image build, after it has already
    imported and booted the VM.
    """

    def _recipe(self, tmp_path: Path, *, kind=None, test=None, name="demo-image") -> Path:
        recipe = {
            "schema_version": 1,
            "recipe": {
                "name": name,
                "upstream_version": "1.0.0",
                "cvc_revision": 1,
                "maintainer": "t",
                "maintainer_email": "t@example.invalid",
                "homepage": "https://example.invalid",
                "license": "MIT",
                "description": "d",
            },
            "source": {"type": "prebuilt", "url": "https://example.invalid"},
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/libdemo.a"]},
        }
        if kind:
            recipe["recipe"]["kind"] = kind
            recipe["package"] = {"files": [f"share/{name}/image.yaml"]}
        if test:
            recipe["test"] = test
        (tmp_path / "build.sh").write_text("#!/bin/sh\n")
        (tmp_path / "recipe.yaml").write_text(yaml.safe_dump(recipe))
        return tmp_path / "recipe.yaml"

    def _errors(self, tmp_path: Path, **kw) -> list[str]:
        from cvcpkg.validation import validate_recipe_dir

        self._recipe(tmp_path, **kw)
        return validate_recipe_dir(tmp_path)

    def test_a_recipe_with_no_test_block_is_unaffected(self, tmp_path):
        # Regression: an absent `vm` key must not be read as "vm with image
        # defaulting to self", which flagged every library recipe in the tree.
        assert self._errors(tmp_path, name="demo") == []

    def test_a_recipe_with_only_a_host_test_is_unaffected(self, tmp_path):
        (tmp_path / "test.sh").write_text("#!/bin/sh\n")
        assert self._errors(tmp_path, name="demo", test={"script": "test.sh"}) == []

    def test_missing_host_test_script_is_caught(self, tmp_path):
        errors = self._errors(tmp_path, name="demo", test={"script": "nope.sh"})
        assert any("test.script 'nope.sh' not found" in e for e in errors)

    def test_missing_guest_script_is_caught(self, tmp_path):
        errors = self._errors(tmp_path, kind="image", test={"vm": {"script": "vm-test.sh"}})
        assert any("test.vm.script 'vm-test.sh' not found" in e for e in errors)

    def test_image_self_on_a_non_image_recipe_is_caught(self, tmp_path):
        errors = self._errors(tmp_path, name="demo", test={"vm": {"connect": "agent"}})
        assert any("recipe.kind is not 'image'" in e for e in errors)

    def test_naming_another_image_is_allowed_on_any_recipe(self, tmp_path):
        errors = self._errors(
            tmp_path, name="demo", test={"vm": {"image": "haiku-image", "connect": "agent"}}
        )
        assert errors == []

    def test_a_complete_image_vm_block_validates(self, tmp_path):
        (tmp_path / "vm-test.sh").write_text("#!/bin/sh\n")
        errors = self._errors(
            tmp_path,
            kind="image",
            test={
                "vm": {
                    "requires_capabilities": ["incus"],
                    "hypervisors": ["incus"],
                    "script": "vm-test.sh",
                    "connect": "ssh",
                    "ssh": {"key_env": "K"},
                    "timeout_seconds": 600,
                }
            },
        )
        assert errors == []

    def test_the_schema_rejects_classic_lxc_and_unknown_keys(self, tmp_path):
        for bad in ({"hypervisors": ["lxc"]}, {"connect": "telnet"}, {"nope": 1}):
            errors = self._errors(tmp_path, kind="image", test={"vm": bad})
            assert errors, f"schema should have rejected test.vm: {bad}"


# ── 13. Teardown reporting ──────────────────────────────────────


class TestTeardownReporting:
    """A teardown step that fails must say so once, and not also say it worked."""

    def test_a_failed_instance_delete_is_not_also_reported_as_destroyed(self, image):
        lines: list[str] = []
        fake = FakeHypervisor(
            overrides={"delete": vmtest.CommandResult(("incus",), 1, "", "instance is busy")}
        )
        result = _run(image, fake, log=lines.append)
        assert any("WARNING: could not delete VM" in line for line in lines)
        assert not any("destroyed VM" in line for line in lines)
        assert result.leaked

    def test_a_failed_image_delete_is_reported_too(self, image):
        # A leaked image is a multi-gigabyte qcow2 sitting in the daemon's
        # store — as expensive as a leaked instance, and just as invisible.
        lines: list[str] = []
        fake = FakeHypervisor(
            overrides={"image-delete": vmtest.CommandResult(("incus",), 1, "", "in use")}
        )
        result = _run(image, fake, log=lines.append)
        assert any("could not delete image" in line for line in lines)
        assert result.leaked

    def test_the_image_is_deleted_after_the_instance(self, image):
        # A daemon refuses to delete an image an instance still uses, so the
        # reverse order would leak the image every single run.
        fake = FakeHypervisor()
        _run(image, fake)
        assert fake.kinds.index("delete") < fake.kinds.index("image-delete")

    def test_the_image_is_still_deleted_when_the_instance_delete_fails(self, image):
        fake = FakeHypervisor(overrides={"delete": vmtest.CommandResult(("incus",), 1, "", "busy")})
        _run(image, fake)
        assert "image-delete" in fake.kinds, "one failed step must not skip the other"

    def test_a_clean_teardown_reports_both(self, image):
        lines: list[str] = []
        _run(image, FakeHypervisor(), log=lines.append)
        assert any("destroyed VM cvcpkg-vmtest-" in line for line in lines)
        assert any("destroyed image cvcpkg-vmtest-" in line for line in lines)


# ── 14. The seam is actually wired into the build ───────────────


class TestBuildWiring:
    """The engine is worthless if nothing calls it.

    `run_vm_test` is easy to unit-test in isolation and equally easy to leave
    unreferenced — which would make every image recipe silently untested while
    every test in this file still passed.  These check the call sites exist.
    """

    def _build_source(self) -> str:
        import inspect

        from cvcpkg import builder

        return inspect.getsource(builder)

    def test_every_run_test_call_site_has_a_vm_sibling(self):
        import ast
        import inspect

        from cvcpkg import builder

        tree = ast.parse(inspect.getsource(builder))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "run_test" in called, "sanity: the host-side hook is still called"
        assert "run_vm_test" in called, "the VM hook is defined but never invoked"

    def test_the_vm_hook_runs_before_the_bundle_is_staged(self):
        # A guest that cannot boot must never become a published bundle, so the
        # VM test has to run before staging, not after.
        source = self._build_source()
        first_call = source.index("run_vm_test(ctx, log_callback=log_callback)")
        staging = source.index("if prefix is not None and prefix != install_dir")
        assert first_call < staging

    def test_pack_goes_through_the_hook(self):
        # `cvcpkg pack` is how an image becomes a publishable bundle, and it
        # delegates to build_recipe -- which is what puts the VM test on the
        # publish path, so a guest that cannot boot never becomes an artifact.
        import ast
        import inspect

        from cvcpkg import builder

        tree = ast.parse(inspect.getsource(builder))
        pack = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "pack_recipe"
        )
        calls = {
            n.func.id
            for n in ast.walk(pack)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "build_recipe" in calls
