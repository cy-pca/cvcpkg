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

import ast
import os
import stat
import subprocess
import sys

import pytest

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
    if sys.platform != "win32":  # Windows has no POSIX perms (mode reads 0o666)
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


def _polyglot(root, relpath, interp, mode=0o755):
    """pip's /bin/sh wrapper, used when the interpreter path is too long for
    the kernel's shebang limit (~128 bytes) or contains a space."""
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"#!/bin/sh\n" b"'''exec' " + interp.encode() + b' "$0" "$@"\n' b"' '''\n" + BODY
    )
    path.chmod(mode)
    return path


# The exact prefix shape that triggered this in the wild.  pip switches to the
# polyglot form once the interpreter path passes the kernel's 128-byte
# BINPRM_BUF_SIZE, and that path is
# <work root>/cvcpkg-job-<name>-<id>/cvcpkg-prefix-<name>-<id>/bin/pythonX.Y --
# so the BUILDER contributes as much as the recipe name does.
LONG_PREFIX = (
    "/tmp/cvcpkg-builder/cvcpkg-job-trove-classifiers-cp311-b07zsnjz"
    "/cvcpkg-prefix-trove-classifiers-cp311-2nqo44fa"
)

# prettyhatemachine's work root is 15 chars longer than the /tmp builders', which
# is enough to flip a recipe across the limit on its own: torch-cp311 stays plain
# at 101 bytes on a /tmp builder, but torch-cp311-cuda -- five characters longer,
# and buildable ONLY on this host -- reaches 126 and goes polyglot.
CUDA_HOST_PREFIX = (
    "/var/lib/cvcpkg-builder/cvcpkg-org/cvcpkg-job-torch-cp311-cuda-jhb2rnq8"
    "/cvcpkg-prefix-torch-cp311-cuda-mlp9p6a4"
)


def test_rewrites_sh_polyglot_interpreter(tmp_path):
    # Regression: the dead path is on line TWO, so the line-one pass saw only a
    # harmless "#!/bin/sh" and shipped bin/trove-classifiers dead on arrival.
    script = _polyglot(tmp_path, "bin/trove-classifiers", f"{LONG_PREFIX}/bin/python3.11")
    _rewrite_shebangs(tmp_path)
    text = script.read_bytes().decode()
    assert text.splitlines()[0] == "#!/bin/sh"
    assert text.splitlines()[1] == "'''exec' /usr/bin/env python3.11 \"$0\" \"$@\""
    assert text.splitlines()[2] == "' '''"
    assert "cvcpkg-" not in text and "/tmp/" not in text
    if sys.platform != "win32":
        assert (script.stat().st_mode & 0o777) == 0o755


def test_sh_polyglot_keeps_both_readings(tmp_path):
    """The wrapper is a polyglot: sh execs line two, Python reads lines 2-3 as
    one triple-quoted string.  A rewrite that broke either would trade a dead
    path for a broken script."""
    script = _polyglot(tmp_path, "bin/tool", f"{LONG_PREFIX}/bin/python3.11")
    _rewrite_shebangs(tmp_path)
    text = script.read_bytes().decode()

    # Python reading: still parses, and still starts with the string expression.
    tree = ast.parse(text)
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)

    # sh reading: the line sh executes resolves the interpreter through env.
    assert text.splitlines()[1].startswith("'''exec' /usr/bin/env ")


@pytest.mark.skipif(sys.platform == "win32", reason="no /bin/sh")
def test_rewritten_sh_polyglot_actually_runs(tmp_path):
    # End-to-end: dead before, executable after, using this interpreter's own
    # versioned name so /usr/bin/env can find it on PATH.
    name = f"python3.{sys.version_info.minor}"
    script = _polyglot(tmp_path, "bin/tool", f"{LONG_PREFIX}/bin/{name}")
    script.write_bytes(script.read_bytes().replace(BODY, b"import sys\nsys.exit(7)\n"))
    script.chmod(0o755)

    assert subprocess.run([str(script)], capture_output=True).returncode != 7

    _rewrite_shebangs(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{os.path.dirname(sys.executable)}{os.pathsep}{os.environ['PATH']}",
    }
    assert subprocess.run([str(script)], capture_output=True, env=env).returncode == 7


def test_rewrites_polyglot_under_alternate_builder_work_root(tmp_path):
    # The rewrite must key off "is this an ephemeral cvcpkg dir", not off /tmp:
    # prettyhatemachine builds under /var/lib/cvcpkg-builder/cvcpkg-org/, and it
    # is the only host that can build the -cuda columns, so a /tmp-only match
    # would leave exactly the packages nothing else can rebuild broken.
    script = _polyglot(tmp_path, "bin/torchrun", f"{CUDA_HOST_PREFIX}/bin/python3.11")
    assert len(f"{CUDA_HOST_PREFIX}/bin/python3.11") > 120  # why pip went polyglot
    _rewrite_shebangs(tmp_path)
    text = script.read_bytes().decode()
    assert text.splitlines()[1] == "'''exec' /usr/bin/env python3.11 \"$0\" \"$@\""
    assert "/var/lib/" not in text and "cvcpkg-" not in text


def test_genuine_sh_polyglot_untouched(tmp_path):
    # Same wrapper shape, but the interpreter is a real system path: nothing
    # about it dies on packaging, so it must pass through byte-identical.
    script = _polyglot(tmp_path, "bin/system-tool", "/usr/bin/python3.11")
    before = script.read_bytes()
    _rewrite_shebangs(tmp_path)
    assert script.read_bytes() == before


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
    # The write must not leave the file more writable than the package
    # intended.  The user-write bit is the one signal both platforms share:
    # Windows squashes 0o555 to its read-only 0o444, so only assert the
    # exact POSIX mode where POSIX modes exist.
    assert not (script.stat().st_mode & stat.S_IWUSR)
    if sys.platform != "win32":
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="shebang execution is a POSIX kernel feature; Windows console "
    "scripts are Scripts/*.exe launchers (see _SHEBANG_DIRS in builder.py)",
)
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
