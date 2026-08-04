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
    """Return 'linux', 'macos', 'windows', 'freebsd', 'openbsd', 'netbsd', or 'dragonflybsd'.

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
    return machine


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
