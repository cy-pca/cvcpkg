"""_patch_linux_rpath must make bundles relocatable ($ORIGIN) WITHOUT
destroying a wheel's bundled-native-lib RPATH (e.g. numpy's OpenBLAS in
$ORIGIN/../../numpy.libs). Regression for: `import numpy` failing with
"libscipy_openblas*.so: cannot open shared object file" after packaging."""

import subprocess
from unittest import mock

from cvcpkg.builder import _patch_linux_rpath


def _run_patch(tmp_path, so_relpath, existing_rpath):
    """Drop a fake .so with *existing_rpath*, run _patch_linux_rpath with a
    mocked patchelf, and return the value passed to --set-rpath."""
    so = tmp_path / "lib" / so_relpath
    so.parent.mkdir(parents=True, exist_ok=True)
    so.write_bytes(b"\x7fELF-fake")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "--print-rpath" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=existing_rpath + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        mock.patch("cvcpkg.builder.shutil.which", return_value="/usr/bin/patchelf"),
        mock.patch("cvcpkg.builder.subprocess.run", side_effect=fake_run),
    ):
        _patch_linux_rpath(tmp_path)

    set_calls = [c for c in calls if "--set-rpath" in c]
    assert set_calls, "expected a --set-rpath call"
    return set_calls[0][set_calls[0].index("--set-rpath") + 1]


def test_preserves_wheel_bundled_lib_rpath(tmp_path):
    # numpy's _core extension points at its bundled OpenBLAS.
    rpath = _run_patch(
        tmp_path,
        "python3.11/site-packages/numpy/_core/_multiarray_umath.so",
        "$ORIGIN/../../numpy.libs",
    )
    assert "numpy.libs" in rpath, f"bundled-lib RPATH dropped: {rpath!r}"
    assert rpath == "$ORIGIN:$ORIGIN/../../numpy.libs"


def test_drops_absolute_keeps_relative(tmp_path):
    rpath = _run_patch(tmp_path, "libfoo.so", "/tmp/build-xyz/lib:$ORIGIN/../lib")
    assert rpath == "$ORIGIN:$ORIGIN/../lib"  # absolute build path dropped


def test_empty_and_bare_origin_are_just_origin(tmp_path):
    assert _run_patch(tmp_path, "libempty.so", "") == "$ORIGIN"
    assert _run_patch(tmp_path, "libo.so", "$ORIGIN") == "$ORIGIN"
