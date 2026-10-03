"""_patch_elf_rpath_absolute bakes an ABSOLUTE RPATH (the merged <prefix>/lib)
into every ELF object of an installed prefix.  This is the OpenBSD relocation
mechanism: its ld.so ignores $ORIGIN, so the build-time $ORIGIN rewrite is inert
and an installed bundle can only resolve its (multi-hop) native chains via an
absolute RPATH or LD_LIBRARY_PATH.  Regression for cy-pca/cvcpkg#94 (h5py
extension -> libhdf5 -> libz unresolvable on OpenBSD without activation)."""

import subprocess
import sys
from pathlib import Path

import pytest

from cvcpkg.builder import _patch_elf_rpath_absolute


def _prefix(tmp_path: Path) -> Path:
    """A minimal installed-prefix layout with real lib dirs (the function's
    keep-in-prefix check calls Path.is_dir)."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib" / "python3.11" / "site-packages" / "h5py").mkdir(parents=True)
    return tmp_path


def _run(prefix: Path, files: dict[str, object], *, lib64: bool = False):
    """Drop each file in *files* (path -> existing rpath string, or the sentinel
    ``NOT_ELF``), run the pass with a mocked patchelf, and return
    {relpath: rpath_set}."""
    from unittest import mock

    for rel in files:
        p = prefix / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x7fELF-fake")
    lib_dirs = [prefix / "lib"] + ([prefix / "lib64"] if lib64 else [])
    if lib64:
        (prefix / "lib64").mkdir(exist_ok=True)

    set_calls: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        target = Path(cmd[-1])
        # .as_posix() so keys are forward-slash on every OS (the test runs on
        # Windows too, where str(relative_to) would yield backslashes).
        rel = target.relative_to(prefix).as_posix()
        spec = files.get(rel)
        if "--print-rpath" in cmd:
            if spec == "NOT_ELF":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not an ELF")
            return subprocess.CompletedProcess(cmd, 0, stdout=(spec or "") + "\n", stderr="")
        if "--set-rpath" in cmd:
            set_calls[rel] = cmd[cmd.index("--set-rpath") + 1]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run):
        n = _patch_elf_rpath_absolute(prefix, lib_dirs, "/usr/bin/patchelf")
    return n, set_calls


def test_sets_absolute_rpath_on_lib_and_extension_and_bin(tmp_path):
    prefix = _prefix(tmp_path)
    lib = str((prefix / "lib").resolve())
    n, calls = _run(
        prefix,
        {
            "lib/libhdf5.so.310": "$ORIGIN",
            "lib/python3.11/site-packages/h5py/_errors.so": "$ORIGIN:$ORIGIN/../../..",
            "bin/python3.11": "$ORIGIN/../lib",
        },
    )
    assert n == 3
    # Every object points at the absolute merged lib dir, $ORIGIN dropped.
    assert calls["lib/libhdf5.so.310"] == lib
    assert calls["lib/python3.11/site-packages/h5py/_errors.so"] == lib
    assert calls["bin/python3.11"] == lib


def test_includes_lib64_when_present(tmp_path):
    prefix = _prefix(tmp_path)
    lib = str((prefix / "lib").resolve())
    lib64 = str((prefix / "lib64").resolve())
    n, calls = _run(prefix, {"lib/libfoo.so": ""}, lib64=True)
    assert calls["lib/libfoo.so"] == f"{lib}:{lib64}"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="_patch_elf_rpath_absolute is openbsd-only; this case embeds an absolute "
    "path in a ':'-separated RPATH, and Windows drive-letter colons (C:\\...) break "
    "the split — never an issue on the openbsd target where paths carry no ':'.",
)
def test_preserves_in_prefix_absolute_but_drops_origin_and_dead(tmp_path):
    prefix = _prefix(tmp_path)
    lib = str((prefix / "lib").resolve())
    bundled = prefix / "lib" / "python3.11" / "site-packages" / "numpy.libs"
    bundled.mkdir(parents=True)
    existing = f"$ORIGIN/../../numpy.libs:{bundled}:/tmp/build-gone/lib"
    n, calls = _run(prefix, {"lib/python3.11/site-packages/numpy/_core.so": existing})
    # merged lib first; the real in-prefix bundled dir kept; $ORIGIN and the
    # dead /tmp/build-gone absolute dropped.
    assert calls["lib/python3.11/site-packages/numpy/_core.so"] == f"{lib}:{bundled}"


def test_skips_non_elf_files_in_bin(tmp_path):
    prefix = _prefix(tmp_path)
    n, calls = _run(prefix, {"lib/libreal.so": "", "bin/wrapper.sh": "NOT_ELF"})
    assert "bin/wrapper.sh" not in calls
    assert "lib/libreal.so" in calls
    assert n == 1


def test_noop_without_patchelf(tmp_path):
    prefix = _prefix(tmp_path)
    (prefix / "lib" / "libfoo.so").write_bytes(b"\x7fELF")
    assert _patch_elf_rpath_absolute(prefix, [prefix / "lib"], None) == 0


def test_noop_when_no_lib_dirs(tmp_path):
    # lib_dirs that do not exist are filtered out -> nothing to point at.
    assert _patch_elf_rpath_absolute(tmp_path, [tmp_path / "nope"], "/usr/bin/patchelf") == 0
