"""Integration tests for ``cvcpkg build --incremental`` and the ``--no-deps``
default.

Incremental mode keeps a STABLE work/build tree keyed by (recipe, platform,
config, link) and does not wipe it, so a re-run recompiles only what changed.
These tests build tiny real vendored recipes (bash build scripts, no mocks) to
verify the tree persists across runs, that a sentinel dropped into the build dir
survives a second run, and that a config change gets its own keyed tree.

The ``--no-deps`` default is verified functionally: a trivial no-dep recipe
builds by default (no ``--with-deps``) from a custom recipes dir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from cvcpkg.platform import detect_platform

# Build scripts are POSIX .sh — skip on Windows.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Shell-script integration tests require bash",
)


def _write_recipe(recipes_dir: Path, name: str) -> Path:
    """Create a minimal vendored recipe with a real build.sh."""
    rd = recipes_dir / name
    rd.mkdir(parents=True, exist_ok=True)
    recipe = {
        "schema_version": 1,
        "recipe": {"name": name, "upstream_version": "0.1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "."},
        "patches": [],
        # Cover "linux" (tests pass platform="linux" explicitly) and the host
        # platform (the CLI-driven test builds for detect_platform(), e.g. "macos").
        # build.sh is POSIX and this module skips Windows, so any host is covered.
        "build": {
            "matrix": [
                {"platform": p, "script": "build.sh"}
                for p in dict.fromkeys(["linux", detect_platform()])
            ]
        },
        "package": {"files": ["lib/*", "include/*"], "cmake_packages": []},
    }
    (rd / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    script = (
        "#!/bin/sh\n"
        "set -e\n"
        'mkdir -p "$CVC_INSTALL_DIR/lib" "$CVC_INSTALL_DIR/include"\n'
        f'echo "lib-{name}" > "$CVC_INSTALL_DIR/lib/lib{name}.a"\n'
        f'echo "hdr-{name}" > "$CVC_INSTALL_DIR/include/{name}.h"\n'
        # Prove the build dir is the CWD and drop a compile artifact into it.
        'echo built > "$CVC_BUILD_DIR/compiled.o"\n'
    )
    build_sh = rd / "build.sh"
    build_sh.write_text(script)
    build_sh.chmod(0o755)
    return rd


# ── incremental keying + reuse ──────────────────────────────────


class TestIncrementalBuild:
    def test_build_dir_reused_between_runs(self, tmp_path):
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "mytool")

        ctx1 = build_recipe(rd, platform="linux", incremental=True, work_dir_root=scratch)

        # Keyed, stable work dir under the scratch root (not a random temp).
        assert ctx1.work_dir == scratch / "mytool-linux-release-shared"
        # Build dir survives an incremental build (not wiped).
        assert ctx1.build_dir.is_dir()

        # Drop a sentinel to prove the SAME tree is reused, not recreated.
        sentinel = ctx1.build_dir / "SENTINEL"
        sentinel.write_text("keep me")

        ctx2 = build_recipe(rd, platform="linux", incremental=True, work_dir_root=scratch)

        assert ctx2.work_dir == ctx1.work_dir
        assert ctx2.build_dir == ctx1.build_dir
        assert sentinel.is_file(), "incremental re-run wiped the build dir"
        assert sentinel.read_text() == "keep me"

    def test_config_change_uses_a_different_keyed_dir(self, tmp_path):
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "mytool")

        rel = build_recipe(
            rd, platform="linux", config="release", incremental=True, work_dir_root=scratch
        )
        dbg = build_recipe(
            rd, platform="linux", config="debug", incremental=True, work_dir_root=scratch
        )

        assert rel.work_dir == scratch / "mytool-linux-release-shared"
        assert dbg.work_dir == scratch / "mytool-linux-debug-shared"
        assert rel.work_dir != dbg.work_dir
        # A mismatched build never reuses the wrong tree.
        assert (rel.build_dir / "compiled.o").is_file()
        assert (dbg.build_dir / "compiled.o").is_file()

    def test_link_change_uses_a_different_keyed_dir(self, tmp_path):
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "mytool")

        shared = build_recipe(
            rd, platform="linux", link="shared", incremental=True, work_dir_root=scratch
        )
        static = build_recipe(
            rd, platform="linux", link="static", incremental=True, work_dir_root=scratch
        )
        assert shared.work_dir != static.work_dir
        assert static.work_dir == scratch / "mytool-linux-release-static"

    def test_default_build_uses_fresh_temp_and_wipes(self, tmp_path):
        """Without --incremental the build tree is a fresh temp dir, wiped."""
        from cvcpkg.builder import build_recipe

        recipes_dir = tmp_path / "recipes"
        scratch = tmp_path / "scratch"
        rd = _write_recipe(recipes_dir, "mytool")

        ctx = build_recipe(rd, platform="linux", work_dir_root=scratch)

        # A random temp dir under scratch, not the incremental keyed name.
        assert ctx.work_dir.parent == scratch
        assert ctx.work_dir.name.startswith("cvcpkg-mytool-")
        # Non-incremental default cleans the build dir for reproducibility.
        assert not ctx.build_dir.is_dir()


# ── --no-deps default (functional) ──────────────────────────────


class TestNoDepsDefault:
    def test_trivial_recipe_builds_by_default(self, tmp_path):
        """No --with-deps: the named recipe builds and installs into --prefix."""
        from cvcpkg.cli import main

        recipes_dir = tmp_path / "recipes"
        _write_recipe(recipes_dir, "solo")
        prefix = tmp_path / "prefix"

        ret = main(
            [
                "build",
                "solo",
                "--local",
                "--prefix",
                str(prefix),
                "--recipes-dir",
                str(recipes_dir),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        assert (prefix / "lib" / "libsolo.a").is_file()
        assert (prefix / "include" / "solo.h").is_file()
