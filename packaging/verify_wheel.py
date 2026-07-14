#!/usr/bin/env python3
"""Verify a built cvcpkg wheel (or sdist) is fit to publish to PyPI.

Checks the archive *contents* without installing it:

- console entry points ``cvcpkg`` and ``cvcpkg-server`` are declared;
- the client and server packages are present;
- recipe files are bundled (at least ``--min-recipes`` recipe.yaml files,
  the sentinel recipes are present, and build scripts came along);
- core metadata is sane (Name == cvcpkg, a version, Requires-Python).

Exits non-zero and prints every problem found, so it can gate CI before a
publish.

Usage:
    python packaging/verify_wheel.py dist/cvcpkg-*.whl
    python packaging/verify_wheel.py dist/cvcpkg-*.tar.gz --min-recipes 100
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

REQUIRED_ENTRY_POINTS = ("cvcpkg", "cvcpkg-server")
REQUIRED_MODULES = (
    "cvcpkg/__init__.py",
    "cvcpkg/cli/__init__.py",
    "cvcpkg/server/app.py",
)
# Recipes that must always be present in a good build.
SENTINEL_RECIPES = ("zlib",)


def _list_archive(path: Path) -> tuple[list[str], dict[str, bytes]]:
    """Return (member names, {suffix-matched member: bytes}) for wheel or sdist.

    Only the small text members we inspect (entry_points.txt, METADATA/
    PKG-INFO) are read into memory.
    """
    names: list[str] = []
    text: dict[str, bytes] = {}
    wanted = ("entry_points.txt", "METADATA", "PKG-INFO")
    if path.suffix == ".whl" or path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            for n in names:
                if n.rsplit("/", 1)[-1] in wanted:
                    text[n] = zf.read(n)
    else:  # sdist tarball
        with tarfile.open(path) as tf:
            for m in tf.getmembers():
                names.append(m.name)
                if m.isfile() and m.name.rsplit("/", 1)[-1] in wanted:
                    f = tf.extractfile(m)
                    if f is not None:
                        text[m.name] = f.read()
    return names, text


def _strip_top(names: list[str]) -> list[str]:
    """For an sdist, drop the leading ``cvcpkg-<ver>/`` dir from paths."""
    out = []
    for n in names:
        parts = n.split("/", 1)
        out.append(parts[1] if parts[0].startswith("cvcpkg-") and len(parts) == 2 else n)
    return out


def verify(path: Path, *, min_recipes: int = 50) -> list[str]:
    """Return a list of problems (empty means the archive looks publishable)."""
    problems: list[str] = []
    raw_names, text = _list_archive(path)
    is_sdist = path.suffixes[-2:] == [".tar", ".gz"] or path.name.endswith(".tar.gz")
    names = _strip_top(raw_names) if is_sdist else raw_names

    # For an sdist the source lives under src/cvcpkg/...; for a wheel it's
    # cvcpkg/... at the root.  Normalize to "cvcpkg/...".
    def _norm(n: str) -> str:
        return n[len("src/") :] if n.startswith("src/cvcpkg/") else n

    normed = {_norm(n) for n in names}

    # Entry points (wheels only carry entry_points.txt).
    ep_blob = b"".join(v for k, v in text.items() if k.endswith("entry_points.txt"))
    if ep_blob:
        ep_txt = ep_blob.decode("utf-8", "replace").replace(" ", "")
        for ep in REQUIRED_ENTRY_POINTS:
            if f"{ep}=" not in ep_txt:
                problems.append(f"missing console entry point: {ep}")

    # Package modules.
    for mod in REQUIRED_MODULES:
        if mod not in normed:
            problems.append(f"missing module in package: {mod}")

    # Bundled recipes.
    recipe_re = re.compile(r"^cvcpkg/recipes/([^/]+)/recipe\.yaml$")
    recipe_names = {m.group(1) for n in normed if (m := recipe_re.match(n))}
    if len(recipe_names) < min_recipes:
        problems.append(f"only {len(recipe_names)} recipes bundled (expected >= {min_recipes})")
    for r in SENTINEL_RECIPES:
        if r not in recipe_names:
            problems.append(f"sentinel recipe not bundled: {r}")
    if not any(n.startswith("cvcpkg/recipes/") and n.endswith(".sh") for n in normed):
        problems.append("no recipe build scripts (*.sh) bundled")

    # Core metadata.
    md_blob = b"".join(v for k, v in text.items() if k.endswith(("METADATA", "PKG-INFO")))
    md = md_blob.decode("utf-8", "replace")
    if not re.search(r"^Name:\s*cvcpkg\s*$", md, re.MULTILINE):
        problems.append("metadata Name is not 'cvcpkg'")
    if not re.search(r"^Version:\s*\S+", md, re.MULTILINE):
        problems.append("metadata has no Version")
    if not re.search(r"^Requires-Python:", md, re.MULTILINE):
        problems.append("metadata has no Requires-Python")

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a cvcpkg wheel/sdist is publishable.")
    ap.add_argument("archive", type=Path, help="Path to a .whl or .tar.gz")
    ap.add_argument("--min-recipes", type=int, default=50)
    args = ap.parse_args(argv)

    if not args.archive.is_file():
        print(f"ERROR: no such file: {args.archive}", file=sys.stderr)
        return 2

    problems = verify(args.archive, min_recipes=args.min_recipes)
    if problems:
        print(f"✗ {args.archive.name} is NOT publishable:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"✓ {args.archive.name} looks publishable.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
