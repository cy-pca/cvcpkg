"""The relocation pass names a scratch-prefix NEEDED by its basename.

xkbcommon-x11 on OpenBSD (dev fleet, 2026-10-19) NEEDED its job prefix's
lib/libxcb.so.2.0 by absolute path: OpenBSD libraries carry no SONAME and
meson links dependencies by path, so the pack-time gate refused the bundle.
"""

from __future__ import annotations

from cvcpkg.builder import _relativize_elf_needed
from cvcpkg.linkage import read_linkage
from tests.unit._binfixtures import elf

JOB = "/usr/local/cvcpkg-builder/10-66-77-207/cvcpkg-job-x/cvcpkg-prefix-x-abc"


def test_scratch_prefix_needed_becomes_a_bare_name(tmp_path):
    lib = tmp_path / "install" / "lib"
    lib.mkdir(parents=True)
    so = elf(
        lib / "libxkbcommon-x11.so.0.0.0",
        kind="dyn",
        needed=[f"{JOB}/lib/libxcb.so.2.0", "/usr/lib/libc.so.100.3"],
        runpath="$ORIGIN",
    )
    _relativize_elf_needed(tmp_path / "install", (JOB, None))
    assert read_linkage(so).needed == ("libxcb.so.2.0", "/usr/lib/libc.so.100.3")
