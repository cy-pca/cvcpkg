# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Schema + graph validation for cvcpkg packaging YAML, shipped in the package.

Historically the validator lived in ``packaging/validate.py`` (this repo only)
and hard-wired both the recipe root and the JSON-Schema files to a libcvc-deps
checkout.  That made ``cvcpkg validate`` unusable anywhere else: a downstream
project could not validate its own ``cvcpkg/recipes/<name>/`` without checking
out libcvc-deps and co-locating the recipe under ``recipes/`` — the very
co-location the recipe-ownership policy forbids.

This module moves the checking logic AND the schemas into the installed package
(schemas loaded via ``importlib.resources``), so ``cvcpkg validate`` runs from
any repo's CI with the same ``--recipes-dir`` / path-target surface as
``build``/``pack``.  ``packaging/validate.py`` is now a thin shim over this
module and keeps working for this repo's CI.

Checks performed:
  * recipe.yaml conforms to the recipe JSON-Schema;
  * every referenced build script and patch file exists;
  * the minted version ``<upstream_version>+cvc.<rev>`` is orderable SemVer
    (an unparseable version silently loses every "pick the newest" comparison);
  * cross-recipe dependencies resolve — each ``depends.{build,runtime,
    host_tools}`` name is another recipe (or a ``provides`` slot) in the merged
    recipe set;
  * components.yaml conforms to its schema and its dependency names cross-check.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib import resources
from pathlib import Path

import yaml

from cvcpkg.optional import require_jsonschema

# Recipes whose minted version is not orderable SemVer.  This gate RATCHETS: new
# recipes must parse, and the set may only shrink.  It is EMPTY and must stay so
# — pick an upstream_version that parses (dot-separated numeric components; a git
# hash or tag goes in a "-<pre>" prerelease, never a second "+").
_UNPARSEABLE_VERSION_GRANDFATHER: frozenset[str] = frozenset()


# ── schema loading (from the installed package) ─────────────────────────


def load_schema(kind: str) -> dict:
    """Load a bundled packaging schema by *kind*.

    *kind* is ``"recipe"``, ``"components"`` or ``"manifest"``.  The schema
    ships inside the ``cvcpkg`` package (``cvcpkg/schemas/<kind>-schema.yaml``)
    and is read via :mod:`importlib.resources`, so it resolves in a wheel
    install with no source checkout.
    """
    ref = resources.files("cvcpkg").joinpath("schemas", f"{kind}-schema.yaml")
    with resources.as_file(ref) as path:
        return _load_yaml(Path(path))


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── recipe discovery over a set of directories ──────────────────────────


def _iter_recipe_dirs(root: Path) -> Iterable[Path]:
    """Yield the recipe subdirectories under a ``recipes/`` *root*."""
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("_") and (child / "recipe.yaml").is_file():
            yield child


def _recipe_name(recipe_dir: Path) -> str:
    doc = _load_yaml(recipe_dir / "recipe.yaml")
    return doc.get("recipe", {}).get("name", recipe_dir.name)


def build_recipe_map(roots: Iterable[Path], singles: Iterable[Path] = ()) -> dict[str, Path]:
    """Map recipe name → recipe dir across *roots* (dirs of recipes) and
    *singles* (individual recipe dirs).  Later entries win on name conflicts,
    matching the ``--recipes-dir`` overlay ("later directories win")."""
    found: dict[str, Path] = {}
    for root in roots:
        for rd in _iter_recipe_dirs(Path(root)):
            found[_recipe_name(rd)] = rd
    for rd in singles:
        rd = Path(rd)
        if (rd / "recipe.yaml").is_file():
            found[_recipe_name(rd)] = rd
    return found


def known_targets(recipe_map: dict[str, Path]) -> set[str]:
    """Every name a dependency may legally reference: recipe names plus the
    ``provides`` slots they announce."""
    known: set[str] = set(recipe_map)
    for rd in recipe_map.values():
        doc = _load_yaml(rd / "recipe.yaml")
        for slot in doc.get("provides") or []:
            known.add(slot)
    return known


def resolve_recipe_dirs(
    extra_dirs: Iterable[str | Path] = (), *, no_default: bool = False
) -> list[Path]:
    """The recipe search path: the default (bundled/repo) recipes unless
    *no_default*, then the *extra_dirs* overlays, then (still unless
    *no_default*) the CWD ``./recipes`` auto-overlay when it holds a recipe and
    isn't already listed.  Later entries win on name conflicts.  Mirrors the
    CLI's ``_resolve_recipes_dirs`` (keeping ``cvcpkg validate`` consistent with
    ``build``) but without importing click."""
    dirs: list[Path] = []
    if not no_default:
        try:
            from cvcpkg.builder import find_recipes_dir

            dirs.append(find_recipes_dir())
        except Exception:
            pass
    for d in extra_dirs:
        p = Path(d).resolve()
        if p not in dirs:
            dirs.append(p)
    if not no_default:
        from cvcpkg.builder import cwd_recipes_overlay

        cwd_overlay = cwd_recipes_overlay(dirs)
        if cwd_overlay is not None:
            dirs.append(cwd_overlay)
    return dirs


# ── individual validators ───────────────────────────────────────────────


def validate_recipe_dir(recipe_dir: Path, *, schema: dict | None = None) -> list[str]:
    """Validate one recipe: schema, script/patch existence, version order."""
    jsonschema = require_jsonschema()

    recipe_dir = Path(recipe_dir)
    recipe_file = recipe_dir / "recipe.yaml"
    if not recipe_file.exists():
        return [f"{recipe_dir}: missing recipe.yaml"]

    if schema is None:
        schema = load_schema("recipe")
    doc = _load_yaml(recipe_file)
    errors: list[str] = []

    v = jsonschema.Draft202012Validator(schema)
    for e in sorted(v.iter_errors(doc), key=lambda x: list(x.absolute_path)):
        errors.append(f"{recipe_file}: {'.'.join(str(p) for p in e.absolute_path)}: {e.message}")

    # Referenced build scripts must exist.
    if "build" in doc and "matrix" in doc["build"]:
        for entry in doc["build"]["matrix"]:
            script = recipe_dir / entry.get("script", "")
            if not script.exists():
                errors.append(f"{recipe_file}: build script '{entry.get('script')}' not found")

    # Referenced patches must exist.
    for patch in doc.get("patches") or []:
        if not (recipe_dir / patch).exists():
            errors.append(f"{recipe_file}: patch '{patch}' not found")

    # The minted version must be orderable SemVer.  Everything that picks "the
    # newest" routes through semver.version_sort_key, which ranks unparseable
    # versions BELOW every parseable one; a recipe that mints an unparseable
    # version can therefore never be selected over any parseable sibling and
    # silently loses.  Validate the exact published string.
    rec = doc.get("recipe", {})
    name = rec.get("name", recipe_dir.name)
    if (
        "upstream_version" in rec
        and "cvc_revision" in rec
        and name not in _UNPARSEABLE_VERSION_GRANDFATHER
    ):
        full_version = f"{rec['upstream_version']}+cvc.{rec['cvc_revision']}"
        try:
            from cvcpkg.semver import Version

            Version.parse(full_version)
        except ImportError:
            pass  # cvcpkg.semver unavailable; the recipe-graph job covers it
        except ValueError:
            errors.append(
                f"{recipe_file}: version {full_version!r} is not orderable SemVer "
                "(version_sort_key would rank it below every parseable version). "
                "Use a dot-separated, numeric upstream_version."
            )

    return errors


def validate_cross_deps(to_check: dict[str, Path], universe: set[str]) -> list[str]:
    """Every dependency of each recipe in *to_check* must resolve to a name in
    *universe* (a recipe name or a provided slot in the merged set)."""
    errors: list[str] = []
    for name in sorted(to_check):
        recipe_dir = to_check[name]
        doc = _load_yaml(recipe_dir / "recipe.yaml")
        deps = doc.get("depends", {}) or {}
        for role in ("build", "runtime", "host_tools"):
            for entry in deps.get(role) or []:
                dep = entry.get("name") if isinstance(entry, dict) else entry
                if dep and dep not in universe:
                    errors.append(
                        f"{recipe_dir / 'recipe.yaml'}: depends.{role}: "
                        f"unknown dependency '{dep}' — not a recipe or a provided "
                        "slot in the resolved recipe set (pass --recipes-dir for "
                        "the dir that defines it, or drop --no-default-recipes)"
                    )
    return errors


def validate_components_file(components_file: Path) -> list[str]:
    """Validate a components.yaml against its schema + dependency cross-refs."""
    jsonschema = require_jsonschema()

    components_file = Path(components_file)
    schema = load_schema("components")
    doc = _load_yaml(components_file)
    errors: list[str] = []

    v = jsonschema.Draft202012Validator(schema)
    for e in sorted(v.iter_errors(doc), key=lambda x: list(x.absolute_path)):
        errors.append(
            f"{components_file}: {'.'.join(str(p) for p in e.absolute_path)}: {e.message}"
        )

    if "components" in doc:
        names = set(doc["components"].keys())
        for comp_name, comp in doc["components"].items():
            for dep in comp.get("dependencies", []):
                if dep["name"] not in names:
                    errors.append(
                        f"{components_file}: {comp_name}.dependencies: "
                        f"references unknown component '{dep['name']}'"
                    )
    return errors


def _find_components_file(resolved_dirs: Iterable[Path]) -> Path | None:
    """Locate packaging/components.yaml relative to a resolved recipes dir
    (``<repo>/recipes`` → ``<repo>/packaging/components.yaml``).  Returns None
    when there is no libcvc-deps checkout on the recipe path (e.g. a bundled
    wheel install validating a downstream repo)."""
    for d in resolved_dirs:
        candidate = Path(d).parent / "packaging" / "components.yaml"
        if candidate.is_file():
            return candidate
    return None


# ── orchestration ───────────────────────────────────────────────────────


def run(
    target: str = "all",
    *,
    extra_dirs: Iterable[str | Path] = (),
    no_default: bool = False,
) -> list[str]:
    """Validate *target* over the resolved recipe search path; return errors.

    *target* is one of the keyword forms (``all`` | ``components`` | ``recipes``
    | ``recipes/<name>``) or a filesystem path — either a single recipe dir
    (contains recipe.yaml) or a ``recipes/`` dir (contains recipe subdirs).
    """
    resolved = resolve_recipe_dirs(extra_dirs, no_default=no_default)
    errors: list[str] = []

    tp = Path(target)
    is_single = tp.is_dir() and (tp / "recipe.yaml").is_file()
    is_root = tp.is_dir() and not is_single and any(_iter_recipe_dirs(tp))

    if target in ("all", "components", "recipes"):
        if target in ("all", "components"):
            comp = _find_components_file(resolved)
            if comp is not None:
                errors += validate_components_file(comp)
            elif target == "components":
                errors.append(
                    "components.yaml not found — no libcvc-deps checkout on the "
                    "recipe search path (nothing to validate for target "
                    "'components')"
                )
        if target in ("all", "recipes"):
            rmap = build_recipe_map(resolved)
            if not rmap:
                errors.append(
                    "no recipes found on the recipe search path "
                    "(use --recipes-dir or run where recipes/ is discoverable)"
                )
            universe = known_targets(rmap)
            for name in sorted(rmap):
                errors += validate_recipe_dir(rmap[name])
            errors += validate_cross_deps(rmap, universe)

    elif is_single or is_root:
        if is_single:
            to_check = build_recipe_map([], [tp])
            # The recipe's siblings in its own recipes/ dir are part of its
            # natural set (e.g. cvc-cli depends on its sibling libcvc), so fold
            # the containing dir into the universe alongside the default tree.
            universe_map = build_recipe_map([*resolved, tp.parent], [tp])
        else:
            to_check = build_recipe_map([tp], [])
            universe_map = build_recipe_map([*resolved, tp], [])
        universe = known_targets(universe_map)
        for name in sorted(to_check):
            errors += validate_recipe_dir(to_check[name])
        errors += validate_cross_deps(to_check, universe)

    elif target.startswith("recipes/"):
        name = target[len("recipes/") :]
        rmap = build_recipe_map(resolved)
        if name not in rmap:
            searched = ", ".join(str(d) for d in resolved) or "(none)"
            errors.append(f"recipe '{name}' not found in recipe dirs: {searched}")
        else:
            universe = known_targets(rmap)
            errors += validate_recipe_dir(rmap[name])
            errors += validate_cross_deps({name: rmap[name]}, universe)

    else:
        errors.append(
            f"unknown validate target: {target!r} — expected 'all', 'components', "
            "'recipes', 'recipes/<name>', or a path to a recipe or recipes/ dir"
        )

    return errors


def report(errors: list[str]) -> int:
    """Print *errors* in the canonical format and return a process exit code."""
    if errors:
        print(f"\n{'=' * 60}")
        print(f"VALIDATION FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        print()
        return 1
    print("✓ All validations passed.")
    return 0
