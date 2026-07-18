#!/usr/bin/env python3
"""Validate packaging YAML files against their JSON-Schema definitions.

Usage:
    python packaging/validate.py                       # validate everything
    python packaging/validate.py components             # validate components.yaml only
    python packaging/validate.py recipes                # validate all recipes
    python packaging/validate.py recipes/zlib           # validate one recipe

Requires: pip install pyyaml jsonschema
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML is required.  pip install pyyaml")

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("ERROR: jsonschema is required.  pip install jsonschema")


ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"

# Recipes whose minted version ("<upstream_version>+cvc.<rev>") is not valid
# SemVer and so cannot be ordered by version_sort_key's parseable rank.  They
# predate the parseability gate below and are grandfathered so it RATCHETS: new
# recipes must parse, and this set may only shrink.  Do NOT add to it -- pick an
# upstream_version that parses (dot-separated dates like "2024.07.02", drop
# non-numeric suffixes, avoid a second "+").
#
#   openssh "10.4p1", openssh-win "10.0.0.0" (4 components), x264/x264-cli
#   "0.164.stable", jam/haiku-image "r1beta5", llvm-cbe "0.0.0+git.<sha>" (a
#   second "+").
_UNPARSEABLE_VERSION_GRANDFATHER = frozenset(
    {"openssh", "openssh-win", "x264", "x264-cli", "jam", "haiku-image", "llvm-cbe"}
)


def _load(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_components() -> list[str]:
    """Validate packaging/components.yaml."""
    schema = _load(SCHEMAS / "components-schema.yaml")
    doc = _load(ROOT / "components.yaml")
    errors: list[str] = []
    v = Draft202012Validator(schema)
    for e in sorted(v.iter_errors(doc), key=lambda x: list(x.absolute_path)):
        errors.append(f"components.yaml: {'.'.join(str(p) for p in e.absolute_path)}: {e.message}")

    # Cross-check: every dependency name must reference a known component.
    if "components" in doc:
        names = set(doc["components"].keys())
        for comp_name, comp in doc["components"].items():
            for dep in comp.get("dependencies", []):
                if dep["name"] not in names:
                    errors.append(
                        f"components.yaml: {comp_name}.dependencies: "
                        f"references unknown component '{dep['name']}'"
                    )
    return errors


def validate_recipe(recipe_dir: Path) -> list[str]:
    """Validate one recipe.yaml against the recipe schema."""
    recipe_file = recipe_dir / "recipe.yaml"
    if not recipe_file.exists():
        return [f"{recipe_dir}: missing recipe.yaml"]

    schema = _load(SCHEMAS / "recipe-schema.yaml")
    doc = _load(recipe_file)
    errors: list[str] = []
    v = Draft202012Validator(schema)
    for e in sorted(v.iter_errors(doc), key=lambda x: list(x.absolute_path)):
        errors.append(f"{recipe_file}: {'.'.join(str(p) for p in e.absolute_path)}: {e.message}")

    # Check that referenced scripts exist.
    if "build" in doc and "matrix" in doc["build"]:
        for entry in doc["build"]["matrix"]:
            script = recipe_dir / entry.get("script", "")
            if not script.exists():
                errors.append(f"{recipe_file}: build script '{entry['script']}' not found")

    # Check that referenced patches exist.
    for patch in doc.get("patches", []):
        if not (recipe_dir / patch).exists():
            errors.append(f"{recipe_file}: patch '{patch}' not found")

    # The minted version must be orderable.  Everything that picks "the newest"
    # -- resolver, installer, publish, the server catalog -- routes through
    # semver.version_sort_key, which ranks unparseable versions BELOW every
    # parseable one; a recipe that mints an unparseable version can therefore
    # never be selected over any parseable sibling and silently loses.  Validate
    # the exact string that gets published: "<upstream_version>+cvc.<rev>".
    rec = doc.get("recipe", {})
    name = rec.get("name", recipe_dir.name)
    if (
        "upstream_version" in rec
        and "cvc_revision" in rec
        and name not in (_UNPARSEABLE_VERSION_GRANDFATHER)
    ):
        full_version = f"{rec['upstream_version']}+cvc.{rec['cvc_revision']}"
        try:
            # Imported lazily so validate.py keeps running without cvcpkg on the
            # path (it only needs pyyaml/jsonschema otherwise).
            from cvcpkg.semver import Version

            Version.parse(full_version)
        except ImportError:
            pass  # cvcpkg not importable here; the recipe-graph CI job covers it
        except ValueError:
            errors.append(
                f"{recipe_file}: version {full_version!r} is not orderable SemVer "
                "(version_sort_key would rank it below every parseable version). "
                "Use a dot-separated, numeric upstream_version."
            )

    return errors


def validate_all_recipes() -> list[str]:
    """Find and validate every recipe under recipes/."""
    recipes_root = ROOT.parent / "recipes"
    errors: list[str] = []
    if not recipes_root.is_dir():
        return [f"No recipes/ directory found at {recipes_root}"]
    for d in sorted(recipes_root.iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            errors.extend(validate_recipe(d))
    return errors


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    errors: list[str] = []

    if target in ("all", "components"):
        errors.extend(validate_components())

    if target == "all" or target == "recipes":
        errors.extend(validate_all_recipes())
    elif target.startswith("recipes/"):
        recipe_dir = ROOT.parent / target
        errors.extend(validate_recipe(recipe_dir))

    if errors:
        print(f"\n{'='*60}")
        print(f"VALIDATION FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        print()
        return 1
    else:
        print("✓ All validations passed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
