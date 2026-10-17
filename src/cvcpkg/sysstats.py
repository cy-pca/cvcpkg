# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Host CPU, memory and disk figures a builder reports with its heartbeat.

The builders page shows them; the scheduler does not use them.  Builders run
as frozen single-file binaries on Linux, macOS, Windows, the BSDs and Haiku, so
nothing here needs a third-party package: psutil is used when importable,
otherwise each figure comes from the platform's own interface (``/proc``,
``sysctl``, the Win32 API) and is simply left out where there is none.  Every
probe is best effort and never raises.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

_STARTED = time.monotonic()


def _psutil():
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - absent, or broken in a frozen build
        return None
    return psutil


def _sysctl(name: str) -> str | None:
    """``sysctl -n <name>`` on the BSDs and macOS, or None."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _sysctl_int(name: str) -> int | None:
    raw = _sysctl(name)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


# ── CPU ─────────────────────────────────────────────────────────


def _cpu_times() -> tuple[int, int] | None:
    """(busy, total) CPU time counters since boot, in the platform's units."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/stat") as f:
                fields = [int(x) for x in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        if len(fields) < 4:
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
        total = sum(fields[:8])  # guest time is already counted in user
        return total - idle, total
    if sys.platform == "win32":
        return _win_cpu_times()
    if "bsd" in sys.platform:
        # kern.cp_time: user nice sys [spin] intr idle -- idle is always last.
        raw = _sysctl("kern.cp_time")
        if raw is None:
            return None
        ticks = [int(x) for x in re.findall(r"\d+", raw)]
        if len(ticks) < 5:
            return None
        return sum(ticks) - ticks[-1], sum(ticks)
    return None


def _win_cpu_times() -> tuple[int, int] | None:
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    try:
        ok = ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
    except (AttributeError, OSError):
        return None
    if not ok:
        return None

    def _n(ft) -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    total = _n(kernel) + _n(user)  # kernel time includes idle time
    return total - _n(idle), total


class CpuSampler:
    """Host CPU busy percentage between two calls to :meth:`percent`."""

    def __init__(self) -> None:
        self._last = _cpu_times()
        ps = _psutil()
        if ps is not None and self._last is None:
            try:
                ps.cpu_percent(interval=None)  # prime psutil's own baseline
            except Exception:  # noqa: BLE001
                pass

    def percent(self) -> float | None:
        now = _cpu_times()
        if now is None or self._last is None:
            self._last = now
            ps = _psutil()
            if ps is None:
                return None
            try:
                return round(float(ps.cpu_percent(interval=None)), 1)
            except Exception:  # noqa: BLE001
                return None
        busy = now[0] - self._last[0]
        total = now[1] - self._last[1]
        self._last = now
        if total <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * busy / total)), 1)


# ── Memory ──────────────────────────────────────────────────────


def _memory() -> tuple[int | None, int | None]:
    """(total, available) bytes of physical memory, each None when unknown."""
    ps = _psutil()
    if ps is not None:
        try:
            vm = ps.virtual_memory()
            return int(vm.total), int(vm.available)
        except Exception:  # noqa: BLE001
            pass
    if sys.platform.startswith("linux"):
        info: dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    key, _, rest = line.partition(":")
                    parts = rest.split()
                    if parts:
                        info[key] = int(parts[0]) * 1024
        except (OSError, ValueError):
            return None, None
        return info.get("MemTotal"), info.get("MemAvailable", info.get("MemFree"))
    if sys.platform == "win32":
        return _win_memory()
    if sys.platform == "darwin":
        total = _sysctl_int("hw.memsize")
        return total, _darwin_available()
    if sys.platform.startswith("freebsd"):
        page = _sysctl_int("hw.pagesize") or 4096
        counts = [_sysctl_int(f"vm.stats.vm.v_{k}_count") for k in ("free", "inactive", "cache")]
        avail = sum(c for c in counts if c) * page if any(counts) else None
        return _sysctl_int("hw.physmem"), avail
    if "bsd" in sys.platform:
        return _sysctl_int("hw.physmem64") or _sysctl_int("hw.physmem"), None
    return None, None


def _win_memory() -> tuple[int | None, int | None]:
    import ctypes

    class _MemStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    st = _MemStatus()
    st.dwLength = ctypes.sizeof(_MemStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
            return None, None
    except (AttributeError, OSError):
        return None, None
    return int(st.ullTotalPhys), int(st.ullAvailPhys)


def _darwin_available() -> int | None:
    try:
        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"page size of (\d+) bytes", out)
    page = int(m.group(1)) if m else 4096
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"{key}:\s+(\d+)", out)
        if m:
            pages += int(m.group(1))
    return pages * page if pages else None


# ── Uptime ──────────────────────────────────────────────────────


def _host_uptime() -> float | None:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "win32":
        import ctypes

        try:
            return ctypes.windll.kernel32.GetTickCount64() / 1000.0  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return None
    if sys.platform == "darwin" or "bsd" in sys.platform:
        raw = _sysctl("kern.boottime")
        if raw is None:
            return None
        m = re.search(r"sec\s*=\s*(\d+)", raw) or re.match(r"(\d+)", raw)
        if not m:
            return None
        return max(0.0, time.time() - int(m.group(1)))
    return None


# ── Snapshot ────────────────────────────────────────────────────


def collect(work_dir: str | os.PathLike | None, cpu: CpuSampler | None = None) -> dict:
    """The heartbeat's ``system`` figures; keys whose value is unknown are left out.

    *work_dir* names the volume whose size and free space are reported (the
    builder's work root; None is the system temp dir, where jobs then run).
    *cpu* carries the CPU baseline between beats; without one there is no CPU
    percentage.
    """
    stats: dict = {
        "cpu_count": os.cpu_count(),
        "agent_uptime_seconds": round(time.monotonic() - _STARTED, 1),
        "os": f"{platform.system()} {platform.release()}".strip()[:128] or None,
        "python": platform.python_version(),
    }
    if cpu is not None:
        stats["cpu_percent"] = cpu.percent()
    try:
        stats["load_avg"] = [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        pass
    stats["mem_total_bytes"], stats["mem_available_bytes"] = _memory()
    try:
        du = shutil.disk_usage(work_dir or tempfile.gettempdir())
        stats["disk_total_bytes"], stats["disk_free_bytes"] = du.total, du.free
    except OSError:
        pass
    up = _host_uptime()
    if up is not None:
        stats["uptime_seconds"] = round(up, 1)
    return {k: v for k, v in stats.items() if v is not None}
