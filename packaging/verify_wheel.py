#!/usr/bin/env python3
r"""Verify a built cvcpkg wheel (or sdist) is fit to publish to PyPI.

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
- every DATA file a bundled recipe's scripts read out of their own recipe
  directory (``${RECIPE_DIR}/x``, ``$(dirname "$0")/x``, ``$PSScriptRoot\x``)
  is bundled too.  Nothing declares these -- they are just files a build
  script cp's, sources or patches -- so an extension allowlist drops them
  silently and the build dies at the point of use, arbitrarily late;
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

# A reference, inside a recipe script, to a file in the script's OWN recipe
# directory.  Every idiom the recipe corpus uses to name "the directory this
# script lives in":
#
#     ${RECIPE_DIR}/x  $RECIPE_DIR/x        (haiku-image)
#     ${CVC_RECIPE_DIR}/x                   (exported by builder.run_build)
#     ${SCRIPT_DIR}/x                       (SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)")
#     $(dirname "$0")/x
#     $PSScriptRoot\x                       (build.ps1)
#
# These are DATA references -- the files a build script cp's, sources, patches
# or reads -- as opposed to the `script:` keys above, which a recipe.yaml
# declares.  Nothing declares these, so packaging cannot know them without
# reading the scripts; that is exactly how an extension allowlist dropped
# haiku-image's README-import.md / UserBuildConfig / UserBootscript.
_OWN_DIR = (
    r"(?:"
    r"\$\{(?:CVC_)?RECIPE_DIR\}|\$(?:CVC_)?RECIPE_DIR(?![A-Za-z0-9_])"
    r"|\$\{SCRIPT_DIR\}|\$SCRIPT_DIR(?![A-Za-z0-9_])"
    r"|\$\(\s*dirname\s+\"?\$0\"?\s*\)"
    r"|\$PSScriptRoot(?![A-Za-z0-9_])"
    r")"
)
# The path tail stops at anything that ends a shell word.  `$` and glob
# metacharacters are allowed INTO the capture so that an interpolated path
# (e.g. env-${CVC_PLATFORM}.sh) is caught and discarded whole, rather than
# truncated into a bogus prefix.
OWN_DIR_REF_RE = re.compile(_OWN_DIR + r"[/\\]([^\s\"'`;:|&<>)]+)")


def recipe_dir_file_refs(script_text: str) -> set[str]:
    """Return the paths a script names relative to its own recipe directory.

    Paths keep any leading ``..`` (a sibling recipe, e.g.
    ``../_common/python-wheel.sh``); join them onto the recipe directory with
    :func:`resolve_recipe_ref`.  References that cannot be resolved by reading
    alone -- ones built from a variable, or containing a glob -- are dropped
    rather than guessed at.
    """
    refs: set[str] = set()
    for raw in OWN_DIR_REF_RE.findall(script_text):
        ref = raw.replace("\\", "/").rstrip("/")
        if not ref or "$" in ref or any(c in ref for c in "*?[]"):
            continue
        parts = [p for p in ref.split("/") if p not in ("", ".")]
        if parts:
            refs.add("/".join(parts))
    return refs


def resolve_recipe_ref(recipe_dir: str, ref: str) -> str | None:
    """Join ``ref`` onto ``recipe_dir`` lexically; None if it escapes recipes/.

    ``recipe_dir`` is a bundle path like ``cvcpkg/recipes/haiku-image``.  A ref
    that climbs above ``cvcpkg/recipes`` is not something packaging can be
    asked to preserve, so it is dropped.
    """
    root = recipe_dir.rsplit("/", 1)[0]  # cvcpkg/recipes
    parts = recipe_dir.split("/")
    for part in ref.split("/"):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    out = "/".join(parts)
    return out if out.startswith(f"{root}/") else None


def _is_wanted(name: str) -> bool:
    """Should this archive member be read into memory for inspection?"""
    base = name.rsplit("/", 1)[-1]
    if base in ("entry_points.txt", "METADATA", "PKG-INFO", "recipe.yaml"):
        return True
    # Recipe scripts, for the own-directory data-reference scan below.  ~1 MB
    # across the whole corpus, so this stays cheap.
    return "/recipes/" in name and base.endswith((".sh", ".ps1"))


def _list_archive(path: Path) -> tuple[list[str], dict[str, bytes]]:
    """Return (member names, {suffix-matched member: bytes}) for wheel or sdist.

    Only the small text members we inspect (entry_points.txt, METADATA/
    PKG-INFO, every recipe.yaml and every recipe script) are read into memory
    -- never the payloads, so this stays cheap on a distribution carrying
    500+ recipes.
    """
    names: list[str] = []
    text: dict[str, bytes] = {}
    if path.suffix == ".whl" or path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            for n in names:
                if _is_wanted(n):
                    text[n] = zf.read(n)
    else:  # sdist tarball
        with tarfile.open(path) as tf:
            for m in tf.getmembers():
                names.append(m.name)
                if m.isfile() and _is_wanted(m.name):
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


RECIPE_PREFIX = "cvcpkg/recipes/"


def source_recipes_root() -> Path | None:
    """The repo's ``recipes/`` directory, when run from a checkout.

    Both workflows run this script from the checkout that produced the
    archive, which is what lets the own-directory scan below tell a PACKAGING
    failure (a file that exists in recipes/ and did not survive) apart from a
    BROKEN RECIPE (a script naming a file that exists nowhere -- not something
    packaging can fix, and not this gate's business).  Outside a checkout the
    scan is skipped rather than guessed at.
    """
    cand = Path(__file__).resolve().parent.parent / "recipes"
    return cand if (cand / "_common").is_dir() else None


def verify(path: Path, *, min_recipes: int = 50, recipes_root: Path | None = None) -> list[str]:
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

    # DATA files a recipe script READS out of its own recipe directory.
    # Nothing declares these -- they are just files the script cp's, sources
    # or patches -- so packaging has to read the scripts to know they matter.
    # An extension allowlist in the build backend dropped haiku-image's
    # README-import.md, UserBuildConfig and UserBootscript, and because they
    # are read AFTER the Haiku cross-toolchain is built, the wheel-installed
    # failure came an hour into an otherwise-good build.
    #
    # Only files that EXIST in the source tree are required to survive: a
    # script naming a file that is in no tree at all is a broken recipe, which
    # a packaging gate can neither diagnose nor fix.
    src_root = recipes_root if recipes_root is not None else source_recipes_root()
    if src_root is not None:
        for member, blob in sorted(normed_text.items()):
            if not (member.startswith(RECIPE_PREFIX) and member.endswith((".sh", ".ps1"))):
                continue
            rdir = member.rsplit("/", 1)[0]
            for ref in sorted(recipe_dir_file_refs(blob.decode("utf-8", "replace"))):
                target = resolve_recipe_ref(rdir, ref)
                if target is None or target in normed:
                    continue
                if (src_root / target[len(RECIPE_PREFIX) :]).is_file():
                    problems.append(f"recipe data file not bundled: {target} (read by {member})")

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
    ap.add_argument(
        "--recipes-dir",
        type=Path,
        default=None,
        help="Source recipes/ tree to compare against (default: the checkout "
        "this script lives in; the data-file scan is skipped without one)",
    )
    args = ap.parse_args(argv)

    if not args.archive.is_file():
        print(f"ERROR: no such file: {args.archive}", file=sys.stderr)
        return 2

    problems = verify(args.archive, min_recipes=args.min_recipes, recipes_root=args.recipes_dir)
    if problems:
        print(f"✗ {args.archive.name} is NOT publishable:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"✓ {args.archive.name} looks publishable.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
