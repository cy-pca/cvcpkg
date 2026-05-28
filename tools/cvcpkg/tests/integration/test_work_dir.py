"""Integration tests for --work-dir / work_dir_root.

These tests create real (tiny) recipes with shell build scripts that
produce actual files, then run build_recipe / build_all *without*
mocks to verify that all intermediate work lands under the specified
work-dir root — not in the system temp directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

# Skip the entire module on Windows — build scripts are .sh
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Shell-script integration tests require bash",
)


# ── Helpers ─────────────────────────────────────────────────────


def _write_recipe(
    recipes_dir: Path,
    name: str,
    *,
    deps: list[str] | None = None,
    script_body: str = "",
) -> Path:
    """Create a minimal recipe with a real build.sh."""
    rd = recipes_dir / name
    rd.mkdir(parents=True, exist_ok=True)

    recipe: dict = {
        "schema_version": 1,
        "recipe": {
            "name": name,
            "upstream_version": "0.1.0",
            "cvc_revision": 1,
        },
        "source": {"type": "vendored", "path": "."},
        "patches": [],
        "build": {
            "matrix": [{"platform": "linux", "script": "build.sh"}],
        },
        "package": {"files": ["lib/*", "include/*"], "cmake_packages": []},
    }
    if deps:
        recipe["depends"] = {"build": [{"name": d} for d in deps]}

    (rd / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))

    # Default script: create a library stub and a header
    if not script_body:
        script_body = (
            "#!/bin/sh\n"
            "set -e\n"
            'mkdir -p "$CVC_INSTALL_DIR/lib"\n'
            'mkdir -p "$CVC_INSTALL_DIR/include"\n'
            f'echo "lib-{name}" > "$CVC_INSTALL_DIR/lib/lib{name}.a"\n'
            f'echo "hdr-{name}" > "$CVC_INSTALL_DIR/include/{name}.h"\n'
        )

    build_sh = rd / "build.sh"
    build_sh.write_text(script_body)
    build_sh.chmod(0o755)
    return rd


def _all_work_dirs_under(root: Path, contexts) -> None:
    """Assert every context's work_dir is a child of *root*."""
    for ctx in contexts:
        assert ctx.work_dir.parent == root, (
            f"{ctx.recipe.name}: work_dir {ctx.work_dir} " f"is not under expected root {root}"
        )


def _no_cvcpkg_dirs_in_system_temp(exclusions: set[Path] | None = None) -> None:
    """Assert no cvcpkg-* directories leaked into the system temp dir.

    *exclusions* is an optional set of paths that were present before
    the test and should be ignored.
    """
    tmpdir = Path(tempfile.gettempdir())
    leaked = [
        p
        for p in tmpdir.iterdir()
        if p.is_dir()
        and p.name.startswith("cvcpkg-")
        and (exclusions is None or p not in exclusions)
    ]
    assert not leaked, f"Leaked cvcpkg dirs in system temp: {leaked}"


def _snapshot_cvcpkg_temps() -> set[Path]:
    """Return the set of cvcpkg-* dirs currently in system temp."""
    tmpdir = Path(tempfile.gettempdir())
    return {p for p in tmpdir.iterdir() if p.is_dir() and p.name.startswith("cvcpkg-")}


# ── Single recipe ──────────────────────────────────────────────


class TestBuildRecipeWorkDir:
    """build_recipe() with work_dir_root — no mocks."""

    def test_single_recipe_work_dir(self, tmp_path):
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "mytool")

        pre_existing = _snapshot_cvcpkg_temps()

        ctx = build_recipe(
            rd,
            platform="linux",
            work_dir_root=scratch,
            keep_build_dir=True,
        )

        # work_dir lives under scratch
        assert ctx.work_dir.parent == scratch
        assert ctx.work_dir.name.startswith("cvcpkg-mytool-")

        # Build actually produced files
        assert (ctx.install_dir / "lib" / "libmytool.a").is_file()
        assert (ctx.install_dir / "include" / "mytool.h").is_file()

        # Nothing leaked to system temp
        _no_cvcpkg_dirs_in_system_temp(pre_existing)

    def test_build_dir_and_source_dir_under_scratch(self, tmp_path):
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "widget")

        ctx = build_recipe(
            rd,
            platform="linux",
            work_dir_root=scratch,
            keep_build_dir=True,
        )

        # build_dir is a subdir of work_dir (which is under scratch)
        assert str(ctx.build_dir).startswith(str(scratch))
        assert str(ctx.install_dir).startswith(str(scratch))


# ── Multiple recipes with dependencies ──────────────────────────


class TestBuildAllWorkDir:
    """build_all() with dependencies and work_dir_root — no mocks."""

    def test_dependency_chain(self, tmp_path):
        """Three recipes: gamma depends on beta, beta depends on alpha."""
        from cvcpkg.builder import build_all

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        prefix = tmp_path / "prefix"

        _write_recipe(recipes_dir, "alpha")
        _write_recipe(recipes_dir, "beta", deps=["alpha"])
        _write_recipe(recipes_dir, "gamma", deps=["beta"])

        pre_existing = _snapshot_cvcpkg_temps()

        contexts = build_all(
            recipes_dir,
            platform="linux",
            prefix=prefix,
            per_component=True,
            work_dir_root=scratch,
            keep_build_dir=True,
            no_cache=True,
        )

        # All three built successfully
        names = sorted(c.recipe.name for c in contexts)
        assert names == ["alpha", "beta", "gamma"]

        # Every work_dir is under scratch
        _all_work_dirs_under(scratch, contexts)

        # Each recipe produced its artifacts
        for ctx in contexts:
            lib = ctx.install_dir / "lib" / f"lib{ctx.recipe.name}.a"
            hdr = ctx.install_dir / "include" / f"{ctx.recipe.name}.h"
            assert lib.is_file(), f"Missing {lib}"
            assert hdr.is_file(), f"Missing {hdr}"

        # Artifacts merged into shared prefix
        for name in ("alpha", "beta", "gamma"):
            assert (prefix / "lib" / f"lib{name}.a").is_file()
            assert (prefix / "include" / f"{name}.h").is_file()

        # Nothing leaked to system temp
        _no_cvcpkg_dirs_in_system_temp(pre_existing)

    def test_deps_visible_via_prefix(self, tmp_path):
        """Verify that a recipe can see its dependency's files in CVC_DEPS_PREFIX."""
        from cvcpkg.builder import build_all

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        prefix = tmp_path / "prefix"

        _write_recipe(recipes_dir, "base")

        # "consumer" checks that CVC_DEPS_PREFIX/include/base.h exists
        consumer_script = (
            "#!/bin/sh\n"
            "set -e\n"
            'if [ ! -f "$CVC_DEPS_PREFIX/include/base.h" ]; then\n'
            '  echo "ERROR: base.h not found in CVC_DEPS_PREFIX=$CVC_DEPS_PREFIX"\n'
            "  exit 1\n"
            "fi\n"
            'mkdir -p "$CVC_INSTALL_DIR/lib"\n'
            'echo "consumer-ok" > "$CVC_INSTALL_DIR/lib/libconsumer.a"\n'
            'mkdir -p "$CVC_INSTALL_DIR/include"\n'
            'echo "consumer-hdr" > "$CVC_INSTALL_DIR/include/consumer.h"\n'
        )
        _write_recipe(recipes_dir, "consumer", deps=["base"], script_body=consumer_script)

        contexts = build_all(
            recipes_dir,
            platform="linux",
            prefix=prefix,
            per_component=True,
            work_dir_root=scratch,
            no_cache=True,
        )

        names = sorted(c.recipe.name for c in contexts)
        assert names == ["base", "consumer"]
        _all_work_dirs_under(scratch, contexts)

        # Consumer's lib proves it found base.h
        consumer_ctx = [c for c in contexts if c.recipe.name == "consumer"][0]
        assert (consumer_ctx.install_dir / "lib" / "libconsumer.a").read_text() == "consumer-ok\n"

    def test_auto_prefix_under_work_dir_root(self, tmp_path):
        """When --prefix is omitted, the auto-created prefix also goes under work_dir_root."""
        from cvcpkg.builder import build_all

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"

        _write_recipe(recipes_dir, "solo")

        pre_existing = _snapshot_cvcpkg_temps()

        contexts = build_all(
            recipes_dir,
            platform="linux",
            work_dir_root=scratch,
            no_cache=True,
        )

        assert len(contexts) == 1
        # Auto-prefix should be under scratch, not system temp
        assert contexts[0].prefix.parent == scratch
        _no_cvcpkg_dirs_in_system_temp(pre_existing)

    def test_non_per_component_mode(self, tmp_path):
        """build_all without per_component also respects work_dir_root."""
        from cvcpkg.builder import build_all

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"

        _write_recipe(recipes_dir, "libx")
        _write_recipe(recipes_dir, "liby", deps=["libx"])

        pre_existing = _snapshot_cvcpkg_temps()

        contexts = build_all(
            recipes_dir,
            platform="linux",
            prefix=tmp_path / "prefix",
            per_component=False,
            work_dir_root=scratch,
            keep_build_dir=True,
            no_cache=True,
        )

        names = sorted(c.recipe.name for c in contexts)
        assert names == ["libx", "liby"]

        # In non-per-component mode, build_recipe is called which uses
        # work_dir_root for its work_dir.
        for ctx in contexts:
            assert str(ctx.work_dir).startswith(
                str(scratch)
            ), f"{ctx.recipe.name}: work_dir {ctx.work_dir} not under {scratch}"

        _no_cvcpkg_dirs_in_system_temp(pre_existing)

    def test_work_dir_root_created_if_missing(self, tmp_path):
        """work_dir_root is auto-created even if deeply nested."""
        from cvcpkg.builder import build_all

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "deep" / "nested" / "scratch"
        assert not scratch.exists()

        _write_recipe(recipes_dir, "mkdirtest")

        build_all(
            recipes_dir,
            platform="linux",
            prefix=tmp_path / "prefix",
            work_dir_root=scratch,
            no_cache=True,
        )

        assert scratch.is_dir()
