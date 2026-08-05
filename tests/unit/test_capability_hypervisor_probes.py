"""Host probes for the container/VM capabilities (``incus``, ``lxd``, ``lxc``).

Two things are under test and both are load-bearing for fleet routing:

1. **Disambiguation.**  ``lxc`` is an overloaded name, and each capability is
   named for the PRODUCT it gates, never for a command that product happens to
   install:

   * ``lxd`` — LXD, whose CLI entry point is a binary literally named ``lxc``
     (``/usr/bin/lxc``).  The binary's *name* is not evidence of the *server*
     either: some distros ship an ``lxc`` compatibility shim that fronts Incus,
     so ``shutil.which("lxc")`` on an Incus-only host would advertise ``lxd``
     and fail every LXD job routed there.  The probe makes the client prove
     which implementation answered.
   * ``lxc`` — CLASSIC LXC (liblxc plus ``lxc-create`` / ``lxc-start`` /
     ``lxc-attach``), which ships NO plain ``lxc`` binary at all.  A naive
     ``which("lxc")`` therefore detects LXD and files it under "lxc", routing
     jobs written against ``lxc-create`` to a host that has never heard of it.
   * ``incus`` — Incus, whose ``incus`` binary is unambiguous.

2. **Usability, not presence.**  A builder outside the ``lxd`` /
   ``incus-admin`` group, or whose daemon is stopped, still has every binary on
   PATH.  A probe that stopped at ``which`` would advertise the capability and
   then burn every build sent to it.  Classic LXC has no daemon, so its
   equivalent gate is subuid/subgid delegation for an unprivileged user.

Everything is mocked: these pass on a host with none of the three installed,
and on one with all three.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from cvcpkg import platform as plat_mod

# Incus, LXD and classic LXC are Linux products, so this whole module simulates
# a Linux host -- the unprivileged-delegation cases monkeypatch ``os.geteuid``,
# which does not exist on Windows and so cannot be patched there.
#
# The PRODUCTION code is already right on Windows: platform.py reaches the
# delegation check through ``getattr(os, "geteuid", None)`` and skips it when
# absent, so the capability just reports "not present".  Nothing is being
# suppressed here -- there is no Windows behaviour to simulate, because the
# capability can never be available there.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="incus/lxd/lxc are Linux-only; this module simulates a Linux host",
)

# ── Fake host harness ───────────────────────────────────────────

# What `lxc info` / `incus info` print: a YAML dump whose `environment.server`
# names the implementation.  The neighbouring server_* keys are included
# because the probe's regex must not match them.
_LXD_INFO = """\
api_status: stable
api_version: "1.0"
auth: trusted
environment:
  architectures:
  - x86_64
  server: lxd
  server_clustered: false
  server_name: builder-01
  server_pid: 1234
  server_version: "5.21"
"""

_INCUS_INFO = _LXD_INFO.replace("server: lxd", "server: incus")


def _fake_host(
    monkeypatch,
    binaries: dict[str, str],
    *,
    exit_codes: dict[str, int] | None = None,
    stdout: dict[str, str] | None = None,
    daemon_paths: tuple[str, ...] = (),
    euid: int = 0,
):
    """Present a synthetic host to the probes.

    *binaries* maps a command name to its absolute path (absent = not
    installed); *exit_codes* maps a command's basename to the status it exits
    with (absent = 0); *stdout* maps a basename to the text it prints.
    *daemon_paths* is the set of absolute paths that "exist" for the LXD
    daemon-binary fallback.  *euid* defaults to root so the classic-LXC
    delegation gate (exercised separately) cannot confound other cases.
    Returns the argv lists the probes executed.
    """
    exit_codes = exit_codes or {}
    stdout = stdout or {}
    executed: list[list[str]] = []

    def _which(name: str) -> str | None:
        return binaries.get(name)

    def _run(argv, **kwargs):
        executed.append(list(argv))
        name = str(argv[0]).rsplit("/", 1)[-1]
        return subprocess.CompletedProcess(
            argv, exit_codes.get(name, 0), stdout=stdout.get(name, "")
        )

    monkeypatch.setattr("shutil.which", _which)
    monkeypatch.setattr("subprocess.run", _run)
    monkeypatch.setattr(plat_mod, "_LXD_DAEMON_PATHS", daemon_paths)
    monkeypatch.setattr("os.path.exists", lambda p: p in daemon_paths)
    monkeypatch.setattr("os.geteuid", lambda: euid)
    return executed


#: A real LXD host: the client is `lxc`, the daemon binary is `lxd`.
_LXD_HOST = {"lxc": "/usr/bin/lxc", "lxd": "/usr/sbin/lxd"}
#: An Incus host that also ships an `lxc` compatibility shim.
_INCUS_HOST_WITH_SHIM = {"incus": "/usr/bin/incus", "lxc": "/usr/bin/lxc"}
_INCUS_HOST = {"incus": "/usr/bin/incus"}
#: Classic LXC: hyphenated tools only, and NO plain `lxc`.
_CLASSIC_LXC_HOST = {
    "lxc-create": "/usr/bin/lxc-create",
    "lxc-start": "/usr/bin/lxc-start",
    "lxc-attach": "/usr/bin/lxc-attach",
    "lxc-ls": "/usr/bin/lxc-ls",
}


# ── The three are told apart ────────────────────────────────────


class TestDisambiguation:
    def test_lxd_only_host(self, monkeypatch):
        """LXD installed: `lxc` IS on PATH, and it is NOT classic LXC."""
        _fake_host(monkeypatch, _LXD_HOST, stdout={"lxc": _LXD_INFO})
        assert plat_mod._probe_lxd() is True
        assert plat_mod._probe_incus() is False
        # The whole point: LXD's client must not read as classic LXC.
        assert plat_mod._probe_lxc() is False

    def test_incus_only_host(self, monkeypatch):
        _fake_host(monkeypatch, _INCUS_HOST, stdout={"incus": _INCUS_INFO})
        assert plat_mod._probe_incus() is True
        assert plat_mod._probe_lxd() is False
        assert plat_mod._probe_lxc() is False

    def test_incus_compat_shim_is_not_lxd(self, monkeypatch):
        """The trap: an `lxc` on PATH that actually drives Incus.

        `which("lxc")` succeeds and `lxc info` exits 0, so presence and
        usability both say yes — only the reported server says no.
        """
        _fake_host(
            monkeypatch,
            _INCUS_HOST_WITH_SHIM,
            stdout={"lxc": _INCUS_INFO, "incus": _INCUS_INFO},
        )
        assert plat_mod._probe_incus() is True
        assert plat_mod._probe_lxd() is False
        assert plat_mod._probe_lxc() is False

    def test_classic_lxc_only_host(self, monkeypatch):
        """Classic LXC: the lxc-* tools exist and a plain `lxc` does not."""
        _fake_host(monkeypatch, _CLASSIC_LXC_HOST)
        assert plat_mod._probe_lxc() is True
        assert plat_mod._probe_lxd() is False
        assert plat_mod._probe_incus() is False

    def test_bare_host(self, monkeypatch):
        _fake_host(monkeypatch, {})
        assert plat_mod._probe_incus() is False
        assert plat_mod._probe_lxd() is False
        assert plat_mod._probe_lxc() is False

    def test_both_managers_coexist(self, monkeypatch):
        """Incus and LXD on one host advertise both capabilities."""
        _fake_host(
            monkeypatch,
            {**_INCUS_HOST, **_LXD_HOST},
            stdout={"lxc": _LXD_INFO, "incus": _INCUS_INFO},
            daemon_paths=("/usr/sbin/lxd",),
        )
        assert plat_mod._probe_incus() is True
        assert plat_mod._probe_lxd() is True
        assert plat_mod._probe_lxc() is False

    def test_classic_lxc_needs_the_whole_driver_pair(self, monkeypatch):
        """liblxc's listing tool alone cannot boot anything."""
        _fake_host(monkeypatch, {"lxc-ls": "/usr/bin/lxc-ls"})
        assert plat_mod._probe_lxc() is False

    def test_classic_lxc_partial_install_without_lxc_ls(self, monkeypatch):
        _fake_host(
            monkeypatch,
            {"lxc-create": "/usr/bin/lxc-create", "lxc-start": "/usr/bin/lxc-start"},
        )
        assert plat_mod._probe_lxc() is False


class TestServerDiscrimination:
    def test_unrecognised_output_falls_back_to_the_daemon_binary(self, monkeypatch):
        """Output shape changed upstream: LXD's own daemon still settles it."""
        _fake_host(
            monkeypatch,
            {"lxc": "/usr/bin/lxc"},
            stdout={"lxc": "some future format with no server key\n"},
            daemon_paths=("/usr/sbin/lxd",),
        )
        assert plat_mod._probe_lxd() is True

    def test_unrecognised_output_without_the_daemon_is_not_lxd(self, monkeypatch):
        _fake_host(
            monkeypatch,
            {"lxc": "/usr/bin/lxc"},
            stdout={"lxc": "some future format with no server key\n"},
        )
        assert plat_mod._probe_lxd() is False

    def test_daemon_found_via_path_lookup(self, monkeypatch):
        """`lxd` lives in /usr/sbin, which is off a non-root PATH."""
        _fake_host(
            monkeypatch,
            {"lxc": "/usr/bin/lxc"},  # no "lxd" entry -> which() misses it
            stdout={"lxc": "no server key\n"},
            daemon_paths=("/usr/sbin/lxd",),
        )
        assert plat_mod._lxd_daemon_present() is True
        assert plat_mod._probe_lxd() is True

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("  server: lxd\n", "lxd"),
            ("  server: incus\n", "incus"),
            ('  server: "lxd"\n', "lxd"),
            ("  server_name: lxd\n", ""),  # must not match a sibling key
            ("  server_version: lxd\n", ""),
            ("", ""),
        ],
    )
    def test_reported_server_parsing(self, text, expected):
        assert plat_mod._reported_server(text) == expected


# ── Presence is not usability ───────────────────────────────────


class TestDaemonUsability:
    def test_incus_binary_present_daemon_unusable(self, monkeypatch):
        """`incus` installed but incusd down / user not in incus-admin."""
        executed = _fake_host(monkeypatch, _INCUS_HOST, exit_codes={"incus": 1})
        assert plat_mod._probe_incus() is False
        assert executed == [["/usr/bin/incus", "info"]]

    def test_lxd_binary_present_daemon_unusable(self, monkeypatch):
        """`lxc` and `lxd` both installed but the user is not in the lxd group.

        The daemon-binary fallback must NOT rescue this: it only disambiguates
        WHICH server answered, it never substitutes for one answering.
        """
        executed = _fake_host(
            monkeypatch,
            _LXD_HOST,
            exit_codes={"lxc": 1},
            daemon_paths=("/usr/sbin/lxd",),
        )
        assert plat_mod._probe_lxd() is False
        assert executed == [["/usr/bin/lxc", "info"]]

    def test_classic_lxc_tools_present_but_unusable(self, monkeypatch):
        """Everything installed, but liblxc cannot read its config/lxcpath."""
        executed = _fake_host(monkeypatch, _CLASSIC_LXC_HOST, exit_codes={"lxc-ls": 1})
        assert plat_mod._probe_lxc() is False
        assert executed == [["/usr/bin/lxc-ls", "-1"]]

    def test_probe_commands_are_read_only_and_bounded(self, monkeypatch):
        """Every probe command must be non-mutating, silent on stdin, bounded."""
        seen: list[dict] = []

        def _run(argv, **kwargs):
            seen.append({"argv": list(argv), **kwargs})
            name = str(argv[0]).rsplit("/", 1)[-1]
            return subprocess.CompletedProcess(
                argv, 0, stdout=_LXD_INFO if name == "lxc" else _INCUS_INFO
            )

        monkeypatch.setattr("subprocess.run", _run)
        monkeypatch.setattr("os.geteuid", lambda: 0)
        monkeypatch.setattr(
            "shutil.which",
            lambda n: {**_INCUS_HOST, **_LXD_HOST, **_CLASSIC_LXC_HOST}.get(n),
        )
        assert plat_mod._probe_incus() is True
        assert plat_mod._probe_lxd() is True
        assert plat_mod._probe_lxc() is True
        assert len(seen) == 3
        for call in seen:
            # `info` (incus/lxd) and `lxc-ls -1` are both read-only listings.
            assert call["argv"][1:] in (["info"], ["-1"]), call
            assert call["timeout"] == plat_mod._PROBE_TIMEOUT_SECONDS
            # Never inherit the caller's stdin: a probe must not be able to
            # block on a prompt.
            assert call["stdin"] is subprocess.DEVNULL
            # stderr is always discarded; stdout is captured only where the
            # probe needs to read which server answered.
            assert call["stderr"] is subprocess.DEVNULL
            assert call["stdout"] in (subprocess.DEVNULL, subprocess.PIPE)

    def test_hung_daemon_times_out_to_absent(self, monkeypatch):
        """A wedged daemon degrades to 'no capability', it does not hang."""

        def _run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

        monkeypatch.setattr("subprocess.run", _run)
        monkeypatch.setattr("os.geteuid", lambda: 0)
        monkeypatch.setattr(
            "shutil.which",
            lambda n: {**_INCUS_HOST, **_LXD_HOST, **_CLASSIC_LXC_HOST}.get(n),
        )
        monkeypatch.setattr(plat_mod, "_LXD_DAEMON_PATHS", ("/usr/sbin/lxd",))
        monkeypatch.setattr("os.path.exists", lambda p: True)
        assert plat_mod._probe_incus() is False
        assert plat_mod._probe_lxd() is False
        assert plat_mod._probe_lxc() is False


# ── Classic LXC: unprivileged delegation ────────────────────────


class TestClassicLxcDelegation:
    """No daemon socket exists, so subuid/subgid stands in for group access.

    Without subordinate id ranges an unprivileged ``lxc-start`` cannot create
    the user namespace: every binary is present and every container fails.
    """

    def _unprivileged(self, monkeypatch):
        _fake_host(monkeypatch, _CLASSIC_LXC_HOST, euid=1000)
        monkeypatch.setattr("os.getuid", lambda: 1000)
        monkeypatch.setattr("getpass.getuser", lambda: "builder")

    def _subid(self, tmp_path, monkeypatch, content: str, *, both: bool = True):
        subuid = tmp_path / "subuid"
        subgid = tmp_path / "subgid"
        subuid.write_text(content)
        subgid.write_text(content if both else "")
        monkeypatch.setattr(plat_mod, "_SUBID_FILES", (str(subuid), str(subgid)))

    def test_root_needs_no_delegation(self, tmp_path, monkeypatch):
        _fake_host(monkeypatch, _CLASSIC_LXC_HOST, euid=0)
        monkeypatch.setattr(plat_mod, "_SUBID_FILES", (str(tmp_path / "nope"),))
        assert plat_mod._probe_lxc() is True

    def test_unprivileged_without_delegation(self, tmp_path, monkeypatch):
        self._unprivileged(monkeypatch)
        self._subid(tmp_path, monkeypatch, "someoneelse:100000:65536\n")
        assert plat_mod._probe_lxc() is False

    def test_unprivileged_with_delegation_by_name(self, tmp_path, monkeypatch):
        self._unprivileged(monkeypatch)
        self._subid(tmp_path, monkeypatch, "root:100000:65536\nbuilder:165536:65536\n")
        assert plat_mod._probe_lxc() is True

    def test_unprivileged_with_delegation_by_uid(self, tmp_path, monkeypatch):
        # /etc/subuid may key on the numeric uid; getpass.getuser() can also
        # fail outright in a container with no passwd entry.
        self._unprivileged(monkeypatch)
        monkeypatch.setattr("getpass.getuser", lambda: (_ for _ in ()).throw(OSError("no pwent")))
        self._subid(tmp_path, monkeypatch, "1000:165536:65536\n")
        assert plat_mod._probe_lxc() is True

    def test_unprivileged_with_subuid_but_no_subgid(self, tmp_path, monkeypatch):
        self._unprivileged(monkeypatch)
        self._subid(tmp_path, monkeypatch, "builder:165536:65536\n", both=False)
        assert plat_mod._probe_lxc() is False

    def test_missing_subid_files(self, tmp_path, monkeypatch):
        self._unprivileged(monkeypatch)
        monkeypatch.setattr(plat_mod, "_SUBID_FILES", (str(tmp_path / "absent"),))
        assert plat_mod._probe_lxc() is False


# ── Never raises ────────────────────────────────────────────────


class TestProbesNeverRaise:
    @pytest.mark.parametrize("probe", ["_probe_incus", "_probe_lxd", "_probe_lxc"])
    def test_which_exploding(self, monkeypatch, probe):
        def _boom(_name):
            raise OSError("PATH is on fire")

        monkeypatch.setattr("shutil.which", _boom)
        assert getattr(plat_mod, probe)() is False

    @pytest.mark.parametrize("probe", ["_probe_incus", "_probe_lxd", "_probe_lxc"])
    def test_subprocess_exploding(self, monkeypatch, probe):
        binaries = {**_INCUS_HOST, **_LXD_HOST, **_CLASSIC_LXC_HOST}
        monkeypatch.setattr("shutil.which", lambda n: binaries.get(n))
        monkeypatch.setattr("os.geteuid", lambda: 0)

        def _boom(*_a, **_k):
            raise PermissionError("exec denied")

        monkeypatch.setattr("subprocess.run", _boom)
        assert getattr(plat_mod, probe)() is False

    def test_daemon_path_stat_exploding(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _n: None)
        monkeypatch.setattr(plat_mod, "_LXD_DAEMON_PATHS", ("/usr/sbin/lxd",))

        def _boom(_p):
            raise OSError("stat denied")

        monkeypatch.setattr("os.path.exists", _boom)
        assert plat_mod._lxd_daemon_present() is False

    def test_unreadable_subid_file(self, monkeypatch, tmp_path):
        """A root-owned /etc/subuid the builder cannot read is just 'no'."""
        monkeypatch.setattr(plat_mod, "_SUBID_FILES", (str(tmp_path),))  # a dir
        assert plat_mod._has_subid_delegation() is False


# ── Registry wiring + env override ──────────────────────────────


class TestRegistry:
    def test_probes_registered_under_the_right_names(self):
        probes = plat_mod._CAPABILITY_PROBES
        assert probes["incus"] is plat_mod._probe_incus
        assert probes["lxd"] is plat_mod._probe_lxd
        assert probes["lxc"] is plat_mod._probe_lxc

    def test_lxc_is_classic_lxc_not_lxd(self):
        """The conflation this unit exists to prevent, asserted directly.

        ``lxc`` is LXD's COMMAND but classic LXC's PRODUCT.  The capability
        name follows the product, so ``lxc`` must be backed by the classic-LXC
        probe and ``lxd`` by the LXD one — never the same function, and never
        swapped.
        """
        probes = plat_mod._CAPABILITY_PROBES
        assert probes["lxc"] is not probes["lxd"]
        assert probes["lxc"] is not probes["incus"]

    def test_names_are_declarable_in_a_recipe(self):
        """Every capability name must satisfy the recipe schema's pattern."""
        import pathlib

        import yaml

        schema = yaml.safe_load(
            (pathlib.Path(plat_mod.__file__).parent / "schemas" / "recipe-schema.yaml").read_text()
        )
        pattern = schema["properties"]["requires_capabilities"]["items"]["pattern"]
        for name in plat_mod._CAPABILITY_PROBES:
            assert re.match(pattern, name), f"{name} cannot appear in requires_capabilities"

    def _hypervisor_probes(self, monkeypatch):
        monkeypatch.setattr(
            plat_mod,
            "_CAPABILITY_PROBES",
            {
                "cuda": lambda: False,
                "incus": plat_mod._probe_incus,
                "lxd": plat_mod._probe_lxd,
                "lxc": plat_mod._probe_lxc,
            },
        )

    def test_host_capabilities_on_an_lxd_host(self, monkeypatch):
        """An LXD host advertises exactly ``lxd`` — never ``lxc``."""
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)
        _fake_host(
            monkeypatch, _LXD_HOST, stdout={"lxc": _LXD_INFO}, daemon_paths=("/usr/sbin/lxd",)
        )
        self._hypervisor_probes(monkeypatch)
        assert plat_mod.host_capabilities() == {"lxd"}

    def test_host_capabilities_on_an_incus_host(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)
        _fake_host(monkeypatch, _INCUS_HOST_WITH_SHIM, stdout={"lxc": _INCUS_INFO, "incus": ""})
        self._hypervisor_probes(monkeypatch)
        assert plat_mod.host_capabilities() == {"incus"}

    def test_host_capabilities_on_a_classic_lxc_host(self, monkeypatch):
        """A classic-LXC host advertises exactly ``lxc`` — never ``lxd``."""
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)
        _fake_host(monkeypatch, _CLASSIC_LXC_HOST)
        self._hypervisor_probes(monkeypatch)
        assert plat_mod.host_capabilities() == {"lxc"}

    def test_a_probe_that_raises_does_not_break_the_others(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)

        def _boom() -> bool:
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(plat_mod, "_CAPABILITY_PROBES", {"cuda": _boom, "incus": lambda: True})
        assert plat_mod.host_capabilities() == {"incus"}

    def test_env_override_wins_over_probes(self, monkeypatch):
        """CVCPKG_CAPABILITIES is authoritative and re-read every call."""
        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)
        # A host where every probe would say yes...
        _fake_host(
            monkeypatch,
            {**_INCUS_HOST, **_LXD_HOST, **_CLASSIC_LXC_HOST},
            stdout={"lxc": _LXD_INFO, "incus": _INCUS_INFO},
            daemon_paths=("/usr/sbin/lxd",),
        )
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "incus")
        assert plat_mod.host_capabilities() == {"incus"}
        # ...and the override can also subtract everything.
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        assert plat_mod.host_capabilities() == set()
        # ...or add a capability with no probe at all (fleet-injected).
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "lxd, incus ,qemu")
        assert plat_mod.host_capabilities() == {"lxd", "incus", "qemu"}
