"""End-to-end canonization of the source-recipe workflow (roadmap Phase 14).

A *source recipe* delivers just (patched) source files — `platform: any`,
no toolchain.  A downstream platform recipe depends on it, finds the staged
source via ``CVC_DEPS_PREFIX``, and compiles it into a real binary.

This test builds both through ``build_all`` with actual compilation, so the
source -> binary contract is locked in and cannot silently regress.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cvcpkg.builder import build_all, build_recipe
from cvcpkg.platform import detect_platform

# The consumer compiles C, so we need a C compiler and a POSIX shell build
# script.  Skip cleanly where neither is available (e.g. bare Windows CI).
_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _CC is None or _BASH is None or sys.platform == "win32",
    reason="source-recipe e2e needs a C compiler + bash (POSIX build scripts)",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"))


def _make_recipes(root: Path) -> Path:
    """Create a tmp recipes dir with a source recipe + a downstream consumer."""
    recipes = root / "recipes"

    # Ship the real staging helper so we exercise what authors would use.
    common = recipes / "_common"
    common.mkdir(parents=True, exist_ok=True)
    real_common = Path(__file__).resolve().parents[2] / "recipes" / "_common"
    shutil.copy(real_common / "stage-source.sh", common / "stage-source.sh")

    # Vendored source trees live under recipes/_vendored/<name>/ (the
    # convention _resolve_vendored uses: recipe_dir.parent/_vendored/<path>).
    _write(
        recipes / "_vendored" / "mathsrc" / "addmul.h",
        """
        #ifndef ADDMUL_H
        #define ADDMUL_H
        int addmul(int a, int b, int c);
        #endif
        """,
    )
    _write(
        recipes / "_vendored" / "mathsrc" / "addmul.c",
        """
        #include "addmul.h"
        int addmul(int a, int b, int c) { return a * b + c; }
        """,
    )

    # ── Source recipe: mathsrc (platform: any, files only) ──────────
    _write(
        recipes / "mathsrc" / "recipe.yaml",
        """
        schema_version: 1
        recipe:
          name: mathsrc
          upstream_version: "1.0.0"
          cvc_revision: 1
          description: Source-only math helpers (header + impl); no toolchain.
          tags: [source, math]
        source:
          type: vendored
          path: mathsrc
        build:
          build_type_independent: true
          matrix:
            - platform: any
              script: build.sh
        package:
          files:
            - src
        """,
    )
    # A source recipe just stages its (patched) tree — no compilation.
    _write(
        recipes / "mathsrc" / "build.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        . "$(dirname "$0")/../_common/stage-source.sh"
        cvc_stage_source
        """,
    )

    # ── Downstream consumer: mathdemo (real platform, compiles source) ──
    plat = detect_platform()
    _write(
        recipes / "_vendored" / "mathdemo" / "main.c",
        """
        #include <stdio.h>
        #include "addmul.h"
        int main(void) { printf("%d\\n", addmul(6, 7, 2)); return 0; }
        """,
    )
    _write(
        recipes / "mathdemo" / "recipe.yaml",
        f"""
        schema_version: 1
        recipe:
          name: mathdemo
          upstream_version: "1.0.0"
          cvc_revision: 1
          description: Consumes the mathsrc source package and builds a binary.
          tags: [demo]
        source:
          type: vendored
          path: mathdemo
        depends:
          build:
            - mathsrc
        build:
          matrix:
            - platform: {plat}
              script: build.sh
        package:
          files:
            - bin
        """,
    )
    # The consumer locates the staged source via CVC_DEPS_PREFIX and compiles
    # it together with its own main into a binary.
    _write(
        recipes / "mathdemo" / "build.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        . "$(dirname "$0")/../_common/stage-source.sh"
        SRC="$(cvc_source_dir_of mathsrc)"
        test -f "$SRC/addmul.c" || { echo "staged source not found at $SRC" >&2; exit 1; }
        mkdir -p "$CVC_INSTALL_DIR/bin"
        cc="${CC:-cc}"
        "$cc" -I "$SRC" -o "$CVC_INSTALL_DIR/bin/mathdemo" \
            "$CVC_SOURCE_DIR/main.c" "$SRC/addmul.c"
        """,
    )
    return recipes


class TestSourceRecipeWorkflow:
    def test_source_then_binary_end_to_end(self, tmp_path):
        recipes = _make_recipes(tmp_path)
        prefix = tmp_path / "prefix"
        plat = detect_platform()

        contexts = build_all(recipes, platform=plat, prefix=prefix, no_cache=True)
        built = {c.recipe.name for c in contexts}
        assert {"mathsrc", "mathdemo"} <= built, f"built: {built}"

        # 1. The source recipe staged its files under the canonical layout.
        #    With no --build-prefix separation requested, everything merges into
        #    the single shared prefix (legacy layout, kept for back-compat).
        staged = prefix / "src" / "mathsrc"
        assert (staged / "addmul.c").is_file()
        assert (staged / "addmul.h").is_file()

        # 2. The source recipe is platform-independent: its build matrix is
        #    entirely `platform: any`, so it is built once and reused.
        src_ctx = next(c for c in contexts if c.recipe.name == "mathsrc")
        assert all(m.platform == "any" for m in src_ctx.recipe.build_matrix)

        # 3. The downstream consumer produced a real binary from the staged
        #    source, and it runs correctly (6*7 + 2 == 44).
        binary = prefix / "bin" / ("mathdemo.exe" if sys.platform == "win32" else "mathdemo")
        assert binary.is_file(), f"consumer binary missing: {binary}"
        out = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "44"

    def test_source_stages_into_build_prefix_not_the_deliverable(self, tmp_path):
        """The contract: a source package is consumed as a BUILD dependency, so
        it stages into the build prefix and never ships in the deliverable.

        Placement follows the dependency edge -- mathdemo declares
        ``depends.build: [mathsrc]`` -- not the fact that mathsrc is `any` or
        "source".  The consumer's own artifacts still land in the install prefix.
        """
        recipes = _make_recipes(tmp_path)
        prefix = tmp_path / "prefix"
        build_prefix = tmp_path / "prefix.build"
        plat = detect_platform()

        contexts = build_all(
            recipes,
            platform=plat,
            prefix=prefix,
            build_prefix=build_prefix,
            no_cache=True,
        )
        built = {c.recipe.name for c in contexts}
        assert {"mathsrc", "mathdemo"} <= built, f"built: {built}"

        # The staged source lives in the BUILD prefix...
        assert (build_prefix / "src" / "mathsrc" / "addmul.c").is_file()
        assert (build_prefix / "src" / "mathsrc" / "addmul.h").is_file()
        # ...and must NOT pollute the deliverable.
        assert not (prefix / "src" / "mathsrc").exists(), "source leaked into the install prefix"

        # The consumer still compiled against it and its binary ships.
        binary = prefix / "bin" / ("mathdemo.exe" if sys.platform == "win32" else "mathdemo")
        assert binary.is_file(), f"consumer binary missing: {binary}"
        out = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "44"

    def test_source_recipe_has_no_build_artifacts(self, tmp_path):
        """A source package is *only* files — no libs/binaries of its own."""
        recipes = _make_recipes(tmp_path)
        prefix = tmp_path / "prefix"
        # Build the source recipe directly as a platform-independent (`any`)
        # package.
        ctx = build_recipe(recipes / "mathsrc", platform="any", prefix=prefix)
        assert (prefix / "src" / "mathsrc" / "addmul.c").is_file()
        # No compiled output directories were created by the source recipe —
        # its install is purely the staged source tree.
        assert not (ctx.install_dir / "lib").exists()
        assert not (ctx.install_dir / "bin").exists()
        assert (ctx.install_dir / "src" / "mathsrc" / "addmul.h").is_file()
