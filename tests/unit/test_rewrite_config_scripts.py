"""cvc_rewrite_install_paths relocates autoconf-style bin/*-config scripts.

29 published bundles (2026-10-13 scan) shipped builder paths; eight did so
through *-config scripts (gsl-config, krb5-config, xml2-config, curl-config,
libpng16-config, pcre2-config, geos-config, MagickWand-config, pspell-config),
which the helper only handled for .pc/.cmake.  Each script must work from
wherever the bundle is unpacked, whatever quoting the path sat in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX-only build-time helper (bash); windows recipes use build.ps1",
)

HELPER = Path(__file__).resolve().parents[2] / "recipes" / "_common" / "rewrite-install-paths.sh"


def _rewrite(install: Path, deps: Path) -> str:
    return subprocess.run(
        ["bash", "-c", f'source "{HELPER}"; cvc_rewrite_install_paths'],
        env={
            **os.environ,
            "CVC_INSTALL_DIR": str(install),
            "CVC_DEPS_PREFIX": str(deps),
            "CVC_PLATFORM": "linux",
        },
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _script(install: Path, name: str, body: str) -> Path:
    p = install / "bin" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    p.chmod(0o755)
    return p


def _run(script: Path, *args: str) -> str:
    return subprocess.run(
        ["sh", str(script), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_bare_double_and_single_quoted_paths_follow_the_bundle(tmp_path):
    install, deps = tmp_path / "install", tmp_path / "deps"
    _script(
        install,
        "foo-config",
        f"""#!/bin/sh
prefix={install}
libdir="{install}/lib"
q='{install}/share'
case "$1" in
  --prefix) echo "$prefix" ;;
  --cflags) echo -I{install}/include ;;
  --libs) echo "-L$libdir -L{deps}/lib -lfoo" ;;
  --data) echo "$q" ;;
  --configure) echo " '--prefix={install}' '--with-x'" ;;
esac
""",
    )
    out = _rewrite(install, deps)
    assert "relocated 1 bin/*-config script" in out
    # Move the whole bundle somewhere else entirely; the build dirs are gone.
    moved = tmp_path / "elsewhere" / "pkg"
    shutil.move(str(install), str(moved))
    script = moved / "bin" / "foo-config"
    text = script.read_text()
    assert str(install) not in text and str(deps) not in text
    real = str(moved.resolve())
    assert _run(script, "--prefix") == real
    assert _run(script, "--cflags") == f"-I{real}/include"
    assert _run(script, "--libs") == f"-L{real}/lib -L{real}/lib -lfoo"
    assert _run(script, "--data") == f"{real}/share"
    assert _run(script, "--configure") == f"'--prefix={real}' '--with-x'"


def test_binaries_and_untouched_scripts_are_left_alone(tmp_path):
    install, deps = tmp_path / "install", tmp_path / "deps"
    binary = install / "bin" / "bar-config"
    binary.parent.mkdir(parents=True)
    payload = b"\x7fELF" + str(install).encode() + b"\x00"
    binary.write_bytes(payload)
    clean = _script(install, "clean-config", "#!/bin/sh\necho -lclean\n")
    clean_text = clean.read_text()
    _rewrite(install, deps)
    assert binary.read_bytes() == payload
    assert clean.read_text() == clean_text


def test_idempotent(tmp_path):
    install, deps = tmp_path / "install", tmp_path / "deps"
    s = _script(install, "foo-config", f"#!/bin/sh\nprefix={install}\necho $prefix\n")
    _rewrite(install, deps)
    once = s.read_text()
    _rewrite(install, deps)
    assert s.read_text() == once
    assert once.count("__cvc_prefix=") == 1


def test_read_only_script_is_still_rewritten(tmp_path):
    """geos-config ships mode 0555; the rewrite must not leave it stale."""
    install, deps = tmp_path / "install", tmp_path / "deps"
    s = _script(install, "geos-config", f"#!/bin/sh\necho {install}\n")
    s.chmod(0o555)
    _rewrite(install, deps)
    assert str(install) not in s.read_text()
