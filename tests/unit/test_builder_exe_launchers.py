"""Staged Windows console-script launchers must run from an installed prefix.

pip builds each Scripts/<tool>.exe as three concatenated segments -- a stub
PE image, a ``#!<python path>`` line naming the exact interpreter that ran
the install, and a zip holding ``__main__.py`` (distlib's ScriptMaker).
Inside cvcpkg that interpreter path is the job's ephemeral build prefix, so
on any consumer machine the launcher exits without running anything: the
POSIX shebang disease (test_builder_shebangs), but with the path inside the
binary.  stage_bundle splices those shebangs to ``#!<launcher_dir>\\..\\
python.exe`` on the staged copy -- a literal prefix the stub resolves
against its own directory (distlib SUPPORT_RELATIVE_PATH), reaching the
interpreter at the merged prefix root with no PATH contract at all.

The stub re-finds the appended zip from the END of the file and takes the
line immediately preceding it as the shebang, so the splice may change the
segment's length; distlib writes the zip's offsets relative to its own
start, and both the stub and the child's zipimport recompute the prepended
delta.  Verified end-to-end against real pip 25.0.1/26.2.1 launchers (both
stubs carry the ``<launcher_dir>`` handler) executed under wine from a
relocated prefix."""

import io
import os
import subprocess
import sys
import zipfile

import pytest

from cvcpkg.builder import (
    _parse_launcher_shebang,
    _rewrite_exe_launchers,
    create_archive,
    stage_bundle,
)

JOB_PREFIX = "C:\\Users\\builder\\AppData\\Local\\Temp\\cvcpkg-job-pybind11-cp312-ab12cd34"
BODY = b"import sys\nsys.exit(0)\n"

# A believable stub: PE magic, embedded NULs, and a decoy ``#!`` proving the
# rewrite anchors on the LAST shebang before the archive, like the stub does.
STUB = b"MZ\x90\x00\x03" + b"\x00" * 32 + b"PE\x00\x00fake-stub #!decoy " + b"\x00" * 64


def _launcher_bytes(shebang, stub=STUB, newline=b"\r\n", body=BODY, main="__main__.py"):
    """Concatenate stub + shebang + zip exactly the way distlib does: the
    zip is built in its own stream, so its offsets are archive-relative."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        zf.writestr(main, body)
    return stub + b"#!" + shebang.encode() + newline + stream.getvalue()


def _launcher(root, relpath, shebang, **kwargs):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_launcher_bytes(shebang, **kwargs))
    return path


def _segments(data):
    """Split launcher bytes into (stub, shebang segment, zip data)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    arc_start = min(zi.header_offset for zi in zf.infolist())
    sheb_start = data.rfind(b"#!", max(0, arc_start - 512), arc_start)
    return data[:sheb_start], data[sheb_start:arc_start], data[arc_start:]


def test_rewrites_job_prefix_launcher(tmp_path):
    exe = _launcher(tmp_path, "Scripts/cython.exe", f"{JOB_PREFIX}\\python\\python.exe")
    original_stub, _, original_zip = _segments(exe.read_bytes())

    _rewrite_exe_launchers(tmp_path)

    data = exe.read_bytes()
    stub, seg, zip_data = _segments(data)
    assert seg == b"#!<launcher_dir>\\..\\python.exe\r\n"
    # The splice must leave the PE image and the archive byte-identical.
    assert stub == original_stub
    assert zip_data == original_zip
    assert zipfile.ZipFile(io.BytesIO(data)).read("__main__.py") == BODY


def test_quoted_shebang_and_newline_preserved(tmp_path):
    # pip quotes the interpreter when its path contains spaces; the plain-\n
    # ending is what pip 25's ScriptMaker actually writes.
    quoted = f'"{JOB_PREFIX}\\python dir\\python.exe"'
    exe = _launcher(tmp_path, "Scripts/tool.exe", quoted, newline=b"\n")
    _rewrite_exe_launchers(tmp_path)
    _, seg, _ = _segments(exe.read_bytes())
    assert seg == b"#!<launcher_dir>\\..\\python.exe\n"


def test_unquoted_path_with_spaces_parsed_like_the_stub(tmp_path):
    # The stub delimits the interpreter at the last ``.exe``, not at
    # whitespace, so an unquoted spaced path is a valid launcher shebang.
    exe = _launcher(tmp_path, "Scripts/tool.exe", f"{JOB_PREFIX}\\python dir\\python.exe")
    _rewrite_exe_launchers(tmp_path)
    _, seg, _ = _segments(exe.read_bytes())
    assert seg == b"#!<launcher_dir>\\..\\python.exe\r\n"


def test_preserves_interpreter_basename(tmp_path):
    gui = _launcher(tmp_path, "Scripts/gui.exe", f"{JOB_PREFIX}\\python\\pythonw.exe")
    ft = _launcher(tmp_path, "Scripts/ft.exe", f"{JOB_PREFIX}\\python\\python3.13t.exe")
    _rewrite_exe_launchers(tmp_path)
    assert _segments(gui.read_bytes())[1] == b"#!<launcher_dir>\\..\\pythonw.exe\r\n"
    assert _segments(ft.read_bytes())[1] == b"#!<launcher_dir>\\..\\python3.13t.exe\r\n"


def test_nested_script_dir_climbs_to_prefix_root(tmp_path):
    exe = _launcher(tmp_path, "Scripts/sub/tool.exe", f"{JOB_PREFIX}\\python\\python.exe")
    _rewrite_exe_launchers(tmp_path)
    assert _segments(exe.read_bytes())[1] == b"#!<launcher_dir>\\..\\..\\python.exe\r\n"


def test_custom_prefix_rewritten_only_via_temp_prefixes(tmp_path):
    # A user-chosen deps prefix has no cvcpkg-* component; only the caller
    # can identify it as ephemeral.  Matching is slash- and case-insensitive.
    shebang = "D:\\MyDeps\\Python\\python.exe"
    exe = _launcher(tmp_path, "Scripts/tool.exe", shebang)

    _rewrite_exe_launchers(tmp_path)
    assert _segments(exe.read_bytes())[1] == b"#!" + shebang.encode() + b"\r\n"

    _rewrite_exe_launchers(tmp_path, temp_prefixes=("d:/mydeps",))
    assert _segments(exe.read_bytes())[1] == b"#!<launcher_dir>\\..\\python.exe\r\n"


def test_relocatable_and_system_launchers_untouched(tmp_path):
    lines = (
        "<launcher_dir>\\..\\python.exe",  # already ours: the pass is idempotent
        "/usr/bin/env python",
        "C:\\Python312\\python.exe",
    )
    exes = [_launcher(tmp_path, f"Scripts/s{i}.exe", line) for i, line in enumerate(lines)]
    before = [e.read_bytes() for e in exes]
    _rewrite_exe_launchers(tmp_path)
    assert [e.read_bytes() for e in exes] == before


def test_non_launcher_exes_untouched(tmp_path):
    plain = tmp_path / "Scripts" / "native.exe"
    plain.parent.mkdir(parents=True)
    plain.write_bytes(b"MZ\x90\x00" + b"\x00" * 128)  # no appended archive
    # Appended zip but no __main__.py: an installer payload, not a launcher.
    payload = _launcher(
        tmp_path, "Scripts/data.exe", f"{JOB_PREFIX}\\python\\python.exe", main="data.txt"
    )
    # Appended __main__.py zip but no shebang between stub and archive.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        zf.writestr("__main__.py", BODY)
    frozen = tmp_path / "Scripts" / "frozen.exe"
    frozen.write_bytes(b"MZ\x90\x00" + b"\x00" * 700 + stream.getvalue())
    not_exe = tmp_path / "Scripts" / "tool.cmd"
    not_exe.write_bytes(b"@echo off\r\n" + JOB_PREFIX.encode() + b"\r\n")

    before = [p.read_bytes() for p in (plain, payload, frozen, not_exe)]
    _rewrite_exe_launchers(tmp_path)
    assert [p.read_bytes() for p in (plain, payload, frozen, not_exe)] == before


def test_argful_launcher_left_with_warning(tmp_path, capsys):
    shebang = f"{JOB_PREFIX}\\python\\python.exe -sE"
    exe = _launcher(tmp_path, "Scripts/argful.exe", shebang)
    _rewrite_exe_launchers(tmp_path)
    assert _segments(exe.read_bytes())[1] == b"#!" + shebang.encode() + b"\r\n"
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "argful" in out


def test_scan_is_bounded_to_launcher_dirs(tmp_path):
    exe = _launcher(tmp_path, "share/pkg/tool.exe", f"{JOB_PREFIX}\\python\\python.exe")
    before = exe.read_bytes()
    _rewrite_exe_launchers(tmp_path)
    assert exe.read_bytes() == before


def test_readonly_launcher_rewritten_and_mode_restored(tmp_path):
    exe = _launcher(tmp_path, "Scripts/frozen.exe", f"{JOB_PREFIX}\\python\\python.exe")
    exe.chmod(0o555)
    _rewrite_exe_launchers(tmp_path)
    assert _segments(exe.read_bytes())[1] == b"#!<launcher_dir>\\..\\python.exe\r\n"
    if sys.platform != "win32":
        assert (exe.stat().st_mode & 0o777) == 0o555


def test_parse_launcher_shebang_forms():
    cases = {
        b"#!C:\\py\\python.exe\r\n": ("C:\\py\\python.exe", ""),
        b'#!"C:\\p y\\python.exe"\n': ("C:\\p y\\python.exe", ""),
        b"#!C:\\py\\python.exe -sE\r\n": ("C:\\py\\python.exe", "-sE"),
        b"#!/usr/bin/env python\n": ("/usr/bin/env", "python"),
        b"#!<launcher_dir>\\..\\python.exe\n": ("<launcher_dir>\\..\\python.exe", ""),
    }
    for seg, expected in cases.items():
        assert _parse_launcher_shebang(seg) == expected, seg
    assert _parse_launcher_shebang(b"#!no trailing newline") is None
    assert _parse_launcher_shebang(b"#!\xff\xfe bad utf8\n") is None


def test_stage_bundle_rewrites_staging_not_install_dir(tmp_path):
    install_dir = tmp_path / "install"
    staging = tmp_path / "staging"
    staging.mkdir()
    source = _launcher(install_dir, "Scripts/tool.exe", f"{JOB_PREFIX}\\python\\python.exe")

    stage_bundle(install_dir, {"name": "toolpkg"}, staging, temp_prefixes=(JOB_PREFIX,))

    staged = staging / "Scripts" / "tool.exe"
    assert _segments(staged.read_bytes())[1] == b"#!<launcher_dir>\\..\\python.exe\r\n"
    # pack --from-prefix hands us a tree the caller owns: never mutate it.
    assert _segments(source.read_bytes())[1] == (
        b"#!" + JOB_PREFIX.encode() + b"\\python\\python.exe\r\n"
    )


def test_windows_pack_path_carries_rewritten_launcher(tmp_path):
    """The windows pack path end to end: stage, then archive via
    create_archive's zip branch, and the .zip must carry the relocatable
    launcher with its appended archive still readable."""
    install_dir = tmp_path / "install"
    _launcher(install_dir, "Scripts/tool.exe", f"{JOB_PREFIX}\\python\\python.exe")

    staging = tmp_path / "staging"
    staging.mkdir()
    stage_bundle(install_dir, {"name": "toolpkg"}, staging, temp_prefixes=(JOB_PREFIX,))
    archive, _sha, _size = create_archive(
        staging,
        tmp_path / "dist",
        "toolpkg",
        "1.0.0+cvc.1",
        "windows",
        "x86_64",
        "release",
        "shared",
    )

    assert archive.suffix == ".zip"
    with zipfile.ZipFile(archive) as bundle:
        # On a Windows runner the archiver derives entry names with
        # backslashes; accept either separator.
        (member,) = [n for n in bundle.namelist() if n.replace("\\", "/") == "Scripts/tool.exe"]
        packed = bundle.read(member)
    stub, seg, _ = _segments(packed)
    assert seg == b"#!<launcher_dir>\\..\\python.exe\r\n"
    assert stub == STUB
    assert zipfile.ZipFile(io.BytesIO(packed)).read("__main__.py") == BODY


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="needs real pip to generate a real distlib launcher .exe",
)
def test_real_pip_generated_launcher_rewritten(tmp_path):
    """Acceptance on the real thing: have pip build a genuine launcher for a
    hand-authored wheel, then rewrite it.  The stub pip embeds must survive
    byte-identical and the appended archive must stay readable."""
    pytest.importorskip("pip")
    wheel = tmp_path / "demo_tool-1.0-py3-none-any.whl"
    dist_info = "demo_tool-1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("demo_tool.py", "def main() -> int:\n    return 0\n")
        zf.writestr(
            f"{dist_info}/METADATA",
            "Metadata-Version: 2.1\nName: demo-tool\nVersion: 1.0\n",
        )
        zf.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr(
            f"{dist_info}/entry_points.txt",
            "[console_scripts]\ndemo-tool = demo_tool:main\n",
        )
        record = "".join(
            f"{name},,\n"
            for name in (
                "demo_tool.py",
                f"{dist_info}/METADATA",
                f"{dist_info}/WHEEL",
                f"{dist_info}/entry_points.txt",
                f"{dist_info}/RECORD",
            )
        )
        zf.writestr(f"{dist_info}/RECORD", record)
    prefix = tmp_path / "prefix"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-warn-script-location",
            "--prefix",
            str(prefix),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    exe = prefix / "Scripts" / "demo-tool.exe"
    assert exe.is_file(), "pip did not generate a launcher exe"
    original_stub, original_seg, original_zip = _segments(exe.read_bytes())
    assert original_seg.startswith(b"#!")

    _rewrite_exe_launchers(
        prefix,
        temp_prefixes=(sys.prefix, sys.exec_prefix, os.path.dirname(sys.executable)),
    )

    data = exe.read_bytes()
    stub, seg, zip_data = _segments(data)
    interp_base = os.path.basename(sys.executable)
    nl = b"\r\n" if original_seg.endswith(b"\r\n") else b"\n"
    assert seg == b"#!<launcher_dir>\\..\\" + interp_base.encode() + nl
    assert stub == original_stub
    assert zip_data == original_zip
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
