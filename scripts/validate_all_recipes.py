#!/usr/bin/env python3
"""Validate every recipe in a recipes/ tree, plus the dependency-closure invariant.

Two classes of check:

1. Per-recipe validation (schema, referenced build scripts and patches exist,
   orderable SemVer) via ``cvcpkg.validation.validate_recipe_dir``.

2. The *closure invariant*: a recipe that declares ``platform: P`` in its build
   matrix must have every dependency that applies on P also buildable on P.
   Without this a recipe advertises a platform it can never actually build —
   a "phantom claim" — and the failure only surfaces when a builder picks the
   job up, hours into a DAG.

   Host tools are resolved on the BUILD host, so for a cross target (wasm,
   wasi, cosmo) only ``runtime`` deps must cover the target; ``build`` deps
   need only cover the entry's ``host_platform`` (default linux).

Usage:
    python scripts/validate_all_recipes.py                 # everything
    python scripts/validate_all_recipes.py --closure-only
    python scripts/validate_all_recipes.py --recipes-dir recipes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Validate against THIS checkout, not whatever cvcpkg happens to be installed
# site-wide. A stale installed copy carries a stale schema, which shows up as
# spurious "additional properties are not allowed" errors for fields the repo
# added since (min_disk_gb was exactly this).
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("ERROR: PyYAML is required.  pip install pyyaml")

CROSS_PLATFORMS = {"wasm", "wasm-mt", "wasi", "cosmo"}


def load(recipe_yaml: Path) -> dict:
    return yaml.safe_load(recipe_yaml.read_text(encoding="utf-8")) or {}


def matrix_platforms(doc: dict) -> set[str]:
    matrix = (doc.get("build") or {}).get("matrix") or []
    return {e["platform"] for e in matrix if isinstance(e, dict) and "platform" in e}


def host_platforms_for(doc: dict, target: str) -> set[str]:
    matrix = (doc.get("build") or {}).get("matrix") or []
    return {
        e["host_platform"]
        for e in matrix
        if isinstance(e, dict) and e.get("platform") == target and e.get("host_platform")
    }


def covers(doc: dict, platform: str) -> bool:
    plats = matrix_platforms(doc)
    return "any" in plats or platform in plats


def deps_of(doc: dict, role: str) -> list[dict]:
    out = []
    for entry in (doc.get("depends") or {}).get(role) or []:
        if isinstance(entry, str):
            out.append({"name": entry})
        elif isinstance(entry, dict) and entry.get("name"):
            out.append(entry)
    return out


def check_closure(docs: dict[str, dict]) -> list[str]:
    """Report every (recipe, platform) whose dependency closure is broken."""
    errors: list[str] = []
    for name in sorted(docs):
        doc = docs[name]
        for platform in sorted(matrix_platforms(doc) - {"any"}):
            hosts = host_platforms_for(doc, platform) or {"linux"}
            for role in ("build", "runtime"):
                for dep in deps_of(doc, role):
                    dep_name = dep["name"]
                    dep_doc = docs.get(dep_name)
                    if dep_doc is None:
                        continue  # provided slot or external; validate_cross_deps covers it
                    only_on = dep.get("platforms")
                    if only_on and platform not in only_on:
                        continue  # dep does not apply on this platform
                    if covers(dep_doc, platform):
                        continue
                    if platform in CROSS_PLATFORMS and role == "build":
                        # Host tool: satisfied if it builds on any declared host.
                        if any(covers(dep_doc, h) for h in hosts):
                            continue
                    errors.append(
                        f"{name}: declares platform '{platform}' but {role} dependency "
                        f"'{dep_name}' has no build for it "
                        f"(add a '{platform}' matrix entry to {dep_name}, or restrict the "
                        f"dependency with 'platforms:')"
                    )
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recipes-dir", default="recipes")
    ap.add_argument("--closure-only", action="store_true")
    ap.add_argument("--schema-only", action="store_true")
    args = ap.parse_args(argv)

    recipes_dir = Path(args.recipes_dir)
    if not recipes_dir.is_dir():
        print(f"ERROR: no such directory: {recipes_dir}", file=sys.stderr)
        return 2

    recipe_files = sorted(recipes_dir.glob("*/recipe.yaml"))
    docs = {rf.parent.name: load(rf) for rf in recipe_files}

    errors: list[str] = []
    ran: list[str] = []

    if not args.closure_only:
        from cvcpkg.validation import load_schema, validate_recipe_dir

        schema = load_schema("recipe")
        for rf in recipe_files:
            errors += validate_recipe_dir(rf.parent, schema=schema)
        ran.append("schema")

    if not args.schema_only:
        errors += check_closure(docs)
        ran.append("dependency closure")

    if errors:
        for e in errors:
            print(e)
        print(f"\n{len(errors)} problem(s) across {len(recipe_files)} recipes")
        return 1

    print(f"OK: {len(recipe_files)} recipes pass {' + '.join(ran)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
