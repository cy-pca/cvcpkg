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

# The recipe schema now ships inside the cvcpkg package; load it the same way
# the validator does (importlib.resources), so this test tracks what ships.
from cvcpkg.validation import load_schema  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def schema():
    return load_schema("recipe")


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


class TestProvidesSlots:
    """`provides:` must be expressible for the cases it exists to serve."""

    def test_version_bearing_slot_accepted(self, schema):
        # `cpython-3.13` is the motivating example — a slot name carrying a
        # version, i.e. containing a dot.  The package-name pattern would
        # reject it and ship the feature unusable.
        assert _valid(schema, _recipe(provides=["cpython-3.13"]))

    def test_plain_slot_accepted(self, schema):
        assert _valid(schema, _recipe(provides=["libgl-implementation"]))

    def test_multiple_slots_accepted(self, schema):
        assert _valid(schema, _recipe(provides=["cpython-3.13", "python-interpreter"]))

    def test_empty_list_accepted(self, schema):
        # `patches: []` is a common idiom in these recipes; `provides: []`
        # must not be a gratuitous failure.
        assert _valid(schema, _recipe(provides=[]))

    def test_uppercase_slot_rejected(self, schema):
        assert not _valid(schema, _recipe(provides=["Bad_Name"]))

    def test_builder_reads_what_the_schema_accepts(self, schema, tmp_path):
        # Guard the schema/builder seam the same way platform: any is guarded.
        from cvcpkg.builder import Recipe

        r = _recipe(provides=["cpython-3.13"])
        assert _valid(schema, r)
        (tmp_path / "widget").mkdir(parents=True)
        with open(tmp_path / "widget" / "recipe.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(r, f)
        assert Recipe.load(tmp_path / "widget").provides == ["cpython-3.13"]


class TestRecipeKind:
    """`recipe.kind` must be expressible — the builder reads it (Recipe.load),
    the manifest emits it (meta.kind), and the docs advertise it, but the
    schema's additionalProperties: false silently rejected it in recipes/."""

    @pytest.mark.parametrize("kind", ["data", "media", "config", "iso"])
    def test_documented_kinds_accepted(self, kind, schema):
        r = _recipe()
        r["recipe"]["kind"] = kind
        assert _valid(schema, r), f"schema rejects recipe.kind: {kind}"

    def test_bogus_kind_rejected(self, schema):
        # kind is an enum of the documented hints, not a free-form string.
        r = _recipe()
        r["recipe"]["kind"] = "binary"
        assert not _valid(schema, r)

    def test_omitted_kind_still_accepted(self, schema):
        assert _valid(schema, _recipe())

    def test_builder_reads_what_the_schema_accepts(self, schema, tmp_path):
        from cvcpkg.builder import Recipe

        r = _recipe(build={"matrix": [{"platform": "any", "script": "build.sh"}]})
        r["recipe"]["kind"] = "data"
        assert _valid(schema, r)
        (tmp_path / "widget").mkdir(parents=True)
        with open(tmp_path / "widget" / "recipe.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(r, f)
        assert Recipe.load(tmp_path / "widget").kind == "data"


class TestShippedRecipesValidate:
    def test_every_shipped_recipe_matches_the_schema(self, schema):
        # Mirrors packaging/validate.py so schema drift fails the unit suite
        # too, not only the separate CI step.
        recipes_dir = REPO_ROOT / "recipes"
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


README_PATH = REPO_ROOT / "README.md"

# A dummy 64-hex digest so placeholder shas (e.g. "<sha256-of-tarball>") pass
# the schema's ``^[0-9a-f]{64}$`` pattern; the README examples are structural,
# not real, so only the shape is under test.
_DUMMY_SHA = "0" * 64


def _readme_yaml_blocks() -> list[tuple[int, dict]]:
    """Return (fence-index, parsed) for every ```yaml block in the README
    that parses to a mapping (skips CI-workflow list snippets etc.)."""
    text = README_PATH.read_text(encoding="utf-8")
    blocks: list[tuple[int, dict]] = []
    in_yaml = False
    buf: list[str] = []
    idx = 0
    for line in text.splitlines():
        if line.strip() == "```yaml":
            in_yaml, buf = True, []
            continue
        if in_yaml and line.strip() == "```":
            in_yaml = False
            idx += 1
            try:
                doc = yaml.safe_load("\n".join(buf))
            except yaml.YAMLError:
                continue
            if isinstance(doc, dict):
                blocks.append((idx, doc))
            continue
        if in_yaml:
            buf.append(line)
    return blocks


def _is_full_recipe(doc: dict) -> bool:
    """A full recipe.yaml example carries build/package structure — enough to
    distinguish it from cvc-requirements.yaml, config snippets, or the
    deliberately partial ``recipe:`` version fragment."""
    return any(k in doc for k in ("build", "build_matrix", "package"))


class TestReadmeRecipeExamples:
    """recipe.yaml examples in the README must match the real schema.  This is
    exactly the drift PR #309 fixed for the ``any`` example while leaving the
    "Write a recipe" and versioning examples in the old, unvalidatable format
    (top-level fields, ``build_matrix``, ``build.system``, ``dependencies``) —
    a copy-paste trap for downstream authors.

    Detection is structural (not "has ``schema_version``"), because the whole
    failure mode is examples that *omit* the fields the schema requires.
    """

    def test_readme_has_full_recipe_examples(self):
        # Guard against the extractor silently matching nothing.
        n = sum(1 for _, d in _readme_yaml_blocks() if _is_full_recipe(d))
        assert n >= 2, f"expected >=2 full recipe examples in README, found {n}"

    def test_full_recipe_examples_validate(self, schema):
        bad = []
        for idx, doc in _readme_yaml_blocks():
            if not _is_full_recipe(doc):
                continue
            src = doc.get("source")
            if isinstance(src, dict) and "sha256" in src:
                src["sha256"] = _DUMMY_SHA  # normalize placeholder digests
            try:
                jsonschema.validate(doc, schema)
            except jsonschema.ValidationError as e:
                name = (doc.get("recipe") or {}).get("name", "?")
                bad.append(f"README yaml block #{idx} ({name}): {e.message[:120]}")
        assert not bad, "README recipe examples failing schema:\n  " + "\n  ".join(bad)

    def test_no_flat_recipe_fields(self):
        # The tell-tale of the pre-#309 format: recipe identity fields at the
        # top level instead of nested under ``recipe:``.  Catches both broken
        # full examples and partial fragments (e.g. the versioning snippet).
        offenders = [
            idx for idx, doc in _readme_yaml_blocks() if "name" in doc and "upstream_version" in doc
        ]
        assert not offenders, (
            "README yaml blocks put recipe fields at the top level instead of "
            f"under `recipe:` (blocks: {offenders})"
        )
