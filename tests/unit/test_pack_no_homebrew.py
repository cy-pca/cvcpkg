"""Pack refuses exported build metadata that points into Homebrew."""

from __future__ import annotations

from pathlib import Path

import pytest

from cvcpkg.builder import PackError, check_no_homebrew


def _bundle(platform="macos"):
    return {"name": "demo", "platform": platform, "arch": "x86_64"}


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.mark.parametrize(
    "rel,text",
    [
        ("lib/pkgconfig/demo.pc", "Libs: -L/usr/local/opt/gettext/lib -lintl\n"),
        ("bin/demo-config", "#!/bin/sh\necho -I/opt/homebrew/include\n"),
        ("lib/libdemo.la", "dependency_libs=' -L/usr/local/Cellar/xz/5.6/lib'\n"),
        ("lib/cmake/Demo/DemoConfig.cmake", 'set(DEMO_INC "/opt/homebrew/include")\n'),
        (
            "lib/python3.13/_sysconfigdata__darwin_darwin.py",
            "build_time_vars = {'LDFLAGS': '-L/usr/local/opt/gettext/lib'}\n",
        ),
        ("lib/python3.13/config-3.13-darwin/Makefile", "LDFLAGS= -L/usr/local/opt/gettext/lib\n"),
        ("lib/pkgconfig/demo.pc", "Cflags: -I/home/linuxbrew/.linuxbrew/include\n"),
    ],
)
def test_homebrew_in_exported_metadata_fails_the_pack(tmp_path, rel, text):
    _write(tmp_path, rel, text)
    with pytest.raises(PackError, match="point into Homebrew"):
        check_no_homebrew(tmp_path, _bundle())


def test_on_linux_too(tmp_path):
    _write(tmp_path, "lib/pkgconfig/demo.pc", "Libs: -L/opt/homebrew/lib\n")
    with pytest.raises(PackError):
        check_no_homebrew(tmp_path, _bundle("linux"))


def test_tools_that_merely_search_homebrew_are_not_flagged(tmp_path):
    # CMake's own find modules and pkgconf's default search path name
    # /opt/homebrew as a place to look; that is not a dependency on it.
    _write(tmp_path, "share/cmake-3.31/Modules/FindFoo.cmake", "PATHS /opt/homebrew/lib\n")
    _write(tmp_path, "share/doc/demo/README", "brew install demo -> /usr/local/opt/demo\n")
    check_no_homebrew(tmp_path, _bundle())


def test_clean_metadata_passes(tmp_path):
    _write(tmp_path, "lib/pkgconfig/demo.pc", "prefix=${pcfiledir}/../..\nLibs: -L${prefix}/lib\n")
    check_no_homebrew(tmp_path, _bundle())


def test_linkage_allow_vouches_for_a_prefix(tmp_path):
    _write(tmp_path, "lib/pkgconfig/demo.pc", "Libs: -L/opt/homebrew/opt/vendored/lib\n")
    check_no_homebrew(tmp_path, _bundle(), allow=["/opt/homebrew/opt/vendored/"])
