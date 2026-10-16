"""The ELF relocation passes keep a DT_RPATH a DT_RPATH.

``patchelf --set-rpath`` silently turns a DT_RPATH into a DT_RUNPATH unless it
is given ``--force-rpath``, and that changes what wins over LD_LIBRARY_PATH.
recipes/libreoffice depends on DT_RPATH: with a DT_RUNPATH, a prefix's
libpython3.13 on LD_LIBRARY_PATH replaced LibreOffice's bundled one and soffice
threw std::bad_alloc in pyuno.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest

from cvcpkg import linkage
from cvcpkg.builder import _patch_elf_rpath, _patch_elf_rpath_absolute, _uses_dt_rpath


def _fake_linkage(rpath_names: set[str]):
    def read(path):
        return mock.MagicMock(format="elf", dt_rpath=Path(path).name in rpath_names)

    return read


def _set_rpath_calls(calls: list[list[str]]) -> dict[str, list[str]]:
    return {c[-1]: c for c in calls if "--set-rpath" in c}


def test_the_origin_pass_forces_rpath_only_where_there_was_one(tmp_path):
    lib = tmp_path / "install" / "lib"
    lib.mkdir(parents=True)
    for name in ("librpath.so", "librunpath.so"):
        (lib / name).write_bytes(b"\x7fELF fake")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.MagicMock(stdout="$ORIGIN", returncode=0)

    with (
        patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
        patch("cvcpkg.builder._linkage.read_linkage", side_effect=_fake_linkage({"librpath.so"})),
    ):
        _patch_elf_rpath(tmp_path / "install", "/opt/cvcpkg/bin/patchelf")

    sets = _set_rpath_calls(calls)
    assert "--force-rpath" in sets[str(lib / "librpath.so")]
    assert "--force-rpath" not in sets[str(lib / "librunpath.so")]


def test_the_absolute_pass_forces_rpath_only_where_there_was_one(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "lib").mkdir(parents=True)
    for name in ("librpath.so", "librunpath.so"):
        (prefix / "lib" / name).write_bytes(b"\x7fELF fake")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.MagicMock(stdout="", returncode=0)

    with (
        patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
        patch("cvcpkg.builder._linkage.read_linkage", side_effect=_fake_linkage({"librpath.so"})),
    ):
        _patch_elf_rpath_absolute(prefix, [prefix / "lib"], "/opt/cvcpkg/bin/patchelf")

    sets = _set_rpath_calls(calls)
    assert "--force-rpath" in sets[str(prefix / "lib" / "librpath.so")]
    assert "--force-rpath" not in sets[str(prefix / "lib" / "librunpath.so")]


def test_a_non_elf_file_is_not_dt_rpath(tmp_path):
    f = tmp_path / "libfake.so"
    f.write_text("not an object\n")
    assert _uses_dt_rpath(f) is False


_CC = shutil.which("cc") or shutil.which("gcc")
_PATCHELF = shutil.which("patchelf")


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not _CC or not _PATCHELF,
    reason="needs Linux, a C compiler and patchelf",
)
@pytest.mark.parametrize("dtags", ["--disable-new-dtags", "--enable-new-dtags"])
def test_a_real_object_keeps_its_tag(tmp_path, dtags):
    src = tmp_path / "x.c"
    src.write_text("int x(void) { return 1; }\n")
    lib = tmp_path / "install" / "lib"
    lib.mkdir(parents=True)
    so = lib / "libx.so"
    subprocess.run(
        [_CC, "-shared", "-fPIC", "-o", str(so), str(src), f"-Wl,{dtags}", "-Wl,-rpath,/nowhere"],
        check=True,
    )
    before = linkage.read_linkage(so)
    assert before is not None and before.dt_rpath is (dtags == "--disable-new-dtags")

    _patch_elf_rpath(tmp_path / "install", _PATCHELF)

    after = linkage.read_linkage(so)
    assert after is not None
    assert after.rpaths == ("$ORIGIN",)
    assert after.dt_rpath is before.dt_rpath
