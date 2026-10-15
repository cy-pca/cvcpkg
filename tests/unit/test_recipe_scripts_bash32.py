"""Recipe shell scripts must run under bash 3.2.

macOS builds them with the OS's /bin/bash (``builder._find_bash``), and that is
bash 3.2: no bash-4 features.  numpy's and pillow's build.sh used ``readarray``
and failed on every macOS runner with exit 127 ("readarray: command not found")
once macOS CI stopped finding Homebrew's bash.

Bash before 4.4 also treats an EMPTY array as unbound under ``set -u``, so
``"${a[@]}"`` aborts the script when nothing was added to ``a``.  glib's
build.sh died that way on macOS (``ICONV_ARGS[@]: unbound variable``).  An array
a script creates empty (``a=()``) must be expanded as ``${a[@]+"${a[@]}"}``.

And bash 3.2 cannot parse a quoted heredoc opened inside a command substitution,
``"$(cat <<'TAG'``, when the heredoc body contains a single quote: maxminddb's
build.sh failed with "unexpected EOF while looking for matching `''" because of
an apostrophe in its check.  Read such a heredoc into a variable instead
(``IFS= read -r -d '' var <<'TAG' || true``).
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


_SET_U = re.compile(r"^\s*set\s+-[a-zA-Z]*u|^\s*set\s+-o\s+nounset", re.M)
_EMPTY_ARRAY = re.compile(r"^\s*(?:local\s+|declare\s+(?:-a\s+)?)?([A-Za-z_]\w*)=\(\s*\)", re.M)


def _unguarded_empty_arrays(text: str) -> list[tuple[int, str]]:
    """(line, name) for each bare "${name[@]}" of an array the script creates
    empty, in a script that runs under set -u."""
    if not _SET_U.search(text):
        return []
    names = set(_EMPTY_ARRAY.findall(text))
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for name in sorted(names):
            if re.search(r'(?<!\[@\]\+)"\$\{' + name + r'\[@\]\}"', line):
                hits.append((n, name))
    return hits


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


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ('set -euo pipefail\nA=()\ncmd "${A[@]}"\n', [(3, "A")]),
        ('set -euo pipefail\nA=()\ncmd ${A[@]+"${A[@]}"}\n', []),
        ('set -e\nA=()\ncmd "${A[@]}"\n', []),  # no set -u: an empty array is fine
        ('set -euo pipefail\nA=(x)\ncmd "${A[@]}"\n', []),  # never empty
        ('set -eu\nlocal B=()\nfor b in "${B[@]}"; do :; done\n', [(3, "B")]),
    ],
)
def test_unguarded_empty_array_detection(script, expected):
    assert _unguarded_empty_arrays(script) == expected


def test_empty_arrays_are_guarded_under_set_u():
    found = [
        f'{path.relative_to(RECIPES.parent)}:{n}: "${{{name}[@]}}" -> ${{{name}[@]+"${{{name}[@]}}"}}'
        for path in _scripts()
        for n, name in _unguarded_empty_arrays(path.read_text(encoding="utf-8", errors="replace"))
    ]
    msg = "empty arrays expanded bare under set -u (fatal on macOS's bash 3.2):"
    assert not found, "\n".join([msg, *found])


_SUBST_HEREDOC = re.compile(r"\$\(\s*cat\s+<<-?\s*(['\"]?)(\w+)\1")


def _quoted_heredocs_in_substitutions(text: str) -> list[int]:
    """Lines opening a heredoc inside $( ) whose body contains a single quote."""
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        m = _SUBST_HEREDOC.search(line)
        if not m:
            continue
        body = []
        for later in lines[i + 1 :]:
            if later.strip() == m.group(2):
                break
            body.append(later)
        if any("'" in b for b in body):
            hits.append(i + 1)
    return hits


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("x \"$(cat <<'PY'\n# MaxMind's test data\nPY\n)\"\n", [1]),
        ("x \"$(cat <<'PY'\nprint(1)\nPY\n)\"\n", []),
        ("IFS= read -r -d '' v <<'PY' || true\n# it's fine here\nPY\n", []),
    ],
)
def test_quoted_heredoc_in_substitution_detection(script, expected):
    assert _quoted_heredocs_in_substitutions(script) == expected


def test_no_apostrophes_in_heredocs_inside_substitutions():
    found = [
        f"{path.relative_to(RECIPES.parent)}:{n}"
        for path in _scripts()
        for n in _quoted_heredocs_in_substitutions(
            path.read_text(encoding="utf-8", errors="replace")
        )
    ]
    msg = "heredoc with a single quote inside $( ) (bash 3.2 cannot parse it):"
    assert not found, "\n".join([msg, *found])
