# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Auto-detect platform, architecture, libc, and CRT metadata."""

from __future__ import annotations

import os
import platform
import re
import struct
import sys
from collections.abc import Sequence

# Canonical vocabularies for the package keyspace. The catalog is keyed by
# these exact strings; every ingestion boundary (CLI detection, composite
# actions, server publish endpoints) must normalize to them. Raw machine
# names are per-OS-inconsistent (Linux 'aarch64' vs macOS 'arm64', BSD
# 'amd64' vs 'x86_64'), so the canonical set — not uname output — is the
# single source of truth.
CANONICAL_PLATFORMS = frozenset(
    {
        "linux",
        "macos",
        "windows",
        "windows-gnu",
        "freebsd",
        "openbsd",
        "netbsd",
        "haiku",
        "wasm",
        # Threaded wasm (emscripten -pthread) is its own platform, not a
        # config of "wasm": every object is compiled with atomics+shared
        # memory and wasm-ld refuses to mix the two worlds ("--shared-memory
        # is disallowed" linking a single-threaded archive into a -pthread
        # build).  One OS, two incompatible ABIs, two catalog keys — the
        # windows / windows-gnu precedent.
        "wasm-mt",
        "wasi",
        "cosmo",
        "any",  # platform-independent bundles (builder.py 'any' fallback)
    }
)

CANONICAL_ARCHES = frozenset(
    {
        "x86_64",
        "arm64",
        "riscv64",
        "ppc64le",
        "ppc64",
        "s390x",
        "wasm32",
        # Platform-independent (noarch) bundles. A `platform: any` recipe is
        # packaged and published as platform=any / arch=noarch (see
        # builder._detect_arch_for_platform + pack_recipe), so "noarch" must be
        # a canonical arch or the publish endpoint 422s every noarch bundle.
        # "any" is kept as a historical alias some callers still emit.
        "noarch",
        "any",
    }
)

# Common non-canonical spellings -> canonical (mirrors detect_arch's map).
ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "aarch64": "arm64",
}


def noarch_build_target() -> tuple[str, str]:
    """The concrete ``(platform, arch)`` a noarch job is *built* on.

    A ``platform: any`` recipe publishes one noarch bundle valid everywhere, but
    it still has to compile on a real host that provides its build deps — for a
    Python wheel, the CPython interpreter recipes, which are published for one
    reference platform only.  Dispatching a noarch job to a host that lacks that
    interpreter fails the build (and, with no cross-builder retry, cascade-
    cancels the whole noarch DAG), so the scheduler routes noarch jobs to a
    builder on this target.  Defaults to ``linux``/``x86_64``; override per
    fleet with ``CVCPKG_NOARCH_BUILD_PLATFORM`` / ``CVCPKG_NOARCH_BUILD_ARCH``.
    """
    return (
        os.environ.get("CVCPKG_NOARCH_BUILD_PLATFORM", "linux"),
        os.environ.get("CVCPKG_NOARCH_BUILD_ARCH", "x86_64"),
    )


PLATFORM_ALIASES = {"win": "windows"}


def served_by_any_entry(matrix_platforms: Sequence[str], target: str) -> bool:
    """True when a build for *target* is driven by a recipe's ``platform: any`` entry.

    A recipe's matrix maps target platforms to build scripts, and ``any`` is the
    fallback for every target without an entry of its own.  That fallback is
    also what decides the bundle's *identity*: targets served by it share one
    noarch bundle, while a target with its own entry gets a bundle tagged for it.

    Deriving this per target — rather than from the recipe as a whole ("every
    entry is ``any``") — is load-bearing.  Pure-Python columns carry an ``any``
    entry for ``build.sh`` plus a ``windows`` entry for ``build.ps1`` (Windows
    needs PowerShell and installs to ``Lib/site-packages`` rather than
    ``lib/pythonX.Y/site-packages``).  Under the whole-recipe test that second
    entry silently re-tagged the *other* platforms' bundles as host-specific, so
    one noarch bundle valid everywhere became a linux-only bundle plus a stale
    noarch one, and macOS/BSD quietly resolved the older revision forever.
    """
    plats = {PLATFORM_ALIASES.get(p, p) for p in matrix_platforms if p}
    if not plats:
        return False
    if "any" not in plats:
        return False
    norm_target = PLATFORM_ALIASES.get(target, target)
    # An explicit request to build "any" is the noarch build itself.
    if not norm_target or norm_target == "any":
        return True
    # A target with its own entry is built by it, not by the fallback.
    return norm_target not in plats


def platform_matches(bundle_platform: str, requested: str) -> bool:
    """True when a bundle's ``platform`` satisfies a host's *requested* platform.

    A platform-independent bundle (``platform: any``) is valid on every host, so
    it matches any concrete request; a concrete bundle matches only its own
    platform.  An empty *requested* means "no filter" and matches everything.
    Without this, a host querying ``linux`` would filter out a noarch bundle
    (``any``) and could never resolve/install it.
    """
    return not requested or bundle_platform == requested or bundle_platform == "any"


def arch_matches(bundle_arch: str, requested: str) -> bool:
    """True when a bundle's ``arch`` satisfies a host's *requested* arch.

    ``noarch`` (the arch of a ``platform: any`` bundle) is valid on every arch,
    so it matches any concrete request; a concrete bundle matches only its own
    arch.  An empty *requested* means "no filter".
    """
    return not requested or bundle_arch == requested or bundle_arch == "noarch"


def normalize_arch(value: str) -> str:
    """Map a possibly-raw arch spelling onto the canonical name."""
    v = (value or "").strip().lower()
    return ARCH_ALIASES.get(v, v)


def detect_platform() -> str:
    """Return the canonical platform tag for the current host.

    One of 'linux', 'macos', 'windows', 'freebsd', 'openbsd', 'netbsd',
    or 'haiku' (DragonFly BSD detects as 'freebsd' — see below).

    GhostBSD note: GhostBSD's kernel identifies as FreeBSD (sys.platform
    is ``freebsd*``), so GhostBSD hosts intentionally detect as
    ``freebsd`` and consume the freebsd package channel (compat mode).

    DragonFly BSD is handled the same way, deliberately.  It used to be its own
    canonical platform, but the recipe schema's platform enum never included it,
    so no recipe could declare a ``dragonflybsd`` build and such a host resolved
    exactly zero packages while the UI advertised the platform.  Rather than
    stand up a whole builder VM and env helper for it, DragonFly consumes the
    freebsd channel in compat mode.  If that ABI compatibility ever proves
    insufficient in practice, the honest fix is to fail here rather than to
    reinstate a platform with no packages behind it.
    """
    s = sys.platform
    if s.startswith("linux"):
        return "linux"
    if s == "darwin":
        return "macos"
    if s in ("win32", "cygwin"):
        return "windows"
    if s.startswith("freebsd"):
        return "freebsd"
    if s.startswith("openbsd"):
        return "openbsd"
    if s.startswith("netbsd"):
        return "netbsd"
    if s.startswith("dragonfly"):
        return "freebsd"
    if s.startswith("haiku"):
        # Haiku's sys.platform is tri-valued and depends on which CPython the
        # host happens to run: upstream CPython reports "haiku1" (the
        # MACHDEP autoconf default appends the major version), HaikuPorts'
        # python3 carries a MACHDEP patch that reports the bare "haiku", and
        # some builds report the full ABI string "haikuR1~beta5".  Prefix
        # matching is the only spelling-independent test — never compare ==.
        return "haiku"
    raise RuntimeError(f"unsupported platform: {s}")


def detect_arch() -> str:
    """Return a normalised architecture name for the current host."""
    machine = platform.machine().lower()
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "riscv64": "riscv64",
        "ppc64le": "ppc64le",
        "ppc64": "ppc64",
        "s390x": "s390x",
    }
    arch = arch_map.get(machine)
    if arch:
        return arch
    # Accept any machine string rather than crashing on new architectures.
    # (Haiku x86_64 reports "x86_64" and needs no special case; only its
    # 32-bit port reports the legacy "BePC", and 32-bit x86 is not a
    # canonical arch here — no Haiku x86 builder exists.)
    return machine


def lib_path_var(plat: str) -> str:
    """Return the run-time linker's library-search environment variable.

    Every POSIX platform we build for uses ``LD_LIBRARY_PATH`` except two:
    macOS's dyld reads ``DYLD_LIBRARY_PATH``, and Haiku's ``runtime_loader``
    reads ``LIBRARY_PATH`` (it ignores ``LD_LIBRARY_PATH`` entirely — exporting
    the wrong name there fails silently, and the build only dies later with an
    unresolvable symbol from a dependency the prefix definitely contains).

    Windows has no such variable (DLLs resolve from ``PATH``), so callers that
    care handle it separately; this returns the POSIX default for it.
    """
    if plat == "macos":
        return "DYLD_LIBRARY_PATH"
    if plat == "haiku":
        return "LIBRARY_PATH"
    return "LD_LIBRARY_PATH"


def detect_pointer_size() -> int:
    """Pointer width in bits (32 or 64)."""
    return struct.calcsize("P") * 8


def detect_libc() -> str:
    """Best-effort libc identifier for the current host."""
    plat = detect_platform()
    if plat == "windows":
        return "msvcrt"
    if plat == "macos":
        return "libc++ABI"
    if plat == "freebsd":
        return "freebsd-libc"
    if plat == "openbsd":
        return "openbsd-libc"
    if plat == "netbsd":
        return "netbsd-libc"
    if plat == "haiku":
        # Haiku's C library is libroot.so — there is no libc.so.6, so without
        # this branch Haiku fell through to the Linux probe below, the
        # ctypes.CDLL("libc.so.6") raised OSError, and every Haiku host was
        # mislabelled "musl" in the ABI tuple.
        return "haiku-libroot"
    # Linux: try to distinguish glibc vs musl.
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        gnu_get_libc_version = libc.gnu_get_libc_version
        gnu_get_libc_version.restype = ctypes.c_char_p
        ver = gnu_get_libc_version().decode()
        return f"glibc-{ver}"
    except (OSError, AttributeError):
        return "musl"


def default_tuple() -> dict[str, str]:
    """Return the default (platform, arch) tuple for the current host."""
    return {
        "platform": detect_platform(),
        "arch": detect_arch(),
    }


# ── Host capabilities ───────────────────────────────────────────
#
# A "capability" is an opt-in host feature (e.g. ``cuda``) that gates which
# concrete provider of a virtual package the resolver may select.  A package
# that declares ``requires_capabilities: [cuda]`` is only installable on a host
# whose capability set contains ``cuda``.  New capabilities plug in by adding a
# probe function to ``_CAPABILITY_PROBES`` below.

# Cache only the (potentially expensive) host probe.  The CVCPKG_CAPABILITIES
# override is cheap and authoritative, so it is re-read on every call rather
# than cached — this keeps CI and tests, which inject via the environment,
# free of cross-test state leakage.
_probed_capabilities: set[str] | None = None


def _probe_cuda() -> bool:
    """Detect a CUDA stack that can COMPILE.  Never raises.

    The capability gates *builds* (recipes declare ``requires_capabilities:
    [cuda]``), so the question is "can this host compile CUDA code?", not "does
    this host have a GPU?".  Only nvcc answers that.

    This deliberately does NOT accept ``nvidia-smi`` or a loadable libcuda:
    both ship with the *driver*, so any machine with an NVIDIA GPU advertised
    the capability and then failed at compile time once a job was routed to it.
    That is exactly what sandipaws did — an RTX 3050 Ti laptop with the driver,
    Visual Studio 2022 and no CUDA Toolkit — and because it is the only
    windows+cuda builder, every windows CUDA job would have been routed to the
    one host guaranteed to fail it.  A missing capability leaves a job queued
    (visible, diagnosable); a falsely advertised one burns a build and reports
    a confusing compiler error.

    The runtime/driver signals are still worth having, but as a SEPARATE
    capability (e.g. ``cuda-runtime``) for recipes that need to *execute* CUDA
    during a build — add that probe when a recipe actually needs it rather than
    conflating the two here.
    """
    import shutil
    from pathlib import Path

    try:
        if shutil.which("nvcc"):
            return True
    except Exception:
        pass
    try:
        # Windows CUDA toolkit installs set CUDA_PATH but a service account
        # (schtasks SYSTEM builder) may not have %CUDA_PATH%\bin on PATH, so
        # shutil.which() misses nvcc there.  Same variable is honoured on any
        # platform for a non-PATH toolkit install.
        cuda_home = os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")
        if cuda_home:
            bin_dir = Path(cuda_home) / "bin"
            if (bin_dir / "nvcc").exists() or (bin_dir / "nvcc.exe").exists():
                return True
    except Exception:
        pass
    # Deliberately no /usr/local/cuda* filesystem glob: it cannot be mocked the
    # way PATH and the env vars can, so it would make this probe depend on real
    # host state and fail test_no_cuda_anywhere on any machine with a toolkit
    # installed at the default prefix. A toolkit there but absent from both PATH
    # and CUDA_PATH/CUDA_HOME is a misconfigured host — set CUDA_HOME (or
    # CVCPKG_CAPABILITIES) rather than widening detection.
    return False


# ── Container / VM manager capabilities ─────────────────────────
#
# These gate jobs that boot an *ephemeral* machine — e.g. running a recipe's
# tests against the image it just built.  THREE capabilities, named for the
# PRODUCT each one gates, never for whatever command that product happens to
# install:
#
#   incus  Incus (the community fork of LXD).  Client binary ``incus``, daemon
#          ``incusd`` on /var/lib/incus/unix.socket, admin group
#          ``incus-admin``.  REST-API manager; can boot real VMs (``--vm``).
#   lxd    LXD.  Its CLI entry point is a binary literally named ``lxc``
#          (/usr/bin/lxc), its daemon is ``lxd`` on /var/lib/lxd/unix.socket,
#          and its admin group is ``lxd``.  Also a REST-API manager with VMs.
#   lxc    CLASSIC LXC — liblxc plus the hyphenated ``lxc-create`` /
#          ``lxc-start`` / ``lxc-attach`` tools, and NO plain ``lxc`` binary
#          at all.  Daemonless (containers are children of the invoking
#          process) and containers only, never VMs.
#
# The three are NOT interchangeable and must never be collapsed: the
# incus/LXD REST CLIs and the classic ``lxc-*`` CLI share no command surface
# (``lxc launch img c1`` vs ``lxc-create -n c1 -t download``), so a job's
# harness is written against exactly one of them.  A recipe declaring
# ``requires_capabilities: [lxc]`` means the classic tools and nothing else;
# LXD is spelled ``lxd`` precisely so that reading stays unambiguous.
#
# ``shutil.which("lxc")`` on its own is the wrong probe, in every direction:
#
#   * it MIS-attributes.  The plain ``lxc`` binary is LXD's client, so a naive
#     which("lxc") detects LXD and files it under "lxc" — routing classic-LXC
#     jobs to a host that has never heard of ``lxc-create``.
#   * it OVER-matches.  Some distros ship an ``lxc`` compatibility shim that
#     actually drives Incus, so a bare ``which`` would label an Incus-only
#     host "lxd" and route LXD jobs to a daemon that is not running there.
#     The probe therefore makes the client prove which server answered.
#   * it UNDER-describes.  The binary existing says nothing about whether this
#     user can reach the socket, which is what a job actually needs.
#
# Every probe answers "can THIS user drive it RIGHT NOW", not "is a binary
# installed": a builder whose account is outside the ``incus-admin``/``lxd``
# group, or whose daemon is stopped, has the binary and fails every job it is
# sent.  Same doctrine as _probe_cuda — an unadvertised capability leaves a job
# queued (visible, diagnosable), a falsely advertised one burns a build.

# Seconds a capability probe may spend in a subprocess.  Probes run on the
# builder's startup path and on the install-side resolve path, so a wedged
# daemon must degrade to "capability absent" fast instead of hanging the CLI.
_PROBE_TIMEOUT_SECONDS = 5.0

# Where LXD's DAEMON binary lives.  This is the positive marker that separates
# LXD from Incus: Incus's daemon is ``incusd``, so an ``lxd`` executable is
# never Incus.  /usr/sbin and /usr/lib are absent from a non-root PATH, so
# these are checked explicitly rather than through which().  Module-level so
# tests can point it at a fixture instead of the host's real filesystem.
_LXD_DAEMON_PATHS = (
    "/usr/sbin/lxd",
    "/usr/lib/lxd/lxd",
    "/usr/local/sbin/lxd",
    "/snap/bin/lxd",
)

# `lxc info` / `incus info` dump the server's environment as YAML; the
# `server:` key names the implementation ("lxd" / "incus").  Anchored so the
# neighbouring server_name/server_pid/server_version keys cannot match.
_SERVER_KEY_RE = re.compile(r"^[ \t]*server:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

# Files granting a user the subordinate uid/gid ranges an *unprivileged*
# classic-LXC container needs.  Module-level so tests can point them at a
# fixture instead of the host's real /etc.
_SUBID_FILES = ("/etc/subuid", "/etc/subgid")


def _which(name: str) -> str | None:
    """``shutil.which`` that never raises."""
    import shutil

    try:
        return shutil.which(name)
    except Exception:
        return None


def _command_ok(argv: list[str]) -> bool:
    """True when *argv* runs and exits 0 within :data:`_PROBE_TIMEOUT_SECONDS`.

    Never raises: a missing binary, a permission error, a hung daemon (timeout)
    and a non-zero exit are all just "no".  stdin is /dev/null so a probe can
    never block waiting for input, and both output streams are discarded so a
    chatty tool cannot fill a pipe buffer and deadlock.
    """
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0


def _command_output(argv: list[str]) -> str | None:
    """stdout of *argv* when it exits 0, else ``None``.  Never raises.

    Same bounds as :func:`_command_ok` (no stdin, hard timeout); stdout is
    captured instead of discarded because a probe may need to read WHICH
    implementation answered, not just that something did.
    """
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout
    if isinstance(out, bytes):
        return out.decode("utf-8", "replace")
    return out or ""


def _reported_server(text: str) -> str:
    """The implementation name an ``info`` dump reports, or ``""``."""
    match = _SERVER_KEY_RE.search(text or "")
    return match.group(1).strip().strip("\"'").lower() if match else ""


def _probe_incus() -> bool:
    """Detect a *usable* Incus daemon.  Never raises.

    ``incus info`` (no argument) is a read-only round-trip to the daemon over
    its unix socket, so exit 0 proves all three things a job needs at once:
    the client exists, incusd is running, and this user's group membership
    lets it open the socket.  ``shutil.which("incus")`` alone proves none of
    them — the client package installs cleanly on a host with no daemon.

    The binary name is unambiguous (nothing else ships ``incus``), so unlike
    :func:`_probe_lxd` this needs no implementation cross-check.
    """
    exe = _which("incus")
    if not exe:
        return False
    return _command_ok([exe, "info"])


def _lxd_daemon_present() -> bool:
    """True when LXD's own daemon binary is installed.  Never raises.

    Incus's daemon is ``incusd``, so finding ``lxd`` rules Incus out.  Used
    only as the fallback discriminator when the ``info`` dump does not name
    its server in the expected shape.
    """
    if _which("lxd"):
        return True
    for candidate in _LXD_DAEMON_PATHS:
        try:
            if os.path.exists(candidate):
                return True
        except Exception:
            continue
    return False


def _probe_lxd() -> bool:
    """Detect a *usable* LXD daemon.  Never raises.

    Two questions, in order, because the client binary's NAME is not evidence:

    1. Does a daemon answer?  ``lxc info`` with no argument is LXD's read-only
       server-info call, so exit 0 proves the daemon is up and this user's
       ``lxd`` group membership lets it reach the socket.  A bare
       ``which("lxc")`` proves neither.
    2. Is that daemon actually LXD?  The dump names its implementation
       (``server: lxd``), which rejects an ``lxc`` compatibility shim that
       fronts Incus — such a host would otherwise advertise ``lxd`` and fail
       every LXD job routed to it.  If the output shape is unrecognised, fall
       back to whether LXD's own daemon binary is installed at all.

    Classic LXC cannot reach either question: it ships no plain ``lxc``.
    """
    exe = _which("lxc")
    if not exe:
        return False
    out = _command_output([exe, "info"])
    if out is None:
        return False
    server = _reported_server(out)
    if server:
        return server == "lxd"
    return _lxd_daemon_present()


def _has_subid_delegation() -> bool:
    """True when the invoking user has BOTH subuid and subgid ranges."""
    import getpass
    from pathlib import Path

    names: set[str] = set()
    try:
        names.add(getpass.getuser())
    except Exception:
        pass
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        try:
            names.add(str(getuid()))
        except Exception:
            pass
    if not names:
        return False
    for path in _SUBID_FILES:
        try:
            text = Path(path).read_text()
        except Exception:
            return False
        owners = {line.split(":", 1)[0] for line in text.splitlines() if ":" in line}
        if not (names & owners):
            return False
    return True


def _probe_lxc() -> bool:
    """Detect a *usable* CLASSIC LXC (liblxc + ``lxc-*`` tools).  Never raises.

    Deliberately never looks at a plain ``lxc`` binary: that is LXD's client
    (see :func:`_probe_lxd`), and classic LXC does not ship one.  The marker
    of classic LXC is the hyphenated pair a job actually drives —
    ``lxc-create`` + ``lxc-start`` — which LXD and Incus never install, so no
    amount of LXD/Incus on a host can satisfy this probe.

    Classic LXC is daemonless, so there is no socket whose permissions stand
    in for "usable".  Two checks replace it:

    * ``lxc-ls -1`` must exit 0.  It is the read-only listing command from the
      same lxc-utils package, and it exercises liblxc's config + lxcpath
      lookup as this user, so it fails on a half-installed or unreadable
      install.  (An empty list is still exit 0 — a fresh host is usable.)
    * a non-root user must additionally hold subuid/subgid delegation.  This
      is the classic-LXC analogue of "not in the ``lxd`` group": without the
      ranges, ``lxc-start`` cannot create the user namespace and every
      unprivileged container fails, even though every binary is present.
    """
    create = _which("lxc-create")
    start = _which("lxc-start")
    if not (create and start):
        return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() != 0 and not _has_subid_delegation():
        return False
    ls = _which("lxc-ls")
    if not ls:
        # lxc-utils ships lxc-ls beside lxc-create; missing means a partial
        # install, and there is no other read-only command cheap enough to
        # stand in for it.
        return False
    return _command_ok([ls, "-1"])


# name -> zero-arg probe returning True when the capability is present.
_CAPABILITY_PROBES = {
    "cuda": _probe_cuda,
    "incus": _probe_incus,
    "lxd": _probe_lxd,
    "lxc": _probe_lxc,
}


def host_capabilities() -> set[str]:
    """Return the set of capabilities available on the current host.

    Resolution order:

    1. If ``CVCPKG_CAPABILITIES`` is set, its comma-separated value is returned
       verbatim (authoritative — this is how CI and tests inject capabilities).
       An empty string means "no capabilities".
    2. Otherwise each registered probe in :data:`_CAPABILITY_PROBES` runs; the
       result is cached in a module global so probing happens at most once.

    A fresh copy is returned each call so callers cannot mutate the cache.
    """
    env = os.environ.get("CVCPKG_CAPABILITIES")
    if env is not None:
        return {c.strip() for c in env.split(",") if c.strip()}

    global _probed_capabilities
    if _probed_capabilities is None:
        found: set[str] = set()
        for cap, probe in _CAPABILITY_PROBES.items():
            try:
                if probe():
                    found.add(cap)
            except Exception:
                pass
        _probed_capabilities = found
    return set(_probed_capabilities)


# ── Free scratch disk ───────────────────────────────────────────
#
# The quantitative sibling of a capability: a recipe declares
# ``build.min_disk_gb`` and a builder advertises how much its work volume has
# free, so the scheduler can refuse the pairing instead of letting the build
# discover it an hour in.  Unlike a capability this is a *measurement*, so the
# builder re-takes it on every heartbeat rather than once at registration.


def free_disk_gb(path: str | os.PathLike[str] | None) -> int | None:
    """Free space in whole GiB on the volume holding *path*, or ``None``.

    *path* is the builder's work-dir root — the volume that actually holds the
    per-job build trees — not the CWD or the install prefix, which routinely
    live on a different filesystem.  ``None`` (no work dir configured) measures
    the system temp dir, because that is where ``_execute_job``'s ``mkdtemp``
    lands when ``--work-dir`` is unset.

    A path that does not exist yet walks up to its nearest existing ancestor:
    the answer is a property of the *volume*, and the builder measures before
    the first job has created anything.  ``None`` is returned when even that
    fails (an unreadable mount, a platform where ``statvfs`` is unavailable) —
    "unknown", which callers must not confuse with "zero free" (see
    ``_satisfies_disk`` in the server).

    Truncating rather than rounding is deliberate: 34.9 GiB free must not
    report as 35 and satisfy a 35 GiB requirement.
    """
    import shutil
    from pathlib import Path

    try:
        import tempfile

        probe = Path(path) if path is not None else Path(tempfile.gettempdir())
        probe = probe.resolve()
        # Walk up to the nearest existing ancestor; on any sane layout that is
        # still the same filesystem, and at worst it is the mount point above.
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return int(shutil.disk_usage(probe).free // (1024**3))
    except Exception:
        return None
