# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Auto-detect platform, architecture, libc, and CRT metadata."""

from __future__ import annotations

import os
import platform
import struct
import sys

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
        "dragonflybsd",
        "haiku",
        "wasm",
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
    'dragonflybsd', or 'haiku'.

    GhostBSD note: GhostBSD's kernel identifies as FreeBSD (sys.platform
    is ``freebsd*``), so GhostBSD hosts intentionally detect as
    ``freebsd`` and consume the freebsd package channel (compat mode).
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
        return "dragonflybsd"
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
    if plat == "dragonflybsd":
        return "dragonfly-libc"
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
    """Best-effort detection of a usable CUDA stack.  Never raises."""
    try:
        import ctypes.util

        if ctypes.util.find_library("cuda"):
            return True
    except Exception:
        pass
    try:
        import shutil

        if shutil.which("nvidia-smi") or shutil.which("nvcc"):
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
            from pathlib import Path

            bin_dir = Path(cuda_home) / "bin"
            if (bin_dir / "nvcc").exists() or (bin_dir / "nvcc.exe").exists():
                return True
    except Exception:
        pass
    return False


# name -> zero-arg probe returning True when the capability is present.
_CAPABILITY_PROBES = {
    "cuda": _probe_cuda,
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
