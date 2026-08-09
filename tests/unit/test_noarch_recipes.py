"""Invariants for the shipped noarch (``platform: any``) recipes.

The noarch *machinery* is already well covered: tests/integration/
test_platform_any.py builds real 'any' recipes end to end, test_catalog.py
proves a noarch bundle surfaces for concrete-host queries, and test_populate.py
proves such a recipe is scheduled once rather than fanned out per host.

What none of that covers is the shipped corpus under recipes/.  These tests
walk the real recipes, in the spirit of test_recipe_schema.py: a feature can be
fully implemented and fully unit-tested against tmp_path recipes while the
recipes people actually ship quietly violate its assumptions.

The central assumption is easy to miss.  A ``platform: any`` recipe still
builds on some *concrete* host -- pack_recipe resolves ``build_platform`` back
to ``detect_platform()`` -- so ``CVC_PLATFORM`` inside a noarch build script is
whatever machine happened to claim the job, not "any".  A noarch build that
reads it is therefore host-dependent by construction, which defeats the whole
point of building the artifact once.  It also breaks outright on a Windows
builder, because there is no ``_common/env-windows.sh`` (only the .ps1).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import Recipe, _is_any_recipe, _select_matrix_entry

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"

# Every concrete platform a consumer can target.  A noarch package must be
# resolvable from all of them, otherwise it publishes but never installs.
CONCRETE_PLATFORMS = [
    "linux",
    "macos",
    "windows",
    "freebsd",
    "openbsd",
    "netbsd",
    "dragonflybsd",
    "wasm",
    "wasi",
    "cosmo",
]


def _recipe_dirs() -> list[Path]:
    return sorted(d for d in RECIPES_DIR.iterdir() if (d / "recipe.yaml").is_file())


def _noarch_recipe_dirs() -> list[Path]:
    out = []
    for d in _recipe_dirs():
        with open(d / "recipe.yaml", encoding="utf-8") as fh:
            r = yaml.safe_load(fh) or {}
        matrix = (r.get("build") or {}).get("matrix") or []
        if matrix and all(e.get("platform") == "any" for e in matrix):
            out.append(d)
    return out


class TestNoarchBuildScriptsAreHostIndependent:
    """A noarch build script must not branch on the machine that builds it."""

    def test_no_noarch_recipe_reads_cvc_platform(self):
        offenders = []
        for d in _noarch_recipe_dirs():
            for script in sorted(d.glob("build.*")):
                text = script.read_text(encoding="utf-8", errors="replace")
                # Strip comments so prose explaining *why* CVC_PLATFORM is
                # avoided does not trip the check.
                code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
                if "CVC_PLATFORM" in code:
                    offenders.append(f"{d.name}/{script.name}")
        assert not offenders, (
            "noarch recipes must not read CVC_PLATFORM -- an 'any' recipe builds on an "
            "arbitrary host, so this makes the result host-dependent (and there is no "
            "_common/env-windows.sh at all):\n  " + "\n  ".join(offenders)
        )

    def test_noarch_matrix_entries_reference_scripts_that_exist(self):
        missing = []
        for d in _noarch_recipe_dirs():
            with open(d / "recipe.yaml", encoding="utf-8") as fh:
                r = yaml.safe_load(fh)
            for entry in r["build"]["matrix"]:
                script = entry.get("script")
                if script and not (d / script).is_file():
                    missing.append(f"{d.name}: {script}")
        assert not missing, "noarch matrix names a script that does not exist:\n  " + "\n  ".join(
            missing
        )


class TestNoarchResolvesEverywhere:
    """A noarch recipe must be selectable from every concrete target platform."""

    @pytest.mark.parametrize("platform", CONCRETE_PLATFORMS)
    def test_ca_bundle_resolves_for(self, platform):
        recipe = Recipe.load(RECIPES_DIR / "ca-bundle")
        entry = _select_matrix_entry(recipe, platform)
        assert entry.platform == "any"
        assert entry.script == "build.sh"

    def test_every_noarch_recipe_resolves_for_every_platform(self):
        failures = []
        for d in _noarch_recipe_dirs():
            recipe = Recipe.load(d)
            for platform in CONCRETE_PLATFORMS:
                try:
                    _select_matrix_entry(recipe, platform)
                except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                    failures.append(f"{d.name} @ {platform}: {exc}")
        assert (
            not failures
        ), "noarch recipe unreachable from a concrete platform:\n  " + "\n  ".join(failures)


class TestCaBundleIsNoarch:
    """ca-bundle is pure data; a per-platform matrix is what made it fragile.

    It shipped a build.ps1 that sourced a _common/build-vars.ps1 which never
    existed in the repo's history, dead on Windows for about a year, because a
    per-platform matrix demanded a second implementation nobody could run.
    Locking this down stops it drifting back.
    """

    def test_matrix_is_a_single_any_entry(self):
        recipe = Recipe.load(RECIPES_DIR / "ca-bundle")
        assert _is_any_recipe(recipe)
        assert [e.platform for e in recipe.build_matrix] == ["any"]

    def test_no_powershell_build_script(self):
        assert not (RECIPES_DIR / "ca-bundle" / "build.ps1").exists(), (
            "ca-bundle is noarch and has nothing platform-specific to do; a build.ps1 "
            "would be a second implementation with no way to keep it honest"
        )

    def test_declared_payload_is_data_only(self):
        with open(RECIPES_DIR / "ca-bundle" / "recipe.yaml", encoding="utf-8") as fh:
            r = yaml.safe_load(fh)
        assert r["package"]["files"] == ["etc/ssl/cert.pem", "share/ca-bundle/"]
        # cxx_std on a package with no compiled artifacts is meaningless, and
        # its presence is a hint someone re-platformed the recipe.
        assert "abi" not in r
