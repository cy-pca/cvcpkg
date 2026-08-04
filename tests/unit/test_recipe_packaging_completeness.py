"""Packaging must carry every file a recipe's build script reads.

A recipe directory is an opaque bundle of build inputs.  ``recipe.yaml`` and
the ``script:`` keys it declares are only the visible part of it: a build
script may also read files that NOTHING declares -- docs it installs into its
output, config templates it patches into a source tree, a boot script it
injects into a disk image.

``cvcpkg_build_backend._sync_recipes`` used to copy recipe directories through
an extension allowlist (``{.yaml,.sh,.ps1,.cmake,.patch}``).  That dropped all
three of ``haiku-image``'s undeclared inputs -- ``README-import.md`` (wrong
extension), ``UserBuildConfig`` and ``UserBootscript`` (no extension at all).
``recipes/haiku-image/build.sh`` reads them out of ``${RECIPE_DIR}`` under
``set -euo pipefail`` *after* it has built the Haiku cross-toolchain, so a
wheel-installed cvcpkg died on a ``cp``, an hour into an otherwise-good build,
with nothing in the failure pointing back at packaging.

These tests pin the contract that replaced the allowlist: recipe directories
ship WHOLESALE, and every own-directory reference in every recipe script
resolves to a file that survived.  The scan is corpus-wide rather than a list
of known files, so a new recipe that reads a new kind of sibling file is
covered the day it lands -- which is the only property that would have caught
haiku-image without someone thinking of it first.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parents[2]
RECIPES = REPO / "recipes"
SCRIPT_SUFFIXES = (".sh", ".ps1")

# poetry-core hooks the backend re-exports at import time.
_POETRY_HOOKS = (
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_backend():
    """Import cvcpkg_build_backend, stubbing poetry-core only if it is absent.

    poetry-core is a BUILD dependency; it is not necessarily installed in the
    test environment, and the recipe sync under test does not touch it.  The
    stub is torn down again so the rest of the suite sees an unpolluted
    ``sys.modules``.
    """
    try:
        import poetry.core.masonry.api  # noqa: F401

        return _load(REPO / "cvcpkg_build_backend.py", "cvcpkg_build_backend")
    except ImportError:
        pass

    api = types.ModuleType("poetry.core.masonry.api")
    for hook in _POETRY_HOOKS:
        setattr(api, hook, lambda *a, **k: None)
    stubs = {
        "poetry": types.ModuleType("poetry"),
        "poetry.core": types.ModuleType("poetry.core"),
        "poetry.core.masonry": types.ModuleType("poetry.core.masonry"),
        "poetry.core.masonry.api": api,
    }
    added = [n for n in stubs if n not in sys.modules]
    sys.modules.update({n: m for n, m in stubs.items() if n in added})
    try:
        return _load(REPO / "cvcpkg_build_backend.py", "cvcpkg_build_backend")
    finally:
        for n in added:
            sys.modules.pop(n, None)


def _load_verify_wheel():
    """The reference scanner lives with the publish gate that also uses it."""
    return _load(REPO / "packaging" / "verify_wheel.py", "verify_wheel")


def _is_junk(backend, rel: PurePosixPath) -> bool:
    """Mirror the backend's denylist, so the two cannot drift apart."""
    return any(
        part in backend._EXCLUDE_NAMES or part.endswith(backend._EXCLUDE_SUFFIXES)
        for part in rel.parts
    )


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def backend():
    return _load_backend()


@pytest.fixture(scope="module")
def synced(tmp_path_factory, backend):
    """The real recipe tree, run through the real sync into a scratch dir."""
    dest = tmp_path_factory.mktemp("packaged") / "recipes"
    backend._sync_recipes(RECIPES, dest)
    return dest


def test_sync_copies_the_recipe_tree_wholesale(synced, backend):
    """Every file in recipes/ survives, byte-for-byte, minus working-tree junk.

    Stated as set equality rather than a spot-check: any filter added to the
    sync -- by extension, by name, by size -- fails here, which is the whole
    point.  packaging/cvcpkg.spec bundles ``ROOT/"recipes"`` wholesale for the
    PyInstaller binary, so this is also what keeps the two packaging routes
    describing the same recipe directory.
    """
    expected = {f for f in _files(RECIPES) if not _is_junk(backend, PurePosixPath(f))}
    assert expected, "no recipes found — test is looking in the wrong place"
    assert _files(synced) == expected


def test_every_file_a_recipe_script_reads_survives_packaging(synced):
    """No recipe script is left reading a file that packaging dropped.

    Only files that EXIST in recipes/ are required to survive: a script naming
    a file that is in no tree at all is a broken recipe, which packaging can
    neither cause nor cure.
    """
    vw = _load_verify_wheel()
    missing: list[str] = []
    checked = 0

    for script in sorted(RECIPES.rglob("*")):
        if not script.is_file() or script.suffix not in SCRIPT_SUFFIXES:
            continue
        recipe_dir = f"recipes/{script.parent.relative_to(RECIPES).as_posix()}"
        text = script.read_text(encoding="utf-8", errors="replace")
        for ref in sorted(vw.recipe_dir_file_refs(text)):
            target = vw.resolve_recipe_ref(recipe_dir, ref)
            if target is None:
                continue
            rel = target[len("recipes/") :]
            if not (RECIPES / rel).is_file():
                continue
            checked += 1
            if not (synced / rel).is_file():
                missing.append(f"{rel} (read by {script.relative_to(REPO)})")

    assert not missing, "packaging dropped files that recipe scripts read:\n" + "\n".join(missing)
    # Guard against the scan silently going blind: if the reference regexes
    # ever stop matching, the loop above passes vacuously.
    assert checked > 100, f"only {checked} references resolved — reference scan is broken"


# The extension allowlist ``_sync_recipes`` used to filter by.  It no longer
# exists in the backend -- removing it is the fix these tests pin -- so it
# lives here as a historical constant describing what USED to be dropped.
_OLD_SYNC_ALLOWLIST = {".yaml", ".sh", ".ps1", ".cmake", ".patch"}


def _at_risk_inputs(recipe: str) -> set[str]:
    """Files ``recipe``'s build.sh reads that the old allowlist would have dropped.

    Derived from the build script rather than hardcoded, because WHICH files a
    recipe injects is the recipe's business and changes with it -- what must
    not change is that a file with no extension, or a non-script one, still
    survives packaging.  haiku-image is mid-rewrite on another branch
    (``UserBootscript`` is being replaced by a ``launch_daemon`` job file,
    ``launch-sshd``); both are extensionless, so both are the same regression
    and neither should need an edit here.
    """
    vw = _load_verify_wheel()
    build_sh = (RECIPES / recipe / "build.sh").read_text(encoding="utf-8", errors="replace")
    at_risk = set()
    for ref in vw.recipe_dir_file_refs(build_sh):
        target = vw.resolve_recipe_ref(f"recipes/{recipe}", ref)
        if target is None:
            continue
        rel = target[len("recipes/") :]
        if not (RECIPES / rel).is_file():
            continue
        name = PurePosixPath(rel).name
        if PurePosixPath(name).suffix not in _OLD_SYNC_ALLOWLIST:
            at_risk.add(name)
    return at_risk


def test_haiku_image_undeclared_inputs_survive(synced):
    """The files the extension allowlist actually dropped.

    Pinned by name as well as by the corpus scan above, because these are the
    regression: ``.md`` was the wrong extension and the rest have none.  The
    set is computed from build.sh so that rewriting the recipe cannot quietly
    empty it -- the anchors and the non-empty assertion below are what keep
    this test from passing vacuously.
    """
    at_risk = _at_risk_inputs("haiku-image")
    assert at_risk, (
        "no undeclared haiku-image inputs resolved — either build.sh stopped "
        "reading its own directory or the reference scan is broken"
    )
    # Present on every revision of this recipe so far; if one of these ever
    # goes away, that is a real change worth failing on rather than papering
    # over, and the message says so.
    for anchor in ("README-import.md", "UserBuildConfig"):
        assert (
            anchor in at_risk
        ), f"haiku-image/build.sh no longer reads {anchor} — update this test"

    missing = sorted(n for n in at_risk if not (synced / "haiku-image" / n).is_file())
    assert not missing, f"packaging dropped undeclared haiku-image inputs: {missing}"


def test_sync_drops_working_tree_junk(tmp_path, backend):
    """Copying wholesale still must not ship editor/VCS/interpreter droppings."""
    src = tmp_path / "recipes"
    (src / "demo").mkdir(parents=True)
    (src / "demo" / "recipe.yaml").write_text("schema_version: 1\n")
    (src / "demo" / "UserBootscript").write_text("#!/bin/sh\n")
    (src / "demo" / "build.sh.orig").write_text("stale\n")
    (src / "demo" / "notes.md~").write_text("backup\n")
    (src / "demo" / "__pycache__").mkdir()
    (src / "demo" / "__pycache__" / "x.pyc").write_bytes(b"\x00")

    dest = tmp_path / "out"
    backend._sync_recipes(src, dest)

    assert _files(dest) == {"demo/recipe.yaml", "demo/UserBootscript"}


def test_sync_replaces_a_stale_destination(tmp_path, backend):
    """A previous sync's leftovers must not survive into the next build."""
    src = tmp_path / "recipes"
    (src / "demo").mkdir(parents=True)
    (src / "demo" / "recipe.yaml").write_text("schema_version: 1\n")

    dest = tmp_path / "out"
    (dest / "ghost").mkdir(parents=True)
    (dest / "ghost" / "recipe.yaml").write_text("schema_version: 1\n")

    backend._sync_recipes(src, dest)

    assert _files(dest) == {"demo/recipe.yaml"}


def test_sync_outside_a_checkout_is_a_no_op(tmp_path, backend):
    """Building from an sdist has no recipes/ to copy; that is not an error."""
    dest = tmp_path / "out"
    backend._sync_recipes(tmp_path / "absent", dest)
    assert not dest.exists()
