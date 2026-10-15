"""Python recipe builds must not see the builder account's user site.

The prefix interpreter searches ``~/.local/lib/pythonX.Y/site-packages`` ahead
of its own site-packages, and fleet builders have one.  With it visible, a
dependency missing from a recipe's declared closure is found there instead of
failing the build, and PyInstaller freezes whatever it can import (recipes/
cvcpkg shipped sqlalchemy and brotli from a builder's user site).  The shared
helpers every Python column sources turn the user site off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[2] / "recipes" / "_common"


def _user_site(base: Path) -> Path:
    """The user site *sys.executable* derives from PYTHONUSERBASE=*base*."""
    out = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
        env={**os.environ, "PYTHONUSERBASE": str(base)},
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


@pytest.mark.skipif(not shutil.which("bash"), reason="python-wheel.sh is a bash helper")
def test_sourcing_python_wheel_sh_hides_the_user_site(tmp_path):
    base = tmp_path / "userbase"
    site_dir = _user_site(base)
    site_dir.mkdir(parents=True)
    (site_dir / "leaked_from_user_site.py").write_text("")

    env = {k: v for k, v in os.environ.items() if k != "PYTHONNOUSERSITE"}
    env["PYTHONUSERBASE"] = str(base)
    probe = f'"{sys.executable}" -c "import leaked_from_user_site"'

    # Control: the planted module IS visible without the helper.  A venv
    # disables the user site on its own, so there is nothing to show there.
    control = subprocess.run(["bash", "-c", probe], env=env, capture_output=True, text=True)
    if control.returncode != 0:
        pytest.skip("this interpreter has no user site (a venv?)")

    isolated = subprocess.run(
        ["bash", "-c", f'. "{COMMON / "python-wheel.sh"}"; {probe}'],
        env=env,
        capture_output=True,
        text=True,
    )
    assert isolated.returncode != 0
    assert "No module named 'leaked_from_user_site'" in isolated.stderr


def test_python_wheel_ps1_sets_it_before_anything_runs():
    text = (COMMON / "python-wheel.ps1").read_text(encoding="utf-8")
    assert "$env:PYTHONNOUSERSITE = '1'" in text
    # Set at load time, ahead of the first function definition, so it covers
    # every call a sourcing build script makes.
    assert text.index("$env:PYTHONNOUSERSITE") < text.index("function ")
