# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""glibc floors for Linux bundles — cvcpkg's answer to manylinux.

A dynamically-linked ELF records the *versioned* glibc symbols it uses
(``GLIBC_2.38`` and friends).  glibc is backward- but NOT forward-compatible,
so a binary built against 2.39 refuses to start on a 2.35 host:

    ./python3.13t: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38'
    not found (required by .../libpython3.13t.so.1.0)

A heterogeneous build fleet turns that into a lottery: whichever machine the
scheduler happened to pick decides whether the artifact is usable elsewhere.
On the cvcpkg fleet, four linux builders run glibc 2.35 and one (the CUDA
host) runs 2.39, so anything that landed on the CUDA host was silently
unusable on the other four — and on every 22.04 consumer.  That is what broke
the cp313t column of the python matrix, and it would have broken every future
CUDA package too.

manylinux solves this with three parts, and so do we:

1. **Produce** against a low glibc.  manylinux builds inside an old image;
   cvcpkg instead ROUTES the job to a builder whose glibc is at or below the
   target floor, reusing the builder-capability mechanism (a builder
   advertises ``glibc2.35`` when a bundle built there runs on 2.35+).
2. **Verify** the artifact.  auditwheel refuses a wheel whose symbols exceed
   its tag; :func:`max_required_glibc` reads the same information back out of
   the built ELFs so the build fails at the builder instead of at some
   consumer months later.
3. **Record** the floor so consumers can check it, the way a wheel carries
   ``manylinux_2_28`` in its filename.

Versions are compared as ``(major, minor)`` tuples, never as floats or
strings: 2.9 < 2.10, which both of the other representations get wrong.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Floors we advertise capabilities for.  These mirror the manylinux series
# (2.17 = manylinux2014, 2.28 = manylinux_2_28, ...) plus the Ubuntu LTS
# glibcs the fleet actually runs, so a policy can name a realistic target.
KNOWN_FLOORS: tuple[tuple[int, int], ...] = (
    (2, 17),
    (2, 28),
    (2, 31),
    (2, 34),
    (2, 35),
    (2, 36),
    (2, 38),
    (2, 39),
    (2, 41),
)

# Default target floor for linux bundles.  2.35 is Ubuntu 22.04 / the oldest
# glibc in the current fleet, so a bundle built to this floor runs on every
# builder and every consumer we have.  Override with CVCPKG_GLIBC_FLOOR.
DEFAULT_FLOOR = (2, 35)

_GLIBC_SYM = re.compile(rb"GLIBC_(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int] | None:
    """Parse ``"2.35"`` / ``"glibc2.35"`` / ``"2.35.1"`` -> ``(2, 35)``."""
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def format_version(v: tuple[int, int]) -> str:
    return f"{v[0]}.{v[1]}"


def capability_name(v: tuple[int, int]) -> str:
    """Capability flag for a floor: ``(2, 35)`` -> ``glibc2.35``."""
    return f"glibc{format_version(v)}"


def host_glibc() -> tuple[int, int] | None:
    """The running host's glibc, or None when not glibc (musl, macOS, ...).

    ``CVCPKG_HOST_GLIBC`` overrides, which is how tests and cross-builds
    inject a value without lying about the machine.
    """
    env = os.environ.get("CVCPKG_HOST_GLIBC")
    if env:
        return parse_version(env)
    # os.confstr("CS_GNU_LIBC_VERSION") -> "glibc 2.35"; the most direct
    # question, and it needs no subprocess.
    try:
        cs = os.confstr("CS_GNU_LIBC_VERSION")  # type: ignore[attr-defined]
        if cs:
            return parse_version(cs)
    except (AttributeError, ValueError, OSError):
        pass
    try:
        out = subprocess.run(
            ["ldd", "--version"], capture_output=True, text=True, timeout=15
        ).stdout
        return parse_version(out.splitlines()[0]) if out else None
    except Exception:
        return None


def target_floor() -> tuple[int, int]:
    """The floor linux bundles are expected to satisfy."""
    return parse_version(os.environ.get("CVCPKG_GLIBC_FLOOR", "")) or DEFAULT_FLOOR


def builder_capabilities(glibc: tuple[int, int] | None = None) -> set[str]:
    """Floors a builder with *glibc* can satisfy.

    A bundle built against glibc X runs on hosts with glibc >= X, so a builder
    satisfies every floor at or above its own: a 2.35 machine can produce
    bundles for the 2.35, 2.38 and 2.39 floors, but NOT for 2.28 — its
    binaries would demand symbols a 2.28 host does not have.
    """
    g = host_glibc() if glibc is None else glibc
    if g is None:
        return set()
    return {capability_name(f) for f in KNOWN_FLOORS if f >= g}


def max_required_glibc(root: Path | str) -> tuple[int, int] | None:
    """Highest ``GLIBC_x.y`` any ELF under *root* requires, or None.

    Reads the raw bytes and scans for the version strings rather than shelling
    out to readelf/objdump: those are not installed on every builder, and a
    verification step that silently skips itself is worse than none.  The
    strings live in ``.dynstr``, so a plain scan is accurate here and cheap.
    """
    root = Path(root)
    files: list[Path] = []
    if root.is_file():
        files = [root]
    else:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                p = Path(dirpath) / n
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                except OSError:
                    continue
                files.append(p)

    worst: tuple[int, int] | None = None
    for p in files:
        try:
            with open(p, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
                f.seek(0)
                blob = f.read()
        except OSError:
            continue
        for maj, minor in _GLIBC_SYM.findall(blob):
            v = (int(maj), int(minor))
            if worst is None or v > worst:
                worst = v
    return worst


def check_floor(
    root: Path | str, floor: tuple[int, int] | None = None
) -> tuple[bool, tuple[int, int] | None, str]:
    """Verify everything under *root* runs on a host at *floor*.

    Returns ``(ok, required, message)``.  ``required`` is None when nothing
    under *root* links glibc at all (a pure-python or static bundle), which is
    trivially fine.
    """
    floor = floor or target_floor()
    required = max_required_glibc(root)
    if required is None:
        return True, None, "no glibc-linked ELF found"
    if required <= floor:
        return (
            True,
            required,
            f"requires glibc {format_version(required)} <= floor {format_version(floor)}",
        )
    return (
        False,
        required,
        (
            f"requires glibc {format_version(required)} but the target floor is "
            f"{format_version(floor)} — this bundle will NOT start on a "
            f"glibc {format_version(floor)} host. It was almost certainly built "
            f"on a machine with a newer glibc than the fleet floor; rebuild it "
            f"on a builder advertising {capability_name(floor)}."
        ),
    )
