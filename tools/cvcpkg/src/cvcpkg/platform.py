"""Auto-detect platform, architecture, libc, and CRT metadata."""

from __future__ import annotations

import platform
import struct
import sys


def detect_platform() -> str:
    """Return 'linux', 'macos', or 'windows'."""
    s = sys.platform
    if s.startswith("linux"):
        return "linux"
    if s == "darwin":
        return "macos"
    if s in ("win32", "cygwin"):
        return "windows"
    raise RuntimeError(f"unsupported platform: {s}")


def detect_arch() -> str:
    """Return 'x86_64' or 'arm64'."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    raise RuntimeError(f"unsupported architecture: {machine}")


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
