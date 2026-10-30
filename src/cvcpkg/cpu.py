# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""x86-64 microarchitecture levels as builder capabilities.

Some upstreams ship x86_64 code that needs a newer CPU than the psABI
baseline.  Dr.Jit and Mitsuba compile with ``-march=haswell``, and Mitsuba's
struct-jit emits F16C and FMA instructions directly, so on a Sandy or Ivy
Bridge host the build dies with SIGILL as soon as it imports the module it
just built (nanobind's stubgen does, mid-build).  Nothing about the recipe can
fix that: the job has to run on a capable machine.

The fix reuses the builder-capability mechanism, the way glibc floors do (see
:mod:`cvcpkg.glibc`):

1. A builder on an x86_64 host advertises every level its CPU satisfies
   (``x86-64-v2``, ``x86-64-v3``, ``x86-64-v4``; a v4 host satisfies all three).
2. A recipe declares ``build.min_cpu: {x86_64: x86-64-v3}``, and submit-dag
   adds that level to the recipe's x86_64 jobs (and its noarch jobs when the
   noarch build target is x86_64), so the scheduler routes them only to
   builders that advertise it.

Levels are builder-only, like glibc floors: they never enter the install-side
capability set, so declaring one changes nothing for consumers.  The level
names and their feature sets are the x86-64 psABI's.

A builder whose CPU cannot be read advertises no level, and a job that needs
one is never routed to it: unlike the glibc floor, an unknown CPU is not
excused, since excusing it is exactly how these jobs reached SIGILL builders.
"""

from __future__ import annotations

import os
import platform as _platform
import subprocess

# The psABI levels, each with the features it adds over the previous one, as
# /proc/cpuinfo spells them (``abm`` is how Linux reports LZCNT).
LEVELS: tuple[tuple[str, frozenset[str]], ...] = (
    ("x86-64-v2", frozenset({"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"})),
    (
        "x86-64-v3",
        frozenset({"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"}),
    ),
    ("x86-64-v4", frozenset({"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"})),
)

LEVEL_NAMES: tuple[str, ...] = tuple(name for name, _ in LEVELS)

# macOS sysctl spellings (machdep.cpu.features / leaf7_features /
# extfeatures) of the features above.
_DARWIN_NAMES = {
    "CX16": "cx16",
    "LAHF": "lahf_lm",
    "POPCNT": "popcnt",
    "SSE4.1": "sse4_1",
    "SSE4.2": "sse4_2",
    "SSSE3": "ssse3",
    "AVX1.0": "avx",
    "AVX2": "avx2",
    "BMI1": "bmi1",
    "BMI2": "bmi2",
    "F16C": "f16c",
    "FMA": "fma",
    "LZCNT": "abm",
    "MOVBE": "movbe",
    "XSAVE": "xsave",
    "AVX512F": "avx512f",
    "AVX512BW": "avx512bw",
    "AVX512CD": "avx512cd",
    "AVX512DQ": "avx512dq",
    "AVX512VL": "avx512vl",
}


def is_x86_64(machine: str | None = None) -> bool:
    m = (machine if machine is not None else _platform.machine()).lower()
    return m in ("x86_64", "amd64")


def levels_for_flags(flags: set[str] | frozenset[str]) -> set[str]:
    """The levels a CPU with *flags* satisfies (each level includes the ones below)."""
    have: set[str] = set()
    for name, needed in LEVELS:
        if not needed <= flags:
            break
        have.add(name)
    return have


def parse_cpuinfo(text: str) -> set[str] | None:
    """Feature flags from /proc/cpuinfo text (the first ``flags`` line), or None."""
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "flags":
            return set(value.split())
    return None


def parse_darwin_sysctl(text: str) -> set[str]:
    """Feature flags from ``sysctl -n machdep.cpu.features ...`` output."""
    return {_DARWIN_NAMES[w] for w in text.split() if w in _DARWIN_NAMES}


def host_cpu_flags() -> set[str] | None:
    """This host's x86-64 feature flags, or None when they cannot be read.

    ``CVCPKG_HOST_CPU_FLAGS`` (space- or comma-separated, /proc/cpuinfo names)
    overrides, which is how tests inject a CPU without lying about the machine.
    Linux reads /proc/cpuinfo and macOS asks sysctl; elsewhere (Windows) the
    flags are unknown.
    """
    env = os.environ.get("CVCPKG_HOST_CPU_FLAGS")
    if env is not None:
        return {f for f in env.replace(",", " ").split() if f}
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            return parse_cpuinfo(f.read())
    except OSError:
        pass
    if _platform.system() == "Darwin":
        try:
            out = subprocess.run(
                [
                    "sysctl",
                    "-n",
                    "machdep.cpu.features",
                    "machdep.cpu.leaf7_features",
                    "machdep.cpu.extfeatures",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
            return parse_darwin_sysctl(out) if out else None
        except Exception:
            return None
    return None


def builder_capabilities(flags: set[str] | None = None, machine: str | None = None) -> set[str]:
    """The level capabilities an x86_64 builder advertises (empty elsewhere)."""
    if not is_x86_64(machine):
        return set()
    f = host_cpu_flags() if flags is None else flags
    if f is None:
        return set()
    return levels_for_flags(f)


def required_level(min_cpu: object, arch: str) -> str | None:
    """A recipe's ``build.min_cpu`` entry for *arch*, or None.

    Only ``x86_64`` has levels today; any other arch (arm64, noarch built on
    arm64, wasm32) carries no CPU requirement.
    """
    if not isinstance(min_cpu, dict) or not is_x86_64(arch):
        return None
    level = min_cpu.get("x86_64")
    return level if isinstance(level, str) and level in LEVEL_NAMES else None
