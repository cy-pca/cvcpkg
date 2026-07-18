"""packaging/validate.py's version-parseability gate.

Everything that picks "the newest" routes through semver.version_sort_key,
which ranks an unparseable version BELOW every parseable one -- so a recipe that
mints an unparseable version silently loses to any parseable sibling.  The gate
rejects a NEW such recipe while grandfathering the ones that predate it, so it
ratchets: the allowlist may only shrink.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "recipes"


def _load_validate():
    spec = importlib.util.spec_from_file_location(
        "cvc_validate", ROOT / "packaging" / "validate.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


validate = _load_validate()
pytest.importorskip("cvcpkg.semver", reason="the gate needs cvcpkg on the path")
from cvcpkg.builder import list_recipes  # noqa: E402
from cvcpkg.semver import Version  # noqa: E402


def _actual_unparseable() -> set[str]:
    bad = set()
    for r in list_recipes(RECIPES):
        try:
            Version.parse(f"{r.upstream_version}+cvc.{r.cvc_revision}")
        except ValueError:
            bad.add(r.name)
    return bad


def test_grandfather_set_equals_the_real_unparseable_set():
    # The allowlist must be EXACTLY the recipes that are actually unparseable.
    # If it lists a recipe that has since been fixed, the entry is stale and
    # must be removed (the ratchet).  If a recipe is unparseable but NOT listed,
    # the gate below would have failed it -- so this pins both directions.
    assert _actual_unparseable() == validate._UNPARSEABLE_VERSION_GRANDFATHER, (
        "grandfather list drifted from the real unparseable set; "
        "remove fixed recipes, and never add new ones"
    )


def test_all_real_recipes_pass_the_gate():
    # With the grandfather list correct, the whole tree validates clean.
    errors = [e for e in validate.validate_all_recipes() if "orderable" in e]
    assert errors == [], errors


def test_a_new_unparseable_recipe_is_rejected(tmp_path):
    d = tmp_path / "recipes" / "newpkg"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(
        "schema_version: 1\n"
        "recipe:\n"
        "  name: newpkg\n"
        '  upstream_version: "1.0stable"\n'  # not numeric -> unparseable
        "  cvc_revision: 1\n"
        "  maintainer: x\n"
        "  maintainer_email: x@x\n"
        "  homepage: http://x\n"
        "  license: MIT\n"
        '  description: "x"\n'
        "source: {type: tarball, url: 'http://x/x.tgz', sha256: '" + "0" * 64 + "'}\n"
        "build: {matrix: [{platform: linux, script: build.sh}]}\n"
        "package: {files: ['bin/x']}\n"
    )
    (d / "build.sh").write_text("#!/bin/sh\n")
    errors = validate.validate_recipe(d)
    assert any("orderable" in e for e in errors), errors


def test_a_new_parseable_recipe_passes(tmp_path):
    d = tmp_path / "recipes" / "goodpkg"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(
        "schema_version: 1\n"
        "recipe:\n"
        "  name: goodpkg\n"
        '  upstream_version: "2024.07.02"\n'  # dot-date, parses as 2024.7.2
        "  cvc_revision: 3\n"
        "  maintainer: x\n"
        "  maintainer_email: x@x\n"
        "  homepage: http://x\n"
        "  license: MIT\n"
        '  description: "x"\n'
        "source: {type: tarball, url: 'http://x/x.tgz', sha256: '" + "0" * 64 + "'}\n"
        "build: {matrix: [{platform: linux, script: build.sh}]}\n"
        "package: {files: ['bin/x']}\n"
    )
    (d / "build.sh").write_text("#!/bin/sh\n")
    errors = validate.validate_recipe(d)
    assert not any("orderable" in e for e in errors), errors


def test_re2_is_not_grandfathered_and_parses():
    # The recipe this PR fixed: it must have left the grandfather list (it was
    # never in it -- it *parsed*, just as a wrong prerelease) and now order right.
    assert "re2" not in validate._UNPARSEABLE_VERSION_GRANDFATHER
    v = Version.parse("2024.07.02+cvc.1")
    assert (v.major, v.minor, v.patch, v.pre) == (2024, 7, 2, "")
