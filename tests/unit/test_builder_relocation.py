"""Builder paths out of executables, and the pack-time gate that refuses them.

cmake's bin/{cmake,ctest,cpack} shipped RUNPATH
/tmp/cvcpkg-builder/cvcpkg-job-cmake-.../lib on linux for +cvc.1..+cvc.6 —
static and shared — because the relocation pass only looked at lib/*.so*, and
nothing checked the packed result.  _scrub_elf_rpaths covers the rest of the
tree; check_relocatable (run by stage_bundle) fails the pack on what is left."""

import os
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
import yaml

from cvcpkg import builder
from cvcpkg.builder import (
    PackError,
    _complete_elf_rpaths,
    _runtime_dep_libraries,
    _scrub_elf_rpaths,
    check_relocatable,
    stage_bundle,
)
from cvcpkg.linkage import library_index, read_linkage
from tests.unit._binfixtures import elf, macho

# Loader paths are POSIX strings on every host.
JOB = "/tmp/cvcpkg-builder/cvcpkg-job-cmake-p1zl8xjl"
DEPS = f"{JOB}/cvcpkg-prefix-cmake-i60b7plm"


def _scrub(root, platform="linux", temp_prefixes=(DEPS,), skip=lambda _p: False):
    """Run the scrub with a mocked patchelf; return the patchelf calls made."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run):
        _scrub_elf_rpaths(root, temp_prefixes, "/x/patchelf", platform=platform, skip=skip)
    return calls


def _rpaths(path):
    return read_linkage(path).rpaths


def test_deps_prefix_runpath_becomes_origin_relative(tmp_path):
    inst = tmp_path / "install"
    for exe in ("cmake", "ctest", "cpack"):
        elf(inst / "bin" / exe, needed=["libcurl.so.4.8.0"], runpath=f"{DEPS}/lib")
    elf(inst / "libexec" / "cmake" / "helper", runpath=f"{DEPS}/lib")
    # Rewritten in place: no patchelf needed (or called).
    assert _scrub(inst) == []
    for exe in ("cmake", "ctest", "cpack"):
        link = read_linkage(inst / "bin" / exe)
        assert link.rpaths == ("$ORIGIN/../lib",)
        assert link.needed == ("libcurl.so.4.8.0",)
    assert _rpaths(inst / "libexec" / "cmake" / "helper") == ("$ORIGIN/../../lib",)


@pytest.mark.skipif(not shutil.which("readelf"), reason="readelf cross-check")
def test_in_place_rewrite_is_valid_elf(tmp_path):
    exe = elf(tmp_path / "install" / "bin" / "cmake", needed=["libc.so.6"], runpath=f"{DEPS}/lib")
    _scrub(tmp_path / "install")
    out = subprocess.run(["readelf", "-d", str(exe)], capture_output=True, text=True).stdout
    assert "Library runpath: [$ORIGIN/../lib]" in out
    assert "Shared library: [libc.so.6]" in out


def test_foreign_builder_dirs_dropped_system_dirs_kept(tmp_path):
    inst = tmp_path / "install"
    other = "/tmp/cvcpkg-builder/cvcpkg-job-curl-1j775f9y/cvcpkg-curl-llhwr8nk/install/lib"
    elf(inst / "bin" / "curl", runpath=other)
    elf(inst / "bin" / "tool", runpath=f"{other}:/usr/local/lib:$ORIGIN/../lib")
    fine = elf(inst / "bin" / "fine", runpath="$ORIGIN/../lib:/usr/pkg/lib")
    before = fine.read_bytes()
    _scrub(inst)
    assert _rpaths(inst / "bin" / "curl") == ()  # emptied search path
    assert _rpaths(inst / "bin" / "tool") == ("/usr/local/lib", "$ORIGIN/../lib")
    assert fine.read_bytes() == before  # nothing to change: not touched at all


def test_dt_rpath_stays_dt_rpath(tmp_path):
    inst = tmp_path / "install"
    elf(inst / "bin" / "old", rpath=f"{DEPS}/lib")
    _scrub(inst)
    link = read_linkage(inst / "bin" / "old")
    assert link.rpaths == ("$ORIGIN/../lib",) and link.dt_rpath


def test_growth_falls_back_to_patchelf_except_on_netbsd(tmp_path):
    # Growing .dynstr adds a PT_LOAD that NetBSD's ld.elf_so refuses to map.
    inst = tmp_path / "install"
    exe = elf(inst / "bin" / "x", runpath="/p/lib")
    before = exe.read_bytes()
    assert _scrub(inst, "netbsd", temp_prefixes=("/p",)) == []
    assert exe.read_bytes() == before
    (call,) = _scrub(inst, "linux", temp_prefixes=("/p",))
    assert call[:3] == ["/x/patchelf", "--set-rpath", "$ORIGIN/../lib"]


def test_openbsd_drops_instead_of_origin(tmp_path):
    # No $ORIGIN in OpenBSD's ld.so: the installer bakes an absolute RPATH.
    inst = tmp_path / "install"
    elf(inst / "bin" / "cmake", runpath=f"{DEPS}/lib")
    _scrub(inst, "openbsd")
    assert _rpaths(inst / "bin" / "cmake") == ()


def test_skip_is_honoured(tmp_path):
    inst = tmp_path / "install"
    so = elf(inst / "lib" / "libfoo.so.1", kind="dyn", runpath=f"{DEPS}/lib")
    _scrub(inst, skip=lambda p: p.name.startswith("libfoo"))
    assert _rpaths(so) == (f"{DEPS}/lib",)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_read_only_binary_is_rewritten_and_mode_restored(tmp_path):
    exe = elf(tmp_path / "install" / "bin" / "openssl", runpath=f"{DEPS}/lib")
    exe.chmod(0o555)
    _scrub(tmp_path / "install")
    assert _rpaths(exe) == ("$ORIGIN/../lib",)
    assert stat.S_IMODE(os.stat(exe).st_mode) == 0o555


def _complete(root, deps=()):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run):
        _complete_elf_rpaths(root, library_index(deps), "/x/patchelf")
    return {c[-1].replace("\\", "/").split("/install/", 1)[-1]: c[1:-1] for c in calls}


def test_complete_adds_the_entries_shipped_libraries_need(tmp_path):
    # openssl +cvc.6: bin/openssl with no RPATH loaded the host's libssl.so.3.
    inst, deps = tmp_path / "install", tmp_path / "deps"
    elf(inst / "bin" / "openssl", needed=["libssl.so.3", "libcrypto.so.3", "libc.so.6"])
    elf(inst / "lib" / "libssl.so.3", kind="dyn", needed=["libcrypto.so.3"], runpath="$ORIGIN")
    elf(inst / "lib" / "libcrypto.so.3", kind="dyn", runpath="$ORIGIN")
    elf(
        inst / "lib" / "engines-3" / "padlock.so",
        kind="dyn",
        needed=["libcrypto.so.3"],
        runpath="$ORIGIN",
    )
    elf(
        inst / "lib" / "ossl-modules" / "legacy.so",
        kind="dyn",
        needed=["libcrypto.so.3"],
        rpath="$ORIGIN",
    )
    elf(deps / "lib" / "libz.so.1", kind="dyn")
    elf(inst / "bin" / "sqlite3", needed=["libz.so.1"], runpath="/usr/local/lib")
    calls = _complete(inst, (deps,))
    assert calls == {
        "bin/openssl": ["--set-rpath", "$ORIGIN/../lib"],
        "lib/engines-3/padlock.so": ["--set-rpath", "$ORIGIN:$ORIGIN/.."],
        "lib/ossl-modules/legacy.so": ["--set-rpath", "$ORIGIN:$ORIGIN/..", "--force-rpath"],
        "bin/sqlite3": ["--set-rpath", "/usr/local/lib:$ORIGIN/../lib"],
    }  # libssl/libcrypto already reach each other; libc is the host's


def test_complete_needs_patchelf(tmp_path, capsys):
    elf(tmp_path / "install" / "bin" / "openssl", needed=["libssl.so.3"])
    elf(tmp_path / "install" / "lib" / "libssl.so.3", kind="dyn")
    with mock.patch("cvcpkg.builder.subprocess.run") as run:
        _complete_elf_rpaths(tmp_path / "install", {}, None)
    run.assert_not_called()
    assert "could not add $ORIGIN/../lib to bin/openssl" in capsys.readouterr().out


# ── which dependencies count: the runtime closure ──────────────


def _installed(prefix, name, files, required=(), provides=()):
    """Install dependency *name* into *prefix* as a builder job does: its files,
    then its manifest at share/libcvc-deps/<name>/manifest.yaml."""
    for f in files:
        elf(prefix / f, kind="dyn" if ".so" in f else "exec")
    manifest = {
        "schema_version": 3,
        "bundle": {"name": name},
        "contents": {"files": list(files)},
        "dependencies": {"required": [{"name": r, "version": "^1"} for r in required]},
    }
    if provides:
        manifest["provides"] = list(provides)
    meta = prefix / "share" / "libcvc-deps" / name
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def _job_prefix(deps):
    """A job prefix for openblas-on-linux: build deps and the runtime closure, mixed."""
    # Build-only: the gfortran toolchain keeps its own runtime in lib64/.
    _installed(deps, "gfortran", ["bin/gfortran", "lib64/libgcc_s.so.1", "lib64/libgfortran.so.5"])
    _installed(deps, "patchelf", ["bin/patchelf"])
    # Runtime: gcc-runtime, and curl -> openssl to have a transitive edge.
    _installed(deps, "gcc-runtime", ["lib/libgfortran.so.5", "lib/libquadmath.so.0"])
    _installed(deps, "curl", ["lib/libcurl.so.4"], required=["openssl"])
    _installed(deps, "openssl", ["lib/libssl.so.3", "lib/engines-3/padlock.so"])


def test_runtime_dep_libraries_walks_the_runtime_closure(tmp_path):
    deps = tmp_path / "deps"
    _job_prefix(deps)
    assert _runtime_dep_libraries((deps,), ["gcc-runtime", "curl"]) == {
        "libgfortran.so.5": {"lib"},
        "libquadmath.so.0": {"lib"},
        "libcurl.so.4": {"lib"},
        "libssl.so.3": {"lib"},  # through curl's own manifest
        "padlock.so": {"lib/engines-3"},
    }  # never gfortran's lib64/: a build dependency is not installed beside the bundle
    assert _runtime_dep_libraries((deps,), []) == {}
    assert _runtime_dep_libraries((None,), ["curl"]) == {}
    # dependencies.required entries, current and legacy ("[org/]name") shapes.
    assert builder._required_names([{"name": "zlib"}, "acme/curl", {"version": "^1"}]) == [
        "zlib",
        "curl",
    ]


def test_runtime_dep_libraries_falls_back_to_the_whole_prefix(tmp_path, capsys):
    deps = tmp_path / "deps"
    _job_prefix(deps)
    everything = library_index([deps])
    assert everything["libgcc_s.so.1"] == {"lib64"}
    # Caller does not know the closure.
    assert _runtime_dep_libraries((deps,), None) == everything
    # A dependency with no manifest here (an old flat-manifest bundle): the
    # closure cannot be told apart, so count everything, as before.
    elf(deps / "lib" / "libz.so.1", kind="dyn")
    assert _runtime_dep_libraries((deps,), ["curl", "zlib"]) == library_index([deps])
    assert "runtime dependency zlib has no installed manifest" in capsys.readouterr().out


def test_runtime_dep_libraries_resolves_a_provided_name(tmp_path):
    deps = tmp_path / "deps"
    _installed(deps, "openblas", ["lib/libopenblas.so.0"], provides=["blas"])
    assert _runtime_dep_libraries((deps,), ["blas"]) == {"libopenblas.so.0": {"lib"}}


def test_make_relocatable_reaches_runtime_deps_only(tmp_path):
    # gcc-runtime +cvc.1: libgfortran got $ORIGIN/../lib64 because its gfortran
    # build dependency, in the same job prefix, keeps libgcc_s.so.1 there.
    ctx = _ctx(
        tmp_path,
        "linux",
        "shared",
        {"build": ["gfortran", "patchelf"], "runtime": [{"name": "curl", "version": "^8"}]},
    )
    _job_prefix(ctx.prefix)
    lib = ctx.install_dir / "lib"
    elf(lib / "libgfortran.so.5.0.0", kind="dyn", needed=["libgcc_s.so.1"], runpath="$ORIGIN")
    elf(ctx.install_dir / "bin" / "tool", needed=["libcurl.so.4", "libgcc_s.so.1"])
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        mock.patch.object(builder, "_find_patchelf", return_value="/x/patchelf"),
        mock.patch.object(builder, "_patch_elf_rpath"),
        mock.patch.object(builder, "_scrub_elf_rpaths"),
        mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
    ):
        builder._make_relocatable(ctx)
    # Only curl's libcurl needs reaching; libgcc_s.so.1 is the host's.
    assert calls == [
        ["/x/patchelf", "--set-rpath", "$ORIGIN/../lib", str(ctx.install_dir / "bin" / "tool")]
    ]


def _ctx(tmp_path, platform, link, depends=None):
    inst = tmp_path / "install"
    return SimpleNamespace(
        platform=platform,
        link=link,
        install_dir=inst,
        prefix=tmp_path / "deps",
        build_prefix=tmp_path / "build-prefix",
        recipe=SimpleNamespace(raw={"depends": depends or {}}),
    )


@pytest.mark.parametrize("link", ["shared", "static"])
def test_make_relocatable_scrubs_executables_for_every_link(tmp_path, link):
    ctx = _ctx(tmp_path, "linux", link)
    with (
        mock.patch.object(builder, "_find_patchelf", return_value="/x/patchelf"),
        mock.patch.object(builder, "_patch_elf_rpath") as lib_pass,
        mock.patch.object(builder, "_scrub_elf_rpaths") as scrub,
    ):
        builder._make_relocatable(ctx)
    assert scrub.call_count == 1
    args, kwargs = scrub.call_args
    assert args[0] == ctx.install_dir and ctx.prefix in args[1]
    assert lib_pass.called == (link == "shared")
    # lib/*.so* belongs to _patch_elf_rpath on shared builds, to the scrub otherwise.
    so = ctx.install_dir / "lib" / "libfoo.so.1"
    assert kwargs["skip"](so) == (link == "shared")
    assert kwargs["skip"](ctx.install_dir / "bin" / "foo") is False


@pytest.mark.parametrize("platform,runs", [("linux", True), ("freebsd", False), ("netbsd", False)])
def test_make_relocatable_completes_rpaths_on_linux_only(tmp_path, platform, runs):
    ctx = _ctx(tmp_path, platform, "shared", {"build": ["cmake"], "runtime": ["zlib"]})
    dep_libs = {"libz.so.1": {"lib"}}
    with (
        mock.patch.object(builder, "_find_patchelf", return_value="/x/patchelf"),
        mock.patch.object(builder, "_patch_elf_rpath"),
        mock.patch.object(builder, "_scrub_elf_rpaths"),
        mock.patch.object(builder, "_runtime_dep_libraries", return_value=dep_libs) as libs,
        mock.patch.object(builder, "_complete_elf_rpaths") as complete,
    ):
        builder._make_relocatable(ctx)
    assert complete.called == runs
    if runs:
        libs.assert_called_once_with((ctx.prefix,), ["zlib"])  # runtime deps only
        complete.assert_called_once_with(ctx.install_dir, dep_libs, "/x/patchelf")


def test_make_relocatable_macos_passes_deps_prefix(tmp_path):
    ctx = _ctx(tmp_path, "macos", "static")
    with mock.patch.object(builder, "_patch_macos_install_names") as mac:
        builder._make_relocatable(ctx)
    mac.assert_called_once_with(
        ctx.install_dir, (ctx.prefix,), (ctx.prefix, ctx.build_prefix, ctx.install_dir)
    )


# ── the gate ────────────────────────────────────────────────────


def _bundle(platform):
    return {"name": "cmake", "version": "3.31.7+cvc.8", "platform": platform, "arch": "x86_64"}


def test_stage_bundle_refuses_builder_runpath(tmp_path):
    inst = tmp_path / "install"
    elf(inst / "bin" / "cmake", needed=["libcurl.so.4.8.0"], runpath=f"{DEPS}/lib")
    with pytest.raises(PackError) as exc:
        stage_bundle(inst, {"bundle": _bundle("linux")}, tmp_path / "staging")
    msg = str(exc.value)
    assert "bin/cmake: RUNPATH" in msg and f"{DEPS}/lib" in msg
    assert "cmake (linux/x86_64)" in msg


def test_stage_bundle_accepts_relocatable_tree(tmp_path, capsys):
    inst = tmp_path / "install"
    elf(inst / "bin" / "cmake", needed=["libcurl.so.4.8.0"], runpath="$ORIGIN/../lib")
    staging = tmp_path / "staging"
    stage_bundle(inst, {"bundle": _bundle("linux")}, staging)
    assert (staging / "share" / "libcvc-deps" / "cmake" / "manifest.yaml").is_file()
    assert "relocatable OK — 1 binary object(s) checked" in capsys.readouterr().out


def test_stage_bundle_treats_install_dir_as_a_build_path(tmp_path):
    # pack --from-prefix hands its own stage dir over as install_dir.
    inst = "/srv/ci/libcvc/stage"
    root = tmp_path / "stage"
    elf(root / "bin" / "app", runpath=f"{inst}/lib")
    check_relocatable(root, _bundle("linux"))  # unknown dir: not flagged
    with pytest.raises(PackError):
        check_relocatable(root, _bundle("linux"), temp_prefixes=[inst])


def test_stage_bundle_refuses_unreachable_shipped_library(tmp_path):
    inst, deps = tmp_path / "install", tmp_path / "deps"
    elf(inst / "bin" / "cmake", needed=["libcurl.so.4.8.0"])  # no RUNPATH
    elf(deps / "lib" / "libcurl.so.4.8.0", kind="dyn")
    with pytest.raises(PackError, match="bin/cmake: NEEDED libcurl.so.4.8.0"):
        stage_bundle(inst, {"bundle": _bundle("linux")}, tmp_path / "s1", dep_prefixes=(deps,))
    stage_bundle(inst, {"bundle": _bundle("linux")}, tmp_path / "s2")  # deps unknown: host's


def test_stage_bundle_demands_only_runtime_closure_libraries(tmp_path):
    inst, deps = tmp_path / "install", tmp_path / "deps"
    _job_prefix(deps)
    elf(inst / "bin" / "tool", needed=["libgcc_s.so.1"])  # no RUNPATH
    bundle = _bundle("linux")
    # gfortran is only a build dependency: its libgcc_s.so.1 is not installed
    # beside the bundle, so the host's copy is the right one.
    manifest = {"bundle": bundle, "dependencies": {"required": [{"name": "gcc-runtime"}]}}
    stage_bundle(inst, manifest, tmp_path / "s1", dep_prefixes=(deps,))
    # Without the manifest's dependency list every library in the prefix counts.
    with pytest.raises(PackError, match="NEEDED libgcc_s.so.1 .shipped in lib64/"):
        stage_bundle(inst, {"bundle": bundle}, tmp_path / "s2", dep_prefixes=(deps,))
    # A runtime dependency's library must be reachable.
    elf(inst / "bin" / "tool", needed=["libquadmath.so.0"])
    with pytest.raises(PackError, match="bin/tool: NEEDED libquadmath.so.0"):
        stage_bundle(inst, manifest, tmp_path / "s3", dep_prefixes=(deps,))


def test_stage_bundle_linkage_allow(tmp_path):
    inst = tmp_path / "install"
    macho(inst / "bin" / "v", loads=["/Library/Frameworks/Vendor.framework/Vendor"])
    with pytest.raises(PackError, match="outside the OS"):
        stage_bundle(inst, {"bundle": _bundle("macos")}, tmp_path / "s1")
    stage_bundle(
        inst,
        {"bundle": _bundle("macos")},
        tmp_path / "s2",
        linkage_allow=["/Library/Frameworks/Vendor.framework/"],
    )
