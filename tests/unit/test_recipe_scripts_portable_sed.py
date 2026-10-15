"""Recipe scripts that run on macOS or the BSDs must use a portable ``sed -i``.

GNU sed takes ``-i`` with no argument; BSD sed (macOS, FreeBSD, OpenBSD,
NetBSD) requires a backup suffix, so ``sed -i 's/x/y/' file`` makes it take the
script as the suffix and the file name as the script.  assimp's macOS build died
that way: ``sed: 1: "/private/var/...": invalid command code v``.  The only form
both accept is an attached suffix, ``sed -i.orig 's/x/y/' file`` (then remove
``file.orig``); ``sed -i '' ...`` breaks GNU sed instead.

The wasm, wasm-mt, wasi and cosmo build scripts run on Linux hosts only, so
they are exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RECIPES = Path(__file__).resolve().parents[2] / "recipes"
LINUX_HOST_ONLY = re.compile(r"^build-(wasm|wasm-mt|wasi|cosmo)\.sh$")

# `sed ... -i` where -i has no attached suffix (followed by whitespace or the end).
_BARE_INPLACE = re.compile(r"(^|[\s;&|(`])sed\b[^|;&\n]*?\s-[a-zA-Z]*i(?=\s|$)")


def _bare_inplace(line: str) -> bool:
    return not line.lstrip().startswith("#") and bool(_BARE_INPLACE.search(line))


def _scripts() -> list[Path]:
    return sorted(
        p for p in RECIPES.rglob("*.sh") if p.is_file() and not LINUX_HOST_ONLY.match(p.name)
    )


@pytest.mark.parametrize(
    "line",
    [
        "sed -i 's/a/b/' f",
        "    sed -i '/x/d' \"${f}\"",
        "sed -E -i 's/a/b/' f",
        "sed -Ei 's/a/b/' f",
        "x && sed -i -e 's/a/b/' f",
    ],
)
def test_bare_inplace_is_flagged(line):
    assert _bare_inplace(line)


@pytest.mark.parametrize(
    "line",
    [
        "sed -i.orig 's/a/b/' f && rm -f f.orig",
        "sed -n 1p",
        "sed 's/-i /x/' f",
        "# sed -i 's/a/b/' f",
        "find . -iname x | sed -e 's/a/b/'",
    ],
)
def test_portable_forms_pass(line):
    assert not _bare_inplace(line)


def test_recipe_scripts_use_portable_sed_inplace():
    found = [
        f"{path.relative_to(RECIPES.parent)}:{n}: {line.strip()[:110]}"
        for path in _scripts()
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if _bare_inplace(line)
    ]
    msg = "GNU-only `sed -i` in scripts that run on macOS/BSD (use `sed -i.orig ... && rm -f FILE.orig`):"
    assert not found, "\n".join([msg, *found])
