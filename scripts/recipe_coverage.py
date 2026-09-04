#!/usr/bin/env python3
"""Report recipe build-matrix platform coverage.

Scans ``recipes/<name>/recipe.yaml`` and reports, per platform, how many
recipes declare a build for it — and which recipes are missing a given
platform. Actually *building* every recipe on every platform is builder-
fleet work; this tool gives visibility into the declared coverage and can
gate CI on a required platform set.

Usage:
    python scripts/recipe_coverage.py                      # summary table
    python scripts/recipe_coverage.py --missing linux      # recipes lacking linux
    python scripts/recipe_coverage.py --require linux,macos,windows   # exit 1 if any lack these
    python scripts/recipe_coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("ERROR: PyYAML is required.  pip install pyyaml")

# The platforms a recipe can target (matches the recipe schema enum).
ALL_PLATFORMS = [
    "linux",
    "macos",
    "windows",
    "freebsd",
    "openbsd",
    "netbsd",
    "wasm",
    "wasm-mt",
    "wasi",
    "cosmo",
]


def recipe_platforms(recipe_yaml: Path) -> set[str]:
    """Return the set of platforms a recipe's build matrix targets."""
    try:
        doc = yaml.safe_load(recipe_yaml.read_text()) or {}
    except yaml.YAMLError:
        return set()
    matrix = (doc.get("build") or {}).get("matrix") or []
    return {e["platform"] for e in matrix if isinstance(e, dict) and "platform" in e}


def collect(recipes_dir: Path) -> dict[str, set[str]]:
    """Map recipe name -> set of platforms it builds for."""
    out: dict[str, set[str]] = {}
    for recipe_yaml in sorted(recipes_dir.glob("*/recipe.yaml")):
        out[recipe_yaml.parent.name] = recipe_platforms(recipe_yaml)
    return out


def summarize(coverage: dict[str, set[str]]) -> dict[str, int]:
    """Per-platform count of recipes that declare a build for it."""
    counts = {p: 0 for p in ALL_PLATFORMS}
    for platforms in coverage.values():
        for p in platforms:
            counts[p] = counts.get(p, 0) + 1
    return counts


def missing_for(coverage: dict[str, set[str]], platform: str) -> list[str]:
    """Recipes that do NOT declare a build for *platform*."""
    return sorted(name for name, plats in coverage.items() if platform not in plats)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report recipe platform coverage.")
    ap.add_argument("--recipes-dir", default="recipes", type=Path)
    ap.add_argument("--missing", metavar="PLATFORM", help="List recipes missing this platform.")
    ap.add_argument(
        "--require",
        metavar="P1,P2,...",
        help="Exit non-zero if any recipe is missing one of these platforms.",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args(argv)

    if not args.recipes_dir.is_dir():
        print(f"ERROR: no recipes dir at {args.recipes_dir}", file=sys.stderr)
        return 2

    coverage = collect(args.recipes_dir)
    total = len(coverage)
    counts = summarize(coverage)

    if args.json:
        print(
            json.dumps(
                {
                    "total_recipes": total,
                    "per_platform": counts,
                    "coverage": {k: sorted(v) for k, v in coverage.items()},
                },
                indent=2,
            )
        )
    elif args.missing:
        missing = missing_for(coverage, args.missing)
        print(f"{len(missing)}/{total} recipes do not build for {args.missing!r}:")
        for name in missing:
            print(f"  {name}")
    else:
        print(f"Recipe platform coverage ({total} recipes):")
        for p in ALL_PLATFORMS:
            n = counts.get(p, 0)
            pct = (100 * n / total) if total else 0
            print(f"  {p:<9} {n:>4}/{total}  ({pct:5.1f}%)")

    if args.require:
        required = [p.strip() for p in args.require.split(",") if p.strip()]
        offenders = {p: missing_for(coverage, p) for p in required}
        offenders = {p: m for p, m in offenders.items() if m}
        if offenders:
            print("", file=sys.stderr)
            for p, m in offenders.items():
                print(f"MISSING {p}: {len(m)} recipe(s) — {', '.join(m)}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
