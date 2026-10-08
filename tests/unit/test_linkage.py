"""cvcpkg.linkage: reading ELF/Mach-O linkage and the relocatability audit.

The audit cases replay what the published cmake/curl/grpc/boost bundles
actually carried (2026-10-08), so each would have failed the pack."""

import struct

import pytest

from cvcpkg.linkage import (
    audit_tree,
    builder_path_reason,
    read_linkage,
    relative_to_prefixes,
    rewrite_elf_rpath,
)
from tests.unit._binfixtures import elf, macho, macho_slice

JOB = "/tmp/cvcpkg-builder/cvcpkg-job-cmake-p1zl8xjl/cvcpkg-prefix-cmake-i60b7plm"
CURL_TMP = "/private/var/folders/d8/hvxv/T/cvcpkg-curl-b1y2e409/install/lib"


# ── read_linkage ────────────────────────────────────────────────


@pytest.mark.parametrize("bits,little", [(64, True), (32, True), (64, False), (32, False)])
def test_reads_elf_needed_runpath_soname(tmp_path, bits, little):
    so = elf(
        tmp_path / "libx.so.1",
        kind="dyn",
        needed=["libcurl.so.4.8.0", "libc.so.6"],
        runpath=f"{JOB}/lib:$ORIGIN",
        soname="libx.so.1",
        bits=bits,
        little=little,
    )
    link = read_linkage(so)
    assert link.format == "elf" and link.kind == "shared"
    assert link.needed == ("libcurl.so.4.8.0", "libc.so.6")
    assert link.rpaths == (f"{JOB}/lib", "$ORIGIN")
    assert link.soname == "libx.so.1"
    assert link.dt_rpath is False


def test_elf_dt_rpath_and_kinds(tmp_path):
    assert read_linkage(elf(tmp_path / "a", rpath="/x")).dt_rpath is True
    assert read_linkage(elf(tmp_path / "exe")).kind == "executable"
    # ET_DYN with an interpreter is a PIE executable, not a library.
    assert read_linkage(elf(tmp_path / "pie", kind="pie")).kind == "executable"
    assert read_linkage(elf(tmp_path / "x.o", kind="rel")) is None


def test_reads_macho_loads_rpaths_id(tmp_path):
    exe = macho(
        tmp_path / "cmake",
        loads=["@rpath/libcurl.4.dylib", "/usr/lib/libSystem.B.dylib"],
        weak=["/usr/lib/libz.1.dylib"],
        rpaths=["/Users/runner/work/x/prefix/lib"],
    )
    link = read_linkage(exe)
    assert link.format == "macho" and link.kind == "executable"
    assert link.needed == (
        "@rpath/libcurl.4.dylib",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libz.1.dylib",
    )
    assert link.rpaths == ("/Users/runner/work/x/prefix/lib",)
    lib = read_linkage(
        macho(tmp_path / "libc.dylib", filetype="dylib", install_id="@rpath/libc.dylib")
    )
    assert lib.kind == "shared" and lib.soname == "@rpath/libc.dylib"
    big = read_linkage(macho(tmp_path / "be", loads=["/a/libq.dylib"], little=False))
    assert big.needed == ("/a/libq.dylib",)


def test_reads_universal_macho_as_union_of_slices(tmp_path):
    fat = macho(
        tmp_path / "libu.dylib",
        fat=[
            macho_slice(filetype="dylib", install_id="@rpath/libu.dylib", loads=["/a/libx.dylib"]),
            macho_slice(filetype="dylib", loads=["/b/liby.dylib"], rpaths=["@loader_path"]),
        ],
    )
    link = read_linkage(fat)
    assert link.needed == ("/a/libx.dylib", "/b/liby.dylib")
    assert link.rpaths == ("@loader_path",)
    assert link.soname == "@rpath/libu.dylib"


def test_non_objects_are_none(tmp_path):
    (tmp_path / "script").write_text("#!/bin/sh\necho hi\n")
    (tmp_path / "trunc").write_bytes(b"\x7fELF\x02\x01")
    (tmp_path / "fake").write_bytes(b"\xcf\xfa\xed\xfe-fake-macho")
    # A Java class file shares 0xCAFEBABE with a universal binary.
    (tmp_path / "A.class").write_bytes(struct.pack(">IHH", 0xCAFEBABE, 0, 52) + b"\0" * 64)
    obj = macho(tmp_path / "x.o", filetype="object", loads=["/tmp/x.dylib"])
    for name in ("script", "trunc", "fake", "A.class"):
        assert read_linkage(tmp_path / name) is None, name
    assert read_linkage(obj) is None
    assert read_linkage(tmp_path / "missing") is None


@pytest.mark.parametrize("bits,little", [(64, True), (32, False)])
def test_rewrite_elf_rpath_in_place(tmp_path, bits, little):
    so = elf(
        tmp_path / "libx.so",
        kind="dyn",
        needed=["libz.so.1"],
        rpath=f"{JOB}/lib",
        runpath=f"{JOB}/lib:/usr/lib",
        soname="libx.so",
        bits=bits,
        little=little,
    )
    size = so.stat().st_size
    assert rewrite_elf_rpath(so, "$ORIGIN")
    link = read_linkage(so)
    assert link.rpaths == ("$ORIGIN", "$ORIGIN")  # both tags rewritten
    assert link.needed == ("libz.so.1",) and link.soname == "libx.so"
    assert so.stat().st_size == size


def test_rewrite_elf_rpath_never_grows(tmp_path):
    so = elf(tmp_path / "x", runpath="/p")
    before = so.read_bytes()
    assert not rewrite_elf_rpath(so, "$ORIGIN/../lib")
    assert so.read_bytes() == before
    assert not rewrite_elf_rpath(elf(tmp_path / "y"), "")  # no RPATH to rewrite
    (tmp_path / "z").write_bytes(b"not an elf")
    assert not rewrite_elf_rpath(tmp_path / "z", "")


# ── builder paths ───────────────────────────────────────────────


def test_builder_path_reason():
    # Loader paths are POSIX strings on every host, so are the prefixes here.
    prefix = "/srv/builds/job42/deps"
    assert builder_path_reason(f"{prefix}/lib", [prefix]) == "this build's scratch prefix"
    assert builder_path_reason(f"{JOB}/lib") is not None
    assert builder_path_reason(f"{CURL_TMP}/libcurl.4.dylib") is not None
    assert builder_path_reason("/Users/runner/work/libcvc-deps/libcvc-deps/prefix/lib")
    assert builder_path_reason("/home/runner/work/x/x/prefix/lib")
    assert builder_path_reason("/var/tmp/build/lib")
    for ok in ("/usr/lib", "/usr/local/lib", "/usr/pkg/lib", "/boot/system/lib", "$ORIGIN", "lib"):
        assert builder_path_reason(ok) is None, ok


def test_relative_to_prefixes_prefers_longest():
    job = "/srv/builds/job42"
    assert relative_to_prefixes(f"{job}/install/lib", [job, f"{job}/install"]) == "lib"
    assert relative_to_prefixes(job, [job + "/"]) == "."
    assert relative_to_prefixes(f"{job}x/lib", [job]) is None


# ── audit_tree ──────────────────────────────────────────────────


def _fields(result):
    return {(f.path, f.field, f.value) for f in result.findings}


def test_audit_linux_cmake_cvc6_runpath_fails(tmp_path):
    for exe in ("cmake", "ctest", "cpack"):
        elf(tmp_path / "bin" / exe, needed=["libcurl.so.4.8.0"], runpath=f"{JOB}/lib")
    res = audit_tree(tmp_path, "linux")
    assert res.objects == 3 and not res.ok
    assert ("bin/cmake", "RUNPATH", f"{JOB}/lib") in _fields(res)


def test_audit_relocatable_elf_passes(tmp_path):
    elf(tmp_path / "bin" / "cmake", needed=["libcurl.so.4.8.0"], runpath="$ORIGIN/../lib")
    elf(
        tmp_path / "lib" / "libcurl.so.4.8.0",
        kind="dyn",
        runpath="$ORIGIN",
        soname="libcurl.so.4.8.0",
    )
    # System dirs stay legal on ELF (NetBSD pkgsrc, FreeBSD ports).
    elf(tmp_path / "bin" / "tool", runpath="/usr/pkg/lib:/usr/local/lib")
    assert audit_tree(tmp_path, "netbsd").ok


def test_audit_elf_needed_with_build_path(tmp_path):
    # A library without a SONAME makes its consumers record the literal path.
    elf(tmp_path / "bin" / "cmake", needed=[f"{JOB}/lib/libcurl.so.12.0"])
    assert ("bin/cmake", "NEEDED", f"{JOB}/lib/libcurl.so.12.0") in _fields(
        audit_tree(tmp_path, "openbsd")
    )


def test_audit_this_builds_prefix_outside_tmp(tmp_path):
    deps = "/srv/builds/job42/prefix"
    elf(tmp_path / "bin" / "x", runpath=f"{deps}/lib")
    assert audit_tree(tmp_path, "linux").ok
    assert not audit_tree(tmp_path, "linux", temp_prefixes=[deps]).ok


def test_audit_macos_cmake_cvc5_and_curl(tmp_path):
    macho(
        tmp_path / "bin" / "cmake",
        loads=["@rpath/libcurl.4.dylib", "/usr/lib/libSystem.B.dylib"],
        rpaths=["/Users/runner/work/libcvc-deps/libcvc-deps/prefix/lib"],
    )
    macho(tmp_path / "bin" / "curl", loads=[f"{CURL_TMP}/libcurl.4.dylib"])
    fields = _fields(audit_tree(tmp_path, "macos"))
    assert (
        "bin/cmake",
        "LC_RPATH",
        "/Users/runner/work/libcvc-deps/libcvc-deps/prefix/lib",
    ) in fields
    assert ("bin/curl", "LC_LOAD_DYLIB", f"{CURL_TMP}/libcurl.4.dylib") in fields
    assert len(fields) == 2  # /usr/lib and @rpath are fine


def test_audit_macos_homebrew_and_absolute_id(tmp_path):
    macho(
        tmp_path / "lib" / "libboost_iostreams.dylib",
        filetype="dylib",
        install_id="@rpath/libboost_iostreams.dylib",
        loads=["/opt/homebrew/opt/zstd/lib/libzstd.1.dylib"],
    )
    macho(tmp_path / "lib" / "libq.dylib", filetype="dylib", install_id="/opt/local/lib/libq.dylib")
    reasons = {f.value: f.reason for f in audit_tree(tmp_path, "macos").findings}
    assert "Homebrew" in reasons["/opt/homebrew/opt/zstd/lib/libzstd.1.dylib"]
    assert "MacPorts" in reasons["/opt/local/lib/libq.dylib"]


def test_audit_allowlist_never_excuses_builder_paths(tmp_path):
    macho(tmp_path / "bin" / "v", loads=["/Library/Frameworks/Vendor.framework/Vendor"])
    assert not audit_tree(tmp_path, "macos").ok
    assert audit_tree(tmp_path, "macos", allow=["/Library/Frameworks/Vendor.framework/"]).ok
    macho(tmp_path / "bin" / "w", rpaths=["/tmp/cvcpkg-x-abcd1234/lib"])
    assert not audit_tree(tmp_path, "macos", allow=["/tmp/", "/Library/Frameworks/"]).ok


def test_audit_skips_platforms_without_loader_paths(tmp_path):
    elf(tmp_path / "bin" / "x", runpath=f"{JOB}/lib")
    for plat in ("windows", "wasm", "wasi", "cosmo", "any"):
        assert audit_tree(tmp_path, plat).ok
