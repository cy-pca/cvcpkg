"""FreeBSD's libdata/pkgconfig is merged into lib/pkgconfig after every build.

meson's pkgconfig module installs .pc files to <prefix>/libdata/pkgconfig on
FreeBSD, but cvcpkg's layout, package.files and every consumer's
PKG_CONFIG_PATH use lib/pkgconfig (pixman and harfbuzz on the dev fleet,
2026-10-19).
"""

from __future__ import annotations

import pytest

from cvcpkg.builder import BuildError, _merge_libdata_pkgconfig


def test_libdata_pkgconfig_moves_into_lib_pkgconfig(tmp_path):
    pc = tmp_path / "libdata" / "pkgconfig"
    pc.mkdir(parents=True)
    (pc / "pixman-1.pc").write_text("prefix=${pcfiledir}/../..\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libpixman-1.so").write_text("")

    _merge_libdata_pkgconfig(tmp_path)

    assert (tmp_path / "lib" / "pkgconfig" / "pixman-1.pc").read_text().startswith("prefix=")
    assert not (tmp_path / "libdata").exists()


def test_other_libdata_content_stays(tmp_path):
    (tmp_path / "libdata" / "pkgconfig").mkdir(parents=True)
    (tmp_path / "libdata" / "pkgconfig" / "a.pc").write_text("")
    (tmp_path / "libdata" / "ldconfig").mkdir()

    _merge_libdata_pkgconfig(tmp_path)

    assert (tmp_path / "lib" / "pkgconfig" / "a.pc").is_file()
    assert (tmp_path / "libdata" / "ldconfig").is_dir()
    assert not (tmp_path / "libdata" / "pkgconfig").exists()


def test_no_libdata_is_a_no_op(tmp_path):
    (tmp_path / "lib" / "pkgconfig").mkdir(parents=True)
    (tmp_path / "lib" / "pkgconfig" / "z.pc").write_text("")

    _merge_libdata_pkgconfig(tmp_path)

    assert sorted(p.name for p in (tmp_path / "lib" / "pkgconfig").iterdir()) == ["z.pc"]


def test_a_pc_in_both_places_is_an_error(tmp_path):
    for d in ("libdata/pkgconfig", "lib/pkgconfig"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "dup.pc").write_text(d)

    with pytest.raises(BuildError, match="dup.pc"):
        _merge_libdata_pkgconfig(tmp_path)
