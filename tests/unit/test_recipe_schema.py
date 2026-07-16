"""Tests that the shipped recipe schema accepts what the builder supports.

These exist because the two can drift apart silently.  The builder's own tests
construct recipes in tmp_path and never touch packaging/validate.py, while
validate.py only ever walks recipes/ — so a feature can be fully implemented,
fully unit-tested, and still be impossible to express in a real recipe.  That
is exactly how `platform: any` shipped unusable: _is_any_recipe honoured it,
the Phase 14 end-to-end test exercised it from a temp dir, and the schema
rejected it for anything under recipes/.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packaging" / "schemas" / "recipe-schema.yaml"


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _recipe(**over):
    r = {
        "schema_version": 1,
        "recipe": {"name": "widget", "upstream_version": "1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "third-party/widget"},
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": ["lib/"]},
    }
    r.update(over)
    return r


def _valid(schema, recipe) -> bool:
    try:
        jsonschema.validate(recipe, schema)
        return True
    except jsonschema.ValidationError:
        return False


class TestPlatformAny:
    def test_platform_any_is_accepted(self, schema):
        # A source recipe / pure-Python wheel must be expressible in recipes/.
        r = _recipe(build={"matrix": [{"platform": "any", "script": "build.sh"}]})
        assert _valid(schema, r), "schema rejects platform: any — source recipes cannot ship"

    def test_builder_and_schema_agree_on_any(self, schema):
        # Guard the specific drift: the builder treats "any" as special, so the
        # schema must permit the value the builder keys on.
        from cvcpkg.builder import MatrixEntry, Recipe, SourceSpec, _is_any_recipe

        r = _recipe(build={"matrix": [{"platform": "any", "script": "build.sh"}]})
        assert _valid(schema, r)

        recipe = Recipe(
            name="widget",
            upstream_version="1.0",
            cvc_revision=1,
            source=SourceSpec(type="vendored", path="x"),
            patches=[],
            build_matrix=[MatrixEntry(platform="any", script="build.sh")],
            package_files=["lib/"],
            test_script=None,
            raw=r,
            recipe_dir=Path("."),
        )
        assert _is_any_recipe(recipe) is True

    def test_bogus_platform_still_rejected(self, schema):
        # The enum must not have been loosened into a free-form string.
        r = _recipe(build={"matrix": [{"platform": "solaris", "script": "build.sh"}]})
        assert not _valid(schema, r)

    @pytest.mark.parametrize(
        "plat",
        [
            "linux",
            "macos",
            "windows",
            "windows-gnu",
            "wasm",
            "wasi",
            "cosmo",
            "freebsd",
            "openbsd",
            "netbsd",
        ],
    )
    def test_known_platforms_still_accepted(self, plat, schema):
        assert _valid(schema, _recipe(build={"matrix": [{"platform": plat, "script": "build.sh"}]}))


class TestShippedRecipesValidate:
    def test_every_shipped_recipe_matches_the_schema(self, schema):
        # Mirrors packaging/validate.py so schema drift fails the unit suite
        # too, not only the separate CI step.
        recipes_dir = SCHEMA_PATH.parents[2] / "recipes"
        bad = []
        for d in sorted(recipes_dir.iterdir()):
            f = d / "recipe.yaml"
            if not f.is_file():
                continue
            with open(f, encoding="utf-8") as fh:
                r = yaml.safe_load(fh)
            try:
                jsonschema.validate(r, schema)
            except jsonschema.ValidationError as e:
                bad.append(f"{d.name}: {e.message[:100]}")
        assert not bad, "recipes failing schema:\n  " + "\n  ".join(bad)
