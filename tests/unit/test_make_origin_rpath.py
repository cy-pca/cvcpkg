"""recipes/_common/rewrite-install-paths.sh: cvc_make_origin_rpath.

An autotools recipe that exports ``LDFLAGS=-Wl,-rpath,$ORIGIN`` links every
library with the RUNPATH ``RIGIN``: ./configure copies LDFLAGS into the
Makefiles, where make reads ``$ORIGIN`` as the variable ``$O`` plus "RIGIN".
libxshmfence, libXxf86vm, libXext and openssh shipped it (2026-10-30).  These
tests drive a real make, and a real linker where there is one.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cvcpkg.linkage import read_linkage

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "recipes" / "_common" / "rewrite-install-paths.sh"

# The helper is sourced by recipe builds on Linux, the BSDs, macOS and Haiku.  Git Bash on a
# Windows runner cannot `source` a Windows-style (D:\a\...) path, and mingw's ld has no ELF
# RUNPATH to read.
needs_posix_make = pytest.mark.skipif(
    sys.platform == "win32" or not (shutil.which("bash") and shutil.which("make")),
    reason="needs bash and make on a POSIX host",
)


def _flags(*args: str) -> str:
    """What `$(cvc_make_origin_rpath ARGS)` expands to in a recipe."""
    cmd = f"source '{HELPER}'; cvc_make_origin_rpath {' '.join(repr(a) for a in args)}"
    return subprocess.run(["bash", "-c", cmd], check=True, capture_output=True, text=True).stdout


def _make_sees(tmp_path: Path, ldflags: str) -> list[str]:
    """The words a make recipe line hands the shell for ``$(LDFLAGS)``: what
    ./configure writes as ``LDFLAGS = <value>`` and libtool/cc then receive."""
    mk = tmp_path / "Makefile"
    mk.write_text(f"LDFLAGS = {ldflags}\nshow:\n\t@printf '%s\\n' $(LDFLAGS)\n")
    out = subprocess.run(
        ["make", "-f", str(mk), "show"], check=True, capture_output=True, text=True
    ).stdout
    return out.split()


@needs_posix_make
def test_helper_spelling():
    assert _flags() == r"-Wl,-rpath,\$$ORIGIN"
    assert _flags("../lib", ".") == r"-Wl,-rpath,\$$ORIGIN/../lib -Wl,-rpath,\$$ORIGIN"
    assert _flags("/..") == r"-Wl,-rpath,\$$ORIGIN/.."


@needs_posix_make
def test_bare_origin_is_what_make_mangles(tmp_path):
    # The bug: the literal `$ORIGIN` that `export LDFLAGS="... \$ORIGIN"` gives.
    assert _make_sees(tmp_path, "-Wl,-rpath,$ORIGIN") == ["-Wl,-rpath,RIGIN"]


@needs_posix_make
def test_helper_flags_reach_the_linker_as_origin(tmp_path):
    assert _make_sees(tmp_path, _flags()) == ["-Wl,-rpath,$ORIGIN"]
    assert _make_sees(tmp_path, _flags("../lib", ".")) == [
        "-Wl,-rpath,$ORIGIN/../lib",
        "-Wl,-rpath,$ORIGIN",
    ]


@needs_posix_make
def test_helper_flags_survive_make_and_a_real_link(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("needs a C compiler")
    (tmp_path / "x.c").write_text("int x(void){return 0;}\n")
    mk = tmp_path / "Makefile"
    mk.write_text(
        f"LDFLAGS = {_flags('../lib', '.')}\n"
        f"libx.so: x.c\n\t{cc} -shared -fPIC -o $@ x.c $(LDFLAGS)\n"
    )
    subprocess.run(["make", "-C", str(tmp_path)], check=True, capture_output=True)
    link = read_linkage(tmp_path / "libx.so")
    assert link is not None
    # Some linkers write the flags as one entry list, some keep them apart.
    assert ":".join(link.rpaths) == "$ORIGIN/../lib:$ORIGIN"


def test_no_autotools_recipe_exports_a_bare_origin_ldflags():
    """The recurrence guard at the source: a build.sh that runs ./configure must
    put its $ORIGIN LDFLAGS through cvc_make_origin_rpath.  (Meson, CMake, waf
    and direct compiler lines take the plain `\\$ORIGIN`: make never sees it.)"""
    bare = re.compile(r"""LDFLAGS=.*(?<![$\\])\\\$ORIGIN""")
    configure = re.compile(r"\./configure\b|\bautogen\.sh\b|\}/configure\b")
    offenders = []
    for sh in sorted((REPO / "recipes").glob("*/build.sh")):
        lines = sh.read_text().splitlines()
        code = [ln for ln in lines if not ln.lstrip().startswith("#")]
        if not any(configure.search(ln) for ln in code):
            continue
        offenders += [
            f"{sh.parent.name}/build.sh:{n}: {ln.strip()}"
            for n, ln in enumerate(lines, 1)
            if bare.search(ln) and not ln.lstrip().startswith("#")
        ]
    assert not offenders, (
        "make expands a bare `$ORIGIN` in LDFLAGS to `RIGIN`; use "
        "$(cvc_make_origin_rpath) from recipes/_common/rewrite-install-paths.sh:\n  "
        + "\n  ".join(offenders)
    )
