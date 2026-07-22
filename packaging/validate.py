#!/usr/bin/env python3
"""Validate packaging YAML files — thin CLI shim over ``cvcpkg.validation``.

The validator and its JSON schemas now live INSIDE the installed cvcpkg package
(``cvcpkg.validation`` + ``cvcpkg/schemas/``), so ``cvcpkg validate`` runs from
any repo with no libcvc-deps checkout.  This script keeps
``python packaging/validate.py [target]`` working for THIS repo's CI, binding
the keyword targets to this checkout's ``recipes/`` and ``packaging/``.

Usage:
    python packaging/validate.py                       # validate everything
    python packaging/validate.py components             # validate components.yaml only
    python packaging/validate.py recipes                # validate all recipes
    python packaging/validate.py recipes/zlib           # validate one recipe

Requires: pip install pyyaml jsonschema  (and cvcpkg importable)
"""
from __future__ import annotations

import sys
from pathlib import Path

from cvcpkg import validation

# Re-exported so back-compat callers / tests can read the canonical value.
_UNPARSEABLE_VERSION_GRANDFATHER = validation._UNPARSEABLE_VERSION_GRANDFATHER

ROOT = Path(__file__).resolve().parent  # packaging/
REPO = ROOT.parent  # repo root
RECIPES = REPO / "recipes"
COMPONENTS = ROOT / "components.yaml"


def validate_recipe(recipe_dir: Path) -> list[str]:
    """Validate one recipe dir (schema, scripts, patches, version order)."""
    return validation.validate_recipe_dir(Path(recipe_dir))


def validate_all_recipes() -> list[str]:
    """Validate every recipe under this repo's recipes/, incl. cross-deps."""
    if not RECIPES.is_dir():
        return [f"No recipes/ directory found at {RECIPES}"]
    rmap = validation.build_recipe_map([RECIPES])
    universe = validation.known_targets(rmap)
    errors: list[str] = []
    for name in sorted(rmap):
        errors += validation.validate_recipe_dir(rmap[name])
    errors += validation.validate_cross_deps(rmap, universe)
    return errors


def validate_components() -> list[str]:
    """Validate this repo's packaging/components.yaml."""
    return validation.validate_components_file(COMPONENTS)


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    errors: list[str] = []

    if target in ("all", "components"):
        errors += validate_components()

    if target in ("all", "recipes"):
        errors += validate_all_recipes()
    elif target.startswith("recipes/"):
        errors += validate_recipe(RECIPES / target.split("/", 1)[1])

    return validation.report(errors)


if __name__ == "__main__":
    sys.exit(main())
