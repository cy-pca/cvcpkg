"""Staged console scripts must be executable from an installed prefix.

pip writes every console script with a shebang naming the exact interpreter
that ran the install -- inside cvcpkg that is the job's ephemeral build
prefix (#!/tmp/cvcpkg-builder/cvcpkg-job-<recipe>-<id>/bin/python3.11), a
path that exists on no consumer machine, so the script "cannot execute"
from any installed prefix.  stage_bundle rewrites those shebangs to
#!/usr/bin/env pythonX.Y on the staged copy.  Regression for: staged
bin/cython and bin/pybind11-config dead on arrival (numpy's build shims a
private cython wrapper; contourpy sidesteps its dead pybind11-config via
pkg-config)."""

import os
import subprocess
import sys

from cvcpkg.builder import _rewrite_shebangs, create_archive, stage_bundle
from cvcpkg.installer import extract_bundle

JOB_PREFIX = "/tmp/cvcpkg-builder/cvcpkg-job-pybind11-cp311-ab12cd34"
BODY = b"import sys\nsys.exit(0)\n"


def _script(root, relpath, shebang, body=BODY, mode=0o755):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"#!" + shebang.encode() + b"\n" + body)
    path.chmod(mode)
    return path


def test_rewrites_job_prefix_shebang(tmp_path):
    script = _script(tmp_path, "bin/pybind11-config", f"{JOB_PREFIX}/bin/python3.11")
    _rewrite_shebangs(tmp_path)
    assert script.read_bytes() == b"#!/usr/bin/env python3.11\n" + BODY
    assert (script.stat().st_mode & 0o777) == 0o755


def test_preserves_free_threaded_interpreter_name(tmp_path):
    script = _script(tmp_path, "bin/cython", f"{JOB_PREFIX}/bin/python3.13t")
    _rewrite_shebangs(tmp_path)
    assert script.read_bytes().startswith(b"#!/usr/bin/env python3.13t\n")


def test_custom_prefix_rewritten_only_via_temp_prefixes(tmp_path):
    # A user-chosen deps prefix (cvcpkg pack --prefix ~/mydeps) has no
    # cvcpkg-* component, so only the caller can identify it as ephemeral.
    shebang = "/home/user/mydeps/bin/python3.12"
    script = _script(tmp_path, "bin/tool", shebang)

    _rewrite_shebangs(tmp_path)
    assert script.read_bytes().startswith(b"#!" + shebang.encode())

    _rewrite_shebangs(tmp_path, temp_prefixes=("/home/user/mydeps",))
    assert script.read_bytes().startswith(b"#!/usr/bin/env python3.12\n")


def test_system_and_env_shebangs_untouched(tmp_path):
    lines = ("/bin/sh", "/usr/bin/perl", "/usr/bin/env python3")
    scripts = [_script(tmp_path, f"bin/s{i}", line) for i, line in enumerate(lines)]
    _rewrite_shebangs(tmp_path)
    for script, line in zip(scripts, lines, strict=True):
        assert script.read_bytes() == b"#!" + line.encode() + b"\n" + BODY


def test_binaries_and_symlinks_skipped(tmp_path):
    elf = tmp_path / "bin" / "native-tool"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64)
    exe = tmp_path / "bin" / "shim.exe"
    exe.write_bytes(b"MZ\x90\x00" + f"#!{JOB_PREFIX}\\python.exe".encode())
    real = _script(tmp_path, "bin/real", f"{JOB_PREFIX}/bin/python3.11")
    alias = tmp_path / "bin" / "alias"
    alias.symlink_to(real.name)

    _rewrite_shebangs(tmp_path)

    assert elf.read_bytes().startswith(b"\x7fELF")
    assert exe.read_bytes().startswith(b"MZ")
    assert alias.is_symlink()
    assert real.read_bytes().startswith(b"#!/usr/bin/env python3.11\n")


def test_argful_shebang_left_with_warning(tmp_path, capsys):
    shebang = f"{JOB_PREFIX}/bin/python3.11 -sE"
    script = _script(tmp_path, "bin/argful", shebang)
    _rewrite_shebangs(tmp_path)
    assert script.read_bytes() == b"#!" + shebang.encode() + b"\n" + BODY
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "argful" in out


def test_readonly_script_rewritten_and_mode_restored(tmp_path):
    script = _script(tmp_path, "bin/frozen", f"{JOB_PREFIX}/bin/python3.11", mode=0o555)
    _rewrite_shebangs(tmp_path)
    assert script.read_bytes().startswith(b"#!/usr/bin/env python3.11\n")
    assert (script.stat().st_mode & 0o777) == 0o555


def test_scan_is_bounded_to_script_dirs(tmp_path):
    # Payload outside bin/sbin/libexec is not scanned -- the pass stays
    # O(script dirs), not O(prefix), and never rewrites package data.
    shebang = f"{JOB_PREFIX}/bin/python3.11"
    data = _script(tmp_path, "share/pkg/helper.py", shebang)
    _rewrite_shebangs(tmp_path)
    assert data.read_bytes().startswith(b"#!" + shebang.encode())


def test_stage_bundle_rewrites_staging_not_install_dir(tmp_path):
    install_dir = tmp_path / "install"
    staging = tmp_path / "staging"
    staging.mkdir()
    source = _script(install_dir, "bin/tool", f"{JOB_PREFIX}/bin/python3.11")

    stage_bundle(install_dir, {"name": "toolpkg"}, staging, temp_prefixes=(JOB_PREFIX,))

    staged = staging / "bin" / "tool"
    assert staged.read_bytes().startswith(b"#!/usr/bin/env python3.11\n")
    # pack --from-prefix hands us a tree the caller owns: never mutate it.
    assert source.read_bytes().startswith(b"#!" + JOB_PREFIX.encode())


def test_packed_bundle_bin_entry_executes_from_fresh_prefix(tmp_path):
    """End-to-end acceptance: pack a script package, install it into a fresh
    prefix, and execute its bin entry -- the exact flow that used to die with
    'cannot execute: required file not found'."""
    install_dir = tmp_path / "install"
    _script(
        install_dir,
        "bin/tool",
        f"{JOB_PREFIX}/bin/python3.11",
        body=b"print('tool-ok', 1 + 1)\n",
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    stage_bundle(install_dir, {"name": "toolpkg"}, staging, temp_prefixes=(JOB_PREFIX,))
    archive, _sha, _size = create_archive(
        staging, tmp_path / "dist", "toolpkg", "1.0.0+cvc.1", "linux", "x86_64", "release", "shared"
    )

    fresh = tmp_path / "fresh-prefix"
    extract_bundle(archive, fresh)

    # The prefix ships its column's interpreter under bin/; model that with a
    # python3.11 name resolving to the test runner's own interpreter.
    (fresh / "bin" / "python3.11").symlink_to(sys.executable)

    tool = fresh / "bin" / "tool"
    assert tool.read_bytes().startswith(b"#!/usr/bin/env python3.11\n")
    env = dict(os.environ, PATH=f"{fresh / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    result = subprocess.run([str(tool)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "tool-ok 2"
