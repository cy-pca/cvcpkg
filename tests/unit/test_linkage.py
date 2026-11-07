"""cvcpkg.linkage: reading ELF/Mach-O linkage and the relocatability audit.

The audit cases replay what the published cmake/curl/grpc/boost bundles
actually carried (2026-10-08), so each would have failed the pack."""

import struct

import pytest

from cvcpkg.linkage import (
    _Sonames,
    audit_tree,
    builder_path_reason,
    host_package_libraries,
    index_files,
    is_system_soname,
    library_index,
    origin_dirs,
    read_linkage,
    relative_to_prefixes,
    relativize_elf_needed,
    rewrite_elf_rpath,
    system_libraries,
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
    assert link.weak == ("/usr/lib/libz.1.dylib",)
    # Loaded strongly as well as weakly: dyld needs it.
    both = macho(tmp_path / "both", loads=["@rpath/libq.dylib"], weak=["@rpath/libq.dylib"])
    assert read_linkage(both).weak == ()
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
            macho_slice(
                filetype="dylib",
                loads=["/b/liby.dylib"],
                weak=["/a/libx.dylib", "@rpath/libw.dylib"],
                rpaths=["@loader_path"],
            ),
        ],
    )
    link = read_linkage(fat)
    assert link.needed == ("/a/libx.dylib", "/b/liby.dylib", "@rpath/libw.dylib")
    assert link.rpaths == ("@loader_path",)
    assert link.soname == "@rpath/libu.dylib"
    # Strong in one slice is strong: only libw is weak in every slice that loads it.
    assert link.weak == ("@rpath/libw.dylib",)


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


@pytest.mark.parametrize("bits,little", [(64, True), (32, False)])
def test_relativize_elf_needed_points_at_the_basename(tmp_path, bits, little):
    # OpenBSD: libxcb has no SONAME and meson linked it by absolute path.
    so = elf(
        tmp_path / "libxkbcommon-x11.so.0.0.0",
        kind="dyn",
        needed=[f"{JOB}/lib/libxcb.so.2.0", "libc.so.100.3", "/usr/lib/libm.so.10.1"],
        runpath="$ORIGIN",
        bits=bits,
        little=little,
    )
    before = so.read_bytes()
    assert relativize_elf_needed(so, under=[f"{JOB}/"]) == [f"{JOB}/lib/libxcb.so.2.0"]
    link = read_linkage(so)
    # Only the scratch-prefix entry moves; a system path outside *under* stays.
    assert link.needed == ("libxcb.so.2.0", "libc.so.100.3", "/usr/lib/libm.so.10.1")
    assert link.rpaths == ("$ORIGIN",)
    after = so.read_bytes()
    assert len(after) == len(before)
    # .dynstr is untouched: the absolute string is still there in full.
    assert f"{JOB}/lib/libxcb.so.2.0".encode() in after
    # Idempotent.
    assert relativize_elf_needed(so, under=[f"{JOB}/"]) == []


def test_relativize_elf_needed_ignores_non_elf(tmp_path):
    (tmp_path / "z").write_bytes(b"not an elf")
    assert relativize_elf_needed(tmp_path / "z") == []
    assert relativize_elf_needed(elf(tmp_path / "s", needed=["libz.so.1"])) == []


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


# ── shipped libraries must be reachable (linux) ─────────────────


def _openssl_cvc6(root):
    """The shape of openssl 3.4.1+cvc.6's linux bundle."""
    elf(root / "bin" / "openssl", needed=["libssl.so.3", "libcrypto.so.3", "libc.so.6"])
    elf(root / "lib" / "libcrypto.so.3", kind="dyn", runpath="$ORIGIN", soname="libcrypto.so.3")
    elf(
        root / "lib" / "libssl.so.3",
        kind="dyn",
        needed=["libcrypto.so.3"],
        runpath="$ORIGIN",
        soname="libssl.so.3",
    )
    elf(
        root / "lib" / "engines-3" / "padlock.so",
        kind="dyn",
        needed=["libcrypto.so.3"],
        runpath="$ORIGIN",
    )


def test_library_index_and_origin_dirs(tmp_path):
    _openssl_cvc6(tmp_path)
    try:
        (tmp_path / "lib" / "libssl.so").symlink_to("libssl.so.3")
    except (OSError, NotImplementedError):
        # Windows without SeCreateSymbolicLinkPrivilege (WinError 1314).
        pytest.skip("cannot create symlinks on this host")
    index = library_index([tmp_path])
    assert index["libssl.so.3"] == {"lib"} and index["libssl.so"] == {"lib"}
    assert index["padlock.so"] == {"lib/engines-3"}
    assert origin_dirs("bin", ["$ORIGIN/../lib", "/usr/lib", "${ORIGIN}"]) == {"lib", "bin"}
    assert origin_dirs("lib/engines-3", ["$ORIGIN/.."]) == {"lib"}
    assert origin_dirs("bin", ["$ORIGIN/../../outside"]) == set()


def test_index_files_matches_a_walk(tmp_path):
    # An installed manifest's contents.files indexes like the tree it lists.
    _openssl_cvc6(tmp_path)
    files = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()]
    assert index_files([*files, "libtop.so.1", "share/doc/README"]) == {
        **library_index([tmp_path]),
        "libtop.so.1": {"."},
    }
    merged = library_index([tmp_path], {"libz.so.1": ["lib"], "libssl.so.3": {"lib64"}})
    assert merged["libz.so.1"] == {"lib"} and merged["libssl.so.3"] == {"lib", "lib64"}


def test_audit_linux_openssl_cvc6_reaches_the_hosts_libssl(tmp_path):
    _openssl_cvc6(tmp_path)
    found = {(f.path, f.value) for f in audit_tree(tmp_path, "linux").findings}
    assert found == {
        ("bin/openssl", "libssl.so.3"),
        ("bin/openssl", "libcrypto.so.3"),
        ("lib/engines-3/padlock.so", "libcrypto.so.3"),
    }
    # libc.so.6 is not shipped, so it is the host's to provide: never reported.


def test_audit_linux_reachable_and_dependency_libraries(tmp_path):
    root, deps = tmp_path / "bundle", tmp_path / "deps"
    elf(root / "bin" / "openssl", needed=["libssl.so.3"], runpath="$ORIGIN/../lib")
    elf(root / "lib" / "libssl.so.3", kind="dyn", runpath="$ORIGIN")
    elf(
        root / "lib" / "engines-3" / "padlock.so",
        kind="dyn",
        needed=["libssl.so.3"],
        runpath="$ORIGIN:$ORIGIN/..",
    )
    assert audit_tree(root, "linux").ok
    # A library from a runtime dependency counts as shipped too.
    elf(deps / "lib" / "libcurl.so.4.8.0", kind="dyn")
    elf(root / "bin" / "cmake", needed=["libcurl.so.4.8.0"])
    assert audit_tree(root, "linux").ok  # without the deps prefix: the host's problem
    (f,) = audit_tree(root, "linux", dep_libs=library_index([deps])).findings
    assert (f.path, f.field, f.value) == ("bin/cmake", "NEEDED", "libcurl.so.4.8.0")


def test_audit_reachability_is_linux_only(tmp_path):
    _openssl_cvc6(tmp_path)
    for plat in ("freebsd", "netbsd", "openbsd", "haiku"):
        assert audit_tree(tmp_path, plat).ok, plat


# ── libraries nothing declares ──────────────────────────────────


def _ffmpeg_lgpl_cvc1(root):
    """ffmpeg-lgpl 7.1.2+cvc.1 on macOS: libavcodec loaded the shared pack-all
    prefix's iconv through @rpath, and the recipe never declared iconv."""
    macho(
        root / "lib" / "libavcodec.61.dylib",
        filetype="dylib",
        install_id="@rpath/libavcodec.61.dylib",
        loads=[
            "@rpath/libavutil.59.dylib",
            "@rpath/libiconv.2.dylib",
            "/usr/lib/libSystem.B.dylib",
            "/System/Library/Frameworks/VideoToolbox.framework/Versions/A/VideoToolbox",
        ],
        rpaths=["@loader_path"],
    )
    macho(
        root / "lib" / "libavutil.59.dylib",
        filetype="dylib",
        install_id="@rpath/libavutil.59.dylib",
    )


def test_audit_macos_ffmpeg_lgpl_cvc1_undeclared_iconv(tmp_path):
    root, deps = tmp_path / "bundle", tmp_path / "deps"
    _ffmpeg_lgpl_cvc1(root)
    macho(deps / "lib" / "libiconv.2.dylib", filetype="dylib")
    prefix = library_index([deps])
    # Runtime closure unknown: not checked -- which is how cvc.1 packed.
    assert audit_tree(root, "macos", prefix_libs=prefix).ok
    res = audit_tree(root, "macos", dep_libs={}, prefix_libs=prefix)
    (f,) = res.findings
    assert (f.path, f.field, f.value) == (
        "lib/libavcodec.61.dylib",
        "LC_LOAD_DYLIB",
        "@rpath/libiconv.2.dylib",
    )
    assert "only the build prefix has it, in lib/" in f.reason
    assert res.undeclared == {"libiconv.2.dylib"}
    # cvc.2 declares iconv: its dylib is in the closure.
    assert audit_tree(root, "macos", dep_libs=index_files(["lib/libiconv.2.dylib"])).ok
    # A deps-prefix library no manifest lists may be an unlisted dependency's.
    assert audit_tree(root, "macos", dep_libs={}, prefix_libs=prefix, unlisted_libs=prefix).ok
    # In no prefix at all: still missing on a clean Mac.
    (f,) = audit_tree(root, "macos", dep_libs={}).findings
    assert f.reason == "neither this bundle nor a runtime dependency ships it"
    # A library the consumer supplies is vouched for by the value the gate reports.
    assert audit_tree(root, "macos", dep_libs={}, allow=["@rpath/libiconv."]).ok
    assert not audit_tree(root, "macos", dep_libs={}, allow=["/usr/local/lib/"]).ok


def test_audit_macos_rpath_frameworks_and_os_search_paths(tmp_path):
    root = tmp_path / "bundle"
    macho(
        root / "bin" / "viewer",
        loads=["@rpath/QtCore.framework/Versions/A/QtCore", "@rpath/libcvc.dylib"],
        rpaths=["@executable_path/../lib"],
    )
    macho(root / "lib" / "libcvc.dylib", filetype="dylib", install_id="@rpath/libcvc.dylib")
    qt = index_files(
        [
            "lib/QtCore.framework/Versions/A/QtCore",
            "lib/QtCore.framework/Versions/A/Resources/Info.plist",
        ]
    )
    assert qt == {"QtCore": {"lib/QtCore.framework/Versions/A"}}
    assert audit_tree(root, "macos", dep_libs=qt).ok
    # The framework binary's name alone, at another path, is not the one loaded.
    other = index_files(["lib/QtCore.framework/Versions/5/QtCore"])
    (f,) = audit_tree(root, "macos", dep_libs=other).findings
    assert f.value == "@rpath/QtCore.framework/Versions/A/QtCore"
    # swiftc's -rpath /usr/lib/swift: the OS's Swift runtime is found there.
    macho(root / "bin" / "tool", loads=["@rpath/libswiftCore.dylib"], rpaths=["/usr/lib/swift"])
    assert audit_tree(root, "macos", dep_libs=qt).ok


def test_audit_macos_weak_rpath_loads_follow_the_build_prefix(tmp_path):
    root, deps = tmp_path / "bundle", tmp_path / "deps"
    macho(
        root / "bin" / "app",
        weak=["@rpath/libswift_Concurrency.dylib", "@rpath/libiconv.2.dylib"],
        rpaths=["@executable_path/../lib"],
    )
    # Optional by design: dyld starts the program without either.
    assert audit_tree(root, "macos", dep_libs={}).ok
    # But one the build linked from the prefix is still a leak.
    macho(deps / "lib" / "libiconv.2.dylib", filetype="dylib")
    (f,) = audit_tree(root, "macos", dep_libs={}, prefix_libs=library_index([deps])).findings
    assert (f.path, f.field, f.value) == (
        "bin/app",
        "LC_LOAD_WEAK_DYLIB",
        "@rpath/libiconv.2.dylib",
    )
    assert "only the build prefix has it, in lib/" in f.reason


def test_audit_elf_gettext_arm64_undeclared_iconv(tmp_path):
    root, deps = tmp_path / "bundle", tmp_path / "deps"
    so = root / "lib" / "libgettextlib-0.22.so"
    elf(
        so,
        kind="dyn",
        needed=["libiconv.so.2", "libgcc_s.so.1", "libc.so.6", "libfoo.so.3"],
        runpath="$ORIGIN",
    )
    elf(deps / "lib" / "libiconv.so.2", kind="dyn")
    # A build-only toolchain in the same prefix ships OS libraries too.
    elf(deps / "lib64" / "libgcc_s.so.1", kind="dyn")
    elf(deps / "sysroot" / "lib" / "libc.so.6", kind="dyn")
    prefix = library_index([deps])
    for plat in ("linux", "freebsd", "openbsd", "netbsd", "haiku"):
        res = audit_tree(root, plat, dep_libs={}, prefix_libs=prefix)
        assert _fields(res) == {("lib/libgettextlib-0.22.so", "NEEDED", "libiconv.so.2")}, plat
        assert res.undeclared == {"libiconv.so.2"}
    # libfoo.so.3 is in no prefix: the host's to provide, as before.
    assert audit_tree(root, "linux", dep_libs={}).ok
    # Declared, it is shipped beside the bundle -- and must be reachable on linux.
    elf(so, kind="dyn", needed=["libiconv.so.2"])
    declared = index_files(["lib/libiconv.so.2"])
    (f,) = audit_tree(root, "linux", dep_libs=declared, prefix_libs=prefix).findings
    assert "no RUNPATH entry reaches it" in f.reason
    assert audit_tree(root, "openbsd", dep_libs=declared, prefix_libs=prefix).ok
    # What the build host's own library directories hold is the OS's.
    assert audit_tree(
        root, "netbsd", dep_libs={}, prefix_libs=prefix, system_libs={"libiconv.so.2"}
    ).ok
    assert audit_tree(root, "netbsd", dep_libs={}, prefix_libs=prefix, allow=["libiconv.so"]).ok


def test_is_system_soname():
    for name in (
        "libc.so.6",
        "libc.so.100.3",
        "libm.so.6",
        "libpthread.so.0",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
        "ld-musl-x86_64.so.1",
        "ld64.so.2",
        "libnss_files.so.2",
        "libgcc_s.so.1",
        "libstdc++.so.6",
        "libc++.so.1",
        "libGL.so.1",
        "libroot.so",
    ):
        assert is_system_soname(name), name
    for name in ("libiconv.so.2", "libz.so.1", "libcrypto.so.3", "libgfortran.so.5", "libcurl.so"):
        assert not is_system_soname(name), name


def test_system_libraries_reads_only_the_os_dirs(tmp_path):
    for f in (
        "lib/x86_64-linux-gnu/libz.so.1",
        "usr/lib/libbz2.so.1.0",
        "usr/lib/python3/dist-packages/_x.so",
        "usr/local/lib/libiconv.so.2",
        "usr/lib/README",
        "usr/X11R6/lib/libX11.so.18.0",
    ):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    assert system_libraries("linux", tmp_path) == {"libz.so.1", "libbz2.so.1.0"}
    assert system_libraries("openbsd", tmp_path) == {"libbz2.so.1.0", "libX11.so.18.0"}
    assert system_libraries("macos", tmp_path) == set()


def test_library_index_includes_dylibs_and_framework_binaries(tmp_path):
    macho(tmp_path / "lib" / "libz.1.dylib", filetype="dylib")
    framework = tmp_path / "lib" / "QtGui.framework" / "Versions" / "A"
    macho(framework / "QtGui", filetype="dylib")
    (framework / "Headers").mkdir()
    (framework / "Headers" / "qgui.h").write_text("")
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "zlib.h").write_text("")
    assert library_index([tmp_path]) == {
        "libz.1.dylib": {"lib"},
        "QtGui": {"lib/QtGui.framework/Versions/A"},
    }


# ── the BSD host's packages ─────────────────────────────────────


def test_sonames_match_as_openbsd_ld_so_does():
    shipped = {"libavcodec.so.61.19": {"lib"}, "libz.so.7.2": {"lib"}, "libgmp.so.15.0": {"lib"}}
    obsd = _Sonames({**shipped, "libpcre2-8.so.0.13.0": {"lib"}}, "openbsd")
    # Unversioned: the highest of any version (ffmpeg's NEEDED libavcodec.so).
    assert obsd.match("libavcodec.so") == "libavcodec.so.61.19"
    # Same major, minor at least the one linked against.
    assert obsd.match("libz.so.7.1") == "libz.so.7.2"
    assert obsd.match("libz.so.7.3") is None
    assert obsd.match("libgmp.so.11.1") is None
    # Not an OpenBSD-style name: only that exact name.
    assert obsd.match("libpcre2-8.so.0.7") is None
    assert obsd.get("libz.so.7") == {"lib"}
    # Everywhere else a NEEDED name is the file name.
    assert _Sonames(shipped, "freebsd").match("libz.so.7.1") is None
    assert _Sonames(shipped, "linux").match("libavcodec.so") is None


def test_host_package_libraries(tmp_path):
    for f in (
        "usr/local/lib/libiconv.so.7.1",
        "usr/local/lib/libiconv.la",
        "usr/local/lib/perl5/5.42/mach/CORE/libperl.so.5.42",
        "usr/local/lib/python3.11/lib-dynload/_ssl.so",
        "usr/pkg/lib/libldap.so.6",
        "opt/vendor/lib/libv.so.1",
    ):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    assert host_package_libraries("openbsd", tmp_path) == {"libiconv.so.7.1": {"/usr/local/lib"}}
    # An object's absolute RUNPATH into the package tree is read too; others are not.
    core = "/usr/local/lib/perl5/5.42/mach/CORE"
    assert host_package_libraries("freebsd", tmp_path, [core, "/opt/vendor/lib"]) == {
        "libiconv.so.7.1": {"/usr/local/lib"},
        "libperl.so.5.42": {core},
    }
    assert host_package_libraries("netbsd", tmp_path) == {"libldap.so.6": {"/usr/pkg/lib"}}
    assert host_package_libraries("linux", tmp_path) == {}


def test_audit_openbsd_host_ports_libraries(tmp_path):
    # ffmpeg-lgpl 7.1.2+cvc.1 and gmp 6.3.0+cvc.12 on OpenBSD, as published.
    root = tmp_path / "bundle"
    elf(
        root / "lib" / "libavcodec.so.61.19",
        kind="dyn",
        needed=["libavutil.so", "libiconv.so.7.1", "libz.so.7.1", "libc.so.100.3"],
        runpath="$ORIGIN",
    )
    elf(root / "lib" / "libavutil.so.59.39", kind="dyn")
    elf(root / "lib" / "libgmp.so.15.0", kind="dyn")
    elf(root / "lib" / "libgmpxx.so.11.0", kind="dyn", needed=["libgmp.so.11.1"])
    host = {
        "libiconv.so.7.1": {"/usr/local/lib"},
        "libgmp.so.11.1": {"/usr/local/lib"},
        "libz.so.7.1": {"/usr/local/lib"},
    }
    system = {"libz.so.7.1", "libc.so.100.3"}  # base wins over a package's copy
    # cvcpkg's own iconv is declared, but it is libiconv.so.8.1.
    declared = index_files(["lib/libiconv.so.8.1"])
    res = audit_tree(root, "openbsd", dep_libs=declared, system_libs=system, host_libs=host)
    assert _fields(res) == {
        ("lib/libavcodec.so.61.19", "NEEDED", "libiconv.so.7.1"),
        ("lib/libgmpxx.so.11.0", "NEEDED", "libgmp.so.11.1"),
    }
    assert {f.reason for f in res.findings} == {
        "only the build host's packages have it, in /usr/local/lib/; "
        "neither this bundle nor a runtime dependency ships it"
    }
    assert res.host_packages == {"libiconv.so.7.1", "libgmp.so.11.1"} and not res.undeclared
    allow = ["libiconv.so.7", "libgmp.so.11"]
    assert audit_tree(
        root, "openbsd", allow=allow, dep_libs=declared, system_libs=system, host_libs=host
    ).ok
    # The closure unknown: not checked.
    assert audit_tree(root, "openbsd", system_libs=system, host_libs=host).ok
