"""Recipe shell scripts must run under bash 3.2.

macOS builds them with the OS's /bin/bash (``builder._find_bash``), and that is
bash 3.2: no bash-4 features.  numpy's and pillow's build.sh used ``readarray``
and failed on every macOS runner with exit 127 ("readarray: command not found")
once macOS CI stopped finding Homebrew's bash.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RECIPES = Path(__file__).resolve().parents[2] / "recipes"

# Constructs that need bash >= 4.  Comment lines are skipped (see _code_lines).
BASH4 = {
    "readarray/mapfile": re.compile(r"(^|[;&|({\s])(readarray|mapfile)\s"),
    "associative array": re.compile(r"\b(declare|local|typeset)\s+-[a-zA-Z]*A"),
    "case modification ${x,,} ${x^^}": re.compile(r"\$\{\w+(\[[^]]*\])?(,,?|\^\^?)[^}]*\}"),
    "globstar": re.compile(r"\bshopt\s+-s\b[^#\n]*\bglobstar\b"),
    "&>> redirection": re.compile(r"&>>"),
    "coproc": re.compile(r"(^|[;&|\s])coproc\s"),
    "negative array index": re.compile(r"\$\{\w+\[-\d+\]\}"),
}


def _scripts() -> list[Path]:
    return sorted(p for p in RECIPES.rglob("*.sh") if p.is_file())


def _code_lines(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield n, line


def _flags(line: str) -> list[str]:
    return [what for what, rx in BASH4.items() if rx.search(line)]


def test_there_are_scripts_to_check():
    assert len(_scripts()) > 100


@pytest.mark.parametrize(
    "line",
    [
        'WHEEL="$(find "$d" -maxdepth 1 -name \'x-*.whl\' | sed -n 1p)"',
        'arr=(a b c); echo "${arr[0]}" "${#arr[@]}" "${x:-a,b}" "${y/a/b}"',
        "make >log 2>&1",
        "shopt -s nullglob",
        'case "$x" in a) echo a ;; *) echo b ;; esac',
    ],
)
def test_bash32_lines_pass(line):
    assert _flags(line) == []


@pytest.mark.parametrize(
    "line",
    [
        "readarray -t m < <(find . -name x)",
        "mapfile -t m < f",
        "declare -A seen=()",
        "local -A seen",
        'echo "${name,,}" "${name,}"',
        'echo "${name^^}"',
        "shopt -s globstar nullglob",
        "make &>> log",
        "coproc cat",
        'echo "${arr[-1]}"',
    ],
)
def test_bash4_lines_are_flagged(line):
    assert _flags(line)


def test_recipe_scripts_are_bash32_compatible():
    found = [
        f"{path.relative_to(RECIPES.parent)}:{n}: {', '.join(what)}: {line.strip()[:100]}"
        for path in _scripts()
        for n, line in _code_lines(path)
        if (what := _flags(line))
    ]
    msg = "bash-4-only constructs in recipe scripts (macOS builds run /bin/bash 3.2):"
    assert not found, "\n".join([msg, *found])
