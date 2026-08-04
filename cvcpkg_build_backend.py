"""Custom PEP 517 build backend that wraps poetry-core.

Copies ./recipes into src/cvcpkg/recipes/ before building so that recipe
files are included in the wheel/sdist.  The copy is wholesale: a recipe
directory is an opaque bundle of build inputs, and packaging decides only
that it ships, never which of its files matter.  See _sync_recipes.
"""

import shutil
from pathlib import Path

from poetry.core.masonry.api import (
    build_editable as _orig_build_editable,
    build_sdist,
    build_wheel,
    get_requires_for_build_sdist,
    get_requires_for_build_wheel,
    prepare_metadata_for_build_editable,
    prepare_metadata_for_build_wheel,
)

__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
]

_HERE = Path(__file__).resolve().parent

# Recipe directories are copied WHOLESALE -- deliberately not through an
# extension allowlist.
#
# A recipe's build script may read any file sitting beside it, not just the
# ones with build-ish extensions: docs it installs into its output, config
# templates it patches into a source tree, boot scripts it injects into a disk
# image.  `haiku-image` alone reads README-import.md, UserBuildConfig and
# UserBootscript out of ${RECIPE_DIR}, and an allowlist of
# {.yaml,.sh,.ps1,.cmake,.patch} dropped all three -- so a wheel-installed
# cvcpkg built the Haiku cross-toolchain for an hour and only then died on a
# `cp` under `set -e`.  Any allowlist has the same shape of bug waiting in it:
# it fails closed on data, silently, at the point of use.
#
# packaging/cvcpkg.spec bundles ROOT/"recipes" wholesale for the PyInstaller
# binary, so copying wholesale here is also what makes the two packaging routes
# agree on what a recipe directory contains.
#
# The denylist below is only for working-tree droppings that can never be a
# recipe input; it changes nothing about a clean checkout.
_EXCLUDE_NAMES = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".DS_Store",
}
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", "~")


def _ignore_junk(_dir: str, names: list[str]) -> set[str]:
    """shutil.copytree ``ignore`` callback: drop VCS/editor/interpreter junk."""
    return {n for n in names if n in _EXCLUDE_NAMES or n.endswith(_EXCLUDE_SUFFIXES)}


def _sync_recipes(repo_recipes: Path | None = None, src_recipes: Path | None = None) -> None:
    """Copy the recipe tree into the package source tree.

    Arguments exist so tests can exercise the real copy against a scratch
    destination; the defaults are the in-repo paths used by the build hooks.
    """
    src_recipes = src_recipes or _HERE / "src" / "cvcpkg" / "recipes"
    repo_recipes = repo_recipes or _HERE / "recipes"

    if not repo_recipes.is_dir():
        return  # Building from sdist or outside repo — skip

    if src_recipes.is_symlink() or src_recipes.is_file():
        src_recipes.unlink()
    elif src_recipes.exists():
        shutil.rmtree(src_recipes)

    shutil.copytree(repo_recipes, src_recipes, ignore=_ignore_junk)


# Wrap the build functions to sync recipes first

_orig_build_wheel = build_wheel
_orig_build_sdist = build_sdist


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _sync_recipes()
    return _orig_build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    _sync_recipes()
    return _orig_build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _orig_build_editable(wheel_directory, config_settings, metadata_directory)
