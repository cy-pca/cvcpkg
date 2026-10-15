"""_patch_macos_install_names must make macOS bundles relocatable: give each
lib/ dylib the id @rpath/<leaf> and a @loader_path RPATH, point every absolute
reference to a bundle or dependency dylib at @rpath, and replace builder-path
LC_RPATHs with the @loader_path-relative ones the @rpath references need --
in bin/ as well as lib/ (cmake +cvc.1/+cvc.5, curl, openssl and grpc all
shipped such paths).  System references (/usr/lib, /System) stay untouched.
macOS analog of test_builder_rpath.py; runs on any host with
install_name_tool/codesign mocked and real (minimal) Mach-O files."""

import subprocess
from unittest import mock

from cvcpkg.builder import _patch_macos_install_names
from tests.unit._binfixtures import macho

_BUILD = "/private/var/folders/xx/cvcpkg-imagemagick-abcd1234/install/lib"
_CURL_TMP = "/private/var/folders/d8/T/cvcpkg-curl-b1y2e409/install/lib"
# The deps prefix the macOS lane builds against, as the linker recorded it.
_DEPS = "/Users/runner/work/cvcpkg/cvcpkg/prefix"
_SYS = "/usr/lib/libSystem.B.dylib"


def _run(install_dir, dep_prefixes=(), temp_prefixes=(), codesign=True):
    """Run the pass with mocked tools; return {file name: install_name_tool args}."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    tools = {"install_name_tool": "/usr/bin/install_name_tool"}
    if codesign:
        tools["codesign"] = "/usr/bin/codesign"
    with (
        mock.patch("cvcpkg.builder.shutil.which", side_effect=tools.get),
        mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
    ):
        _patch_macos_install_names(install_dir, dep_prefixes, temp_prefixes)
    edits = {
        c[-1].replace("\\", "/").rsplit("/", 1)[-1]: c[1:-1]
        for c in calls
        if c[0].endswith("install_name_tool")
    }
    signed = [c[-1] for c in calls if c[0].endswith("codesign")]
    return edits, signed


def _pairs(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_rewrites_ids_rpath_and_sibling_refs(tmp_path):
    lib = tmp_path / "lib"
    macho(
        lib / "libMagick++-7.Q16HDRI.5.dylib",
        filetype="dylib",
        install_id=f"{_BUILD}/libMagick++-7.Q16HDRI.5.dylib",
        loads=[f"{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib", _SYS],
    )
    macho(
        lib / "libMagickCore-7.Q16HDRI.10.dylib",
        filetype="dylib",
        install_id=f"{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib",
        loads=[_SYS],
    )
    edits, _ = _run(tmp_path)
    pp = edits["libMagick++-7.Q16HDRI.5.dylib"]
    core = edits["libMagickCore-7.Q16HDRI.10.dylib"]
    # id -> @rpath/<leaf> for every bundle dylib
    assert _pairs(pp, "-id") == ["@rpath/libMagick++-7.Q16HDRI.5.dylib"]
    assert _pairs(core, "-id") == ["@rpath/libMagickCore-7.Q16HDRI.10.dylib"]
    # @loader_path RPATH added (the $ORIGIN analog), once
    assert _pairs(pp, "-add_rpath") == ["@loader_path"]
    assert _pairs(core, "-add_rpath") == ["@loader_path"]
    # absolute reference to a SIBLING bundle dylib rewritten to @rpath
    i = pp.index("-change")
    assert pp[i + 1 : i + 3] == [
        f"{_BUILD}/libMagickCore-7.Q16HDRI.10.dylib",
        "@rpath/libMagickCore-7.Q16HDRI.10.dylib",
    ]
    # system references are never touched
    assert _SYS not in pp and _SYS not in core


def test_bin_reference_into_dependency_and_builder_rpath(tmp_path):
    # cmake +cvc.1: loads libcurl from curl's OWN build dir (its dylib id was
    # absolute then); +cvc.5: @rpath load, but the only LC_RPATH is the job's
    # deps prefix.  Both live in bin/, which the pass used to skip.
    deps = tmp_path / "deps"
    macho(deps / "lib" / "libcurl.4.dylib", filetype="dylib", install_id="@rpath/libcurl.4.dylib")
    inst = tmp_path / "install"
    macho(
        inst / "bin" / "cmake",
        loads=[f"{_CURL_TMP}/libcurl.4.dylib", _SYS],
        rpaths=[f"{_DEPS}/lib"],
    )
    macho(inst / "bin" / "ctest", loads=["@rpath/libcurl.4.dylib", _SYS], rpaths=[f"{_DEPS}/lib"])
    edits, signed = _run(inst, dep_prefixes=[deps], temp_prefixes=[_DEPS])

    cmake = edits["cmake"]
    i = cmake.index("-change")
    assert cmake[i + 1 : i + 3] == [f"{_CURL_TMP}/libcurl.4.dylib", "@rpath/libcurl.4.dylib"]
    for args in (cmake, edits["ctest"]):
        assert _pairs(args, "-delete_rpath") == [f"{_DEPS}/lib"]
        assert _pairs(args, "-add_rpath") == ["@loader_path/../lib"]
        assert "-id" not in args  # executables have no install name
    # every edited file is re-signed (arm64 will not run an invalid signature)
    assert sorted(s.replace("\\", "/").rsplit("/", 1)[-1] for s in signed) == ["cmake", "ctest"]


def test_foreign_builder_rpath_dropped_and_needed_rpath_added(tmp_path):
    inst = tmp_path / "install"
    macho(inst / "lib" / "libfoo.1.dylib", filetype="dylib", install_id="@rpath/libfoo.1.dylib")
    macho(
        inst / "libexec" / "foo" / "helper",
        loads=["@rpath/libfoo.1.dylib"],
        rpaths=["/Users/runner/work/libcvc-deps/libcvc-deps/prefix/lib", "/usr/lib/swift"],
    )
    edits, _ = _run(inst)
    helper = edits["helper"]
    assert _pairs(helper, "-delete_rpath") == [
        "/Users/runner/work/libcvc-deps/libcvc-deps/prefix/lib"
    ]
    assert _pairs(helper, "-add_rpath") == ["@loader_path/../../lib"]


def test_relocatable_tree_is_left_alone(tmp_path):
    inst = tmp_path / "install"
    macho(
        inst / "lib" / "libcurl.4.dylib",
        filetype="dylib",
        install_id="@rpath/libcurl.4.dylib",
        loads=["@rpath/libssl.3.dylib", _SYS],
        rpaths=["@loader_path"],
    )
    # @executable_path/../lib reaches lib/ from bin/ just as @loader_path/../lib does.
    macho(
        inst / "bin" / "cmake",
        loads=["@rpath/libcurl.4.dylib", _SYS],
        rpaths=["@executable_path/../lib"],
    )
    edits, signed = _run(inst)
    assert edits == {} and signed == []


def test_homebrew_reference_is_not_papered_over(tmp_path):
    # No dependency ships libzstd, so there is nothing to point @rpath at: the
    # reference stays (and the pack-time gate reports it).
    inst = tmp_path / "install"
    macho(
        inst / "lib" / "libboost_iostreams.dylib",
        filetype="dylib",
        install_id="@rpath/libboost_iostreams.dylib",
        loads=["/opt/homebrew/opt/zstd/lib/libzstd.1.dylib"],
        rpaths=["@loader_path"],
    )
    edits, _ = _run(inst)
    assert edits == {}


def test_noop_without_tools(tmp_path):
    macho(tmp_path / "bin" / "x", rpaths=["/tmp/cvcpkg-x-abcd1234/lib"])
    with (
        mock.patch("cvcpkg.builder.shutil.which", return_value=None),
        mock.patch("cvcpkg.builder.subprocess.run") as run,
    ):
        _patch_macos_install_names(tmp_path)  # must not raise
    run.assert_not_called()
