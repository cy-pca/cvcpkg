"""Auto-detect platform, architecture, libc, and CRT metadata."""

from __future__ import annotations

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
        "any",
    }
)

# Common non-canonical spellings -> canonical (mirrors detect_arch's map).
ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "aarch64": "arm64",
}


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
