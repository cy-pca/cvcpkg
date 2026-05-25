"""Custom PEP 517 build backend that wraps poetry-core.

Copies ../../recipes into src/cvcpkg/recipes/ before building so that
recipe files are included in the wheel/sdist.
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
_EXTS = {".yaml", ".sh", ".ps1", ".cmake", ".patch"}


def _sync_recipes() -> None:
    """Copy recipe files into the package source tree."""
    src_recipes = _HERE / "src" / "cvcpkg" / "recipes"
    repo_recipes = _HERE.parent.parent / "recipes"

    if not repo_recipes.is_dir():
        return  # Building from sdist or outside repo — skip

    if src_recipes.exists():
        shutil.rmtree(src_recipes)

    for child in sorted(repo_recipes.iterdir()):
        if not child.is_dir():
            continue
        dest = src_recipes / child.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in child.rglob("*"):
            if f.is_file() and f.suffix in _EXTS:
                rel = f.relative_to(child)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)


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
