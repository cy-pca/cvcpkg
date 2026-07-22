"""_patch_macos_install_names must make macOS dylib bundles relocatable:
rewrite each dylib's id to @rpath/<leaf>, add a @loader_path RPATH, and rewrite
absolute references to SIBLING bundle dylibs to @rpath — while leaving system
references (/usr/lib, /System/...) untouched. macOS analog of
test_builder_rpath.py; the logic is exercised on Linux via mocked
install_name_tool/otool."""

import subprocess
from unittest import mock

from cvcpkg.builder import _patch_macos_install_names

_BUILD = "/private/var/folders/xx/cvcpkg-imagemagick-abcd/install/lib"

# otool -L output, keyed by dylib leaf, as it reads AFTER the id was set to
# @rpath. libMagick++ references a sibling bundle dylib (abs, build-tree path)
# plus a system lib.
_OTOOL = {
    "libMagick++-7.Q16HDRI.5.dylib": (
        f"{_BUILD}/libMagick++-7.Q16HDRI.5.dylib:\n"
        "\t@rpath/libMagick++-7.Q16HDRI.5.dylib (compatibility version 1.0.0)\n"
        f"\t{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib (compatibility version 1.0.0)\n"
        "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
    ),
    "libMagickCore-7.Q16HDRI.10.dylib": (
        f"{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib:\n"
        "\t@rpath/libMagickCore-7.Q16HDRI.10.dylib (compatibility version 1.0.0)\n"
        "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
    ),
}


def _patch_with_mocks(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    for leaf in _OTOOL:
        (lib / leaf).write_bytes(b"\xcf\xfa\xed\xfe-fake-macho")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0].endswith("otool"):
            leaf = cmd[-1].rsplit("/", 1)[-1]
            return subprocess.CompletedProcess(cmd, 0, stdout=_OTOOL[leaf], stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_which(tool):
        return {
            "install_name_tool": "/usr/bin/install_name_tool",
            "otool": "/usr/bin/otool",
        }.get(tool)

    with (
        mock.patch("cvcpkg.builder.shutil.which", side_effect=fake_which),
        mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
    ):
        _patch_macos_install_names(tmp_path)
    return calls


def _has(calls, *parts):
    return any(all(p in c for p in parts) for c in calls)


def test_rewrites_ids_rpath_and_sibling_refs(tmp_path):
    calls = _patch_with_mocks(tmp_path)
    # id -> @rpath/<leaf> for every bundle dylib
    assert _has(calls, "-id", "@rpath/libMagick++-7.Q16HDRI.5.dylib")
    assert _has(calls, "-id", "@rpath/libMagickCore-7.Q16HDRI.10.dylib")
    # @loader_path RPATH added (the $ORIGIN analog)
    assert _has(calls, "-add_rpath", "@loader_path")
    # absolute reference to a SIBLING bundle dylib rewritten to @rpath
    assert _has(
        calls,
        "-change",
        f"{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib",
        "@rpath/libMagickCore-7.Q16HDRI.10.dylib",
    )


def test_leaves_system_refs_untouched(tmp_path):
    calls = _patch_with_mocks(tmp_path)
    assert not _has(calls, "-change", "/usr/lib/libSystem.B.dylib")


def test_noop_without_tools(tmp_path):
    (tmp_path / "lib").mkdir()
    with mock.patch("cvcpkg.builder.shutil.which", return_value=None):
        _patch_macos_install_names(tmp_path)  # must not raise
