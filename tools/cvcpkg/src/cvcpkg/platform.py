"""Auto-detect platform, architecture, libc, and CRT metadata."""

from __future__ import annotations

import platform
import struct
import sys


def detect_platform() -> str:
    """Return 'linux', 'macos', 'windows', 'freebsd', 'openbsd', or 'netbsd'."""
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
    raise RuntimeError(f"unsupported platform: {s}")


def detect_arch() -> str:
    """Return a normalised architecture name for the current host."""
    machine = platform.machine().lower()
    _ARCH_MAP = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "riscv64": "riscv64",
        "ppc64le": "ppc64le",
        "ppc64": "ppc64",
        "s390x": "s390x",
    }
    arch = _ARCH_MAP.get(machine)
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
