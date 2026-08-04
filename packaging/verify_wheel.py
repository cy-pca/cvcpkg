#!/usr/bin/env python3
"""Verify a built cvcpkg wheel (or sdist) is fit to publish to PyPI.

Checks the archive *contents* without installing it:

- console entry points ``cvcpkg`` and ``cvcpkg-server`` are declared;
- the client and server packages are present;
- the bundled non-Python DATA files are present -- every JSON-Schema under
  ``cvcpkg/schemas/``.  These are loaded through ``importlib.resources`` at
  runtime, so a wheel that drops one still imports cleanly and only fails
  when someone runs ``cvcpkg validate`` (or installs an image package);
- recipe files are bundled (at least ``--min-recipes`` recipe.yaml files,
  the sentinel recipes are present, and build scripts came along);
- every AUXILIARY script a bundled recipe names (``build.matrix[].script``,
  ``test.script``, ``test.vm.script``) is bundled next to its recipe.yaml.
  A recipe's scripts are resolved as ``recipe_dir / <script>`` on the
  builder, so one that did not ride along is a build/test that dies at the
  point of use;
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
    # Image packages: the descriptor reader, the throwaway-VM test engine and
    # the `cvcpkg image` command group.  Listed explicitly because they are
    # reached only from `cvcpkg image ...` / a `test.vm` recipe block, so a
    # wheel that dropped them still passes --version and `recipes --list`.
    "cvcpkg/images.py",
    "cvcpkg/vmtest.py",
    "cvcpkg/cli/_image.py",
)
# Non-Python data that must ride along.  poetry-core only ships these because
# pyproject's [tool.poetry] `include` names src/cvcpkg/schemas explicitly --
# nothing about a .yaml makes it a package member -- and they are read via
# importlib.resources, so a wheel missing one imports fine and fails later at
# `cvcpkg validate` / `cvcpkg pack` time.
REQUIRED_DATA = (
    "cvcpkg/schemas/recipe-schema.yaml",
    "cvcpkg/schemas/components-schema.yaml",
    "cvcpkg/schemas/manifest-schema.yaml",
    "cvcpkg/schemas/image-schema.yaml",
)
# Recipes that must always be present in a good build.
SENTINEL_RECIPES = ("zlib",)

# A `script:` value inside a recipe.yaml -- build.matrix[].script,
# test.script, or test.vm.script.  Matched textually (no PyYAML dependency:
# this runs in a bare CI python before anything is installed) and only for
# values that look like a file name, which is what all three keys hold.
SCRIPT_REF_RE = re.compile(
    r'(?m)^[ \t]*script:[ \t]*["\']?([A-Za-z0-9_.\-/]+\.(?:sh|ps1|bat|cmd|py))["\']?[ \t]*(?:#.*)?$'
)


def _list_archive(path: Path) -> tuple[list[str], dict[str, bytes]]:
    """Return (member names, {suffix-matched member: bytes}) for wheel or sdist.

    Only the small text members we inspect (entry_points.txt, METADATA/
    PKG-INFO and every recipe.yaml) are read into memory -- never the
    payloads, so this stays cheap on a distribution carrying 500+ recipes.
    """
    names: list[str] = []
    text: dict[str, bytes] = {}
    wanted = ("entry_points.txt", "METADATA", "PKG-INFO", "recipe.yaml")
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


def _strip_top_one(n: str) -> str:
    """For an sdist member, drop the leading ``cvcpkg-<ver>/`` dir."""
    parts = n.split("/", 1)
    return parts[1] if parts[0].startswith("cvcpkg-") and len(parts) == 2 else n


def _strip_top(names: list[str]) -> list[str]:
    """For an sdist, drop the leading ``cvcpkg-<ver>/`` dir from paths."""
    return [_strip_top_one(n) for n in names]


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
    # Same normalization for the members we actually read, so the recipe scan
    # below can talk in "cvcpkg/recipes/<name>/..." terms for both formats.
    normed_text = {_norm(_strip_top_one(k) if is_sdist else k): v for k, v in text.items()}

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

    # Non-Python data files (schemas).  A .py is a package member by virtue of
    # being a .py; these ship only because pyproject names them, so they are
    # exactly the thing a packaging change silently drops.
    for data in REQUIRED_DATA:
        if data not in normed:
            problems.append(f"missing data file in package: {data}")

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

    # Auxiliary scripts a recipe NAMES must ship next to it.  cvcpkg resolves
    # them as ``recipe_dir / <script>`` on the builder (see
    # builder.run_build / builder.run_vm_test), so a bundle that carries
    # build.sh but not the test.vm.script beside it is a tree that builds and
    # then dies at the test step -- which is how `haiku-image`'s vm-test.sh
    # went missing from a declared file list in the first place.
    for member, blob in sorted(normed_text.items()):
        if not (member.startswith("cvcpkg/recipes/") and member.endswith("/recipe.yaml")):
            continue
        rdir = member.rsplit("/", 1)[0]
        for ref in sorted(set(SCRIPT_REF_RE.findall(blob.decode("utf-8", "replace")))):
            if f"{rdir}/{ref}" not in normed:
                problems.append(f"recipe script not bundled: {rdir}/{ref} (named by {member})")

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
