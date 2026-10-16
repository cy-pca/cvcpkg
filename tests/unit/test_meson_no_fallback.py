"""Every shipped meson build refuses meson's subproject fallback.

With the default wrap mode, a dependency meson cannot find is replaced by a
subproject that meson downloads from upstream during the build, then builds and
installs into the prefix.  pango did this with cairo: its .pc chain could not
resolve, so meson downloaded cairo 1.18.7, built it without the Xlib surface
and installed it over cvcpkg's own, and gtk4 then failed far from the cause.
A source fetched that way is also outside cvcpkg's pinned, sha256-checked
sources.  ``--wrap-mode=nofallback`` makes the missing dependency fail setup.

These tests walk the real recipes, like test_noarch_recipes.py, so a new meson
recipe cannot quietly leave the flag out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
FLAG = "--wrap-mode=nofallback"


def _setup_commands(text: str) -> list[str]:
    """Each bash ``meson setup`` command, with its continuation lines joined."""
    lines = text.splitlines()
    commands = []
    for i, line in enumerate(lines):
        if not re.match(r"\s*meson setup\b", line):
            continue
        cmd = [line]
        j = i
        while cmd[-1].rstrip().endswith("\\") and j + 1 < len(lines):
            j += 1
            cmd.append(lines[j])
        commands.append("\n".join(cmd))
    return commands


def _bash_meson_scripts() -> list[Path]:
    return sorted(
        p
        for p in RECIPES_DIR.glob("*/build*.sh")
        if re.search(r"^\s*meson setup\b", p.read_text(encoding="utf-8"), re.M)
    )


def test_corpus_has_meson_scripts():
    # Guards the walk itself: a moved recipes/ dir would make every test vacuous.
    assert len(_bash_meson_scripts()) >= 30


@pytest.mark.parametrize("script", _bash_meson_scripts(), ids=lambda p: p.parent.name)
def test_bash_meson_setup_refuses_fallback(script):
    text = script.read_text(encoding="utf-8")
    for cmd in _setup_commands(text):
        # An options array ("${MESON_OPTS[@]}") carries the flag in its definition.
        arrays = re.findall(r'"\$\{(\w+)\[@\]\}"', cmd)
        bodies = [
            m.group(1)
            for name in arrays
            for m in re.finditer(rf"^{name}=\((.*?)^\)", text, re.M | re.S)
        ]
        msg = f"{script.relative_to(REPO_ROOT)}: `meson setup` without {FLAG}"
        assert FLAG in cmd or any(FLAG in b for b in bodies), msg


def test_windows_meson_helper_refuses_fallback():
    text = (RECIPES_DIR / "_common" / "env-windows.ps1").read_text(encoding="utf-8")
    body = text.split("function Invoke-CvcMesonBuild", 1)[1].split("\n}\n", 1)[0]
    assert f"'{FLAG}'" in body


@pytest.mark.parametrize(
    "script",
    sorted(
        p
        for p in RECIPES_DIR.glob("*/build*.ps1")
        if re.search(r"'setup'", p.read_text(encoding="utf-8"))
        and "Invoke-CvcMesonBuild" not in p.read_text(encoding="utf-8")
    ),
    ids=lambda p: p.parent.name,
)
def test_windows_direct_meson_setup_refuses_fallback(script):
    # Recipes that call meson themselves instead of Invoke-CvcMesonBuild.
    assert f"'{FLAG}'" in script.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "script",
    sorted(
        p
        for p in RECIPES_DIR.glob("*/build*")
        if p.suffix in (".sh", ".ps1")
        and re.search(r"pip wheel", p.read_text(encoding="utf-8"))
        and re.search(r"setup-args=|BUILD BACKEND: meson", p.read_text(encoding="utf-8"))
    ),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_meson_python_wheel_refuses_fallback(script):
    # A meson-python sdist build runs `meson setup` itself; setup-args reach it.
    assert f"-C setup-args={FLAG}" in script.read_text(encoding="utf-8")
