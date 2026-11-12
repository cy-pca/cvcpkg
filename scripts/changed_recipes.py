#!/usr/bin/env python3
"""The recipes a deploy has to push to the server, from what git says changed.

deploy-prod used to push every recipe on every deploy, ~2,260 uploads that
took 22-36 minutes and, on 2026-10-31 and 2026-11-07, outlasted the job.  A
deploy changes a handful, so this names those: the recipes whose bundle
differs between the previously deployed commit and the one being deployed.

Usage:
    python scripts/changed_recipes.py OLD NEW [--repo DIR] [--recipes-dir DIR]

Prints one recipe name per line, or a lone ``*`` when every recipe has to be
pushed.  No output means nothing changed.  Why it fell back to ``*`` goes to
stderr.  Pushing everything is the answer whenever the diff cannot be trusted:

* OLD is empty, all zeros (a new branch) or not a commit this checkout has,
  which is also what a manual dispatch with no base commit looks like;
* ``recipes/_common/`` changed, because every bundle carries it.

A bundle is the recipe's own directory, ``_common/`` and, for a recipe with
``source: {type: vendored, path: P}``, the tree at P (relative to the repo
root, the parent of the recipes directory), so a change under P selects that
recipe.  A recipe deleted by the diff has nothing to push and is left out.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

FULL = "*"

_SHA = re.compile(r"[0-9a-fA-F]{7,40}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def changed_files(repo: Path, old: str, new: str) -> list[str] | None:
    """Paths that differ between commits *old* and *new*; None if git cannot say.

    ``--no-renames`` lists both sides of a rename, so a moved recipe directory
    names the new recipe (the old one no longer exists and is dropped later).
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--no-renames", "-z", old, new, "--"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def recipe_names(recipes_dir: Path) -> list[str]:
    """Names of the recipes in *recipes_dir*, as ``recipe push-all`` sees them."""
    return sorted(
        p.name
        for p in recipes_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "recipe.yaml").is_file()
    )


def vendored_trees(recipes_dir: Path, names: list[str]) -> dict[str, str]:
    """``{recipe: tree}`` for recipes bundling vendored source, *tree* relative
    to the repo root in POSIX form.  Only recipes that mention ``vendored`` are
    parsed."""
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    out: dict[str, str] = {}
    for name in names:
        text = (recipes_dir / name / "recipe.yaml").read_text()
        if "vendored" not in text:
            continue
        data = yaml.load(text, Loader=loader) or {}
        source = data.get("source")
        if not isinstance(source, dict) or source.get("type") != "vendored":
            continue
        path = source.get("path")
        if isinstance(path, str) and path:
            tree = Path(path).as_posix().strip("/")
            if tree and tree != ".":
                out[name] = tree
    return out


def select(files: list[str], recipes_dir: Path, repo: Path) -> list[str] | str:
    """The recipes *files* touch, sorted; ``FULL`` if one has to push them all."""
    try:
        prefix = recipes_dir.resolve().relative_to(repo.resolve()).as_posix() + "/"
    except ValueError:
        return FULL
    names = recipe_names(recipes_dir)
    vendored = vendored_trees(recipes_dir, names)
    chosen: set[str] = set()
    for f in files:
        if f.startswith(prefix):
            top = f[len(prefix) :].split("/", 1)
            if top[0] == "_common" and len(top) == 2:
                return FULL
            if len(top) == 2 and not top[0].startswith(("_", ".")):
                chosen.add(top[0])
        for name, tree in vendored.items():
            if f == tree or f.startswith(tree + "/"):
                chosen.add(name)
    present = set(names)
    return sorted(n for n in chosen if n in present and _NAME.fullmatch(n))


def plan(old: str, new: str, repo: Path, recipes_dir: Path) -> tuple[list[str] | str, str]:
    """``(names or FULL, why)`` for a deploy of *new* over *old*."""
    if not _SHA.fullmatch(old) or set(old) == {"0"}:
        return FULL, f"no previous commit to compare with (got {old!r})"
    if not _SHA.fullmatch(new):
        return FULL, f"not a commit: {new!r}"
    files = changed_files(repo, old, new)
    if files is None:
        return FULL, f"git cannot diff {old[:12]}..{new[:12]} in this checkout"
    chosen = select(files, recipes_dir, repo)
    if chosen == FULL:
        return FULL, "recipes/_common changed, which every bundle carries"
    return chosen, f"{len(files)} changed file(s) touch {len(chosen)} recipe(s)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("old", help="previously deployed commit; empty means unknown")
    ap.add_argument("new", help="commit being deployed")
    ap.add_argument("--repo", type=Path, default=Path("."), help="git checkout (default: .)")
    ap.add_argument(
        "--recipes-dir",
        type=Path,
        default=None,
        help="recipes directory (default: <repo>/recipes)",
    )
    args = ap.parse_args(argv)
    recipes_dir = args.recipes_dir or args.repo / "recipes"

    chosen, why = plan(args.old, args.new, args.repo, recipes_dir)
    if chosen == FULL:
        print(f"push every recipe: {why}", file=sys.stderr)
        print(FULL)
    else:
        print(f"push {len(chosen)} recipe(s): {why}", file=sys.stderr)
        for name in chosen:
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
