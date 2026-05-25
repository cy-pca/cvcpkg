"""Integration tests for the source-build fallback in ``cvcpkg install``.

Verifies that when prebuilt binaries are unavailable (network error,
missing catalog entry), ``--fallback-to-source`` correctly builds the
component from its recipe.

These tests use the real zlib recipe shipped with libcvc-deps and
require build tools (cmake, make/ninja) to be installed.

Run on merge to master via the source-fallback-ci.yml workflow, or
locally::

    cd tools/cvcpkg
    poetry run pytest tests/integration/test_source_fallback.py -v

Markers:
    source_fallback — all tests in this module carry this marker so
    CI can select them with ``-m source_fallback``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ── Paths ───────────────────────────────────────────────────────

_CVCPKG_ROOT = Path(__file__).resolve().parents[2]  # tools/cvcpkg
_REPO_ROOT = _CVCPKG_ROOT.parents[1]  # libcvc-deps
_RECIPES_DIR = _REPO_ROOT / "recipes"

# Skip the entire module if the recipes directory is missing (e.g.
# when cvcpkg is installed as a standalone package without the repo).
pytestmark = [
    pytest.mark.source_fallback,
    pytest.mark.skipif(
        not (_RECIPES_DIR / "zlib" / "recipe.yaml").is_file(),
        reason="zlib recipe not found — not running inside libcvc-deps repo",
    ),
]


# ── Helpers ─────────────────────────────────────────────────────


def _has_build_tools() -> bool:
    """Check that cmake and a C compiler are available."""
    import shutil

    return bool(
        shutil.which("cmake") and (shutil.which("cc") or shutil.which("gcc") or shutil.which("cl"))
    )


def _run_cvcpkg(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``cvcpkg`` via ``python -m cvcpkg`` in the cvcpkg source tree."""
    cmd = [sys.executable, "-m", "cvcpkg", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        cwd=str(_CVCPKG_ROOT),
        env={**os.environ, "PYTHONPATH": str(_CVCPKG_ROOT / "src")},
    )


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.skipif(not _has_build_tools(), reason="cmake or C compiler not found")
class TestSourceFallback:
    """Verify the fallback-to-source path in ``cvcpkg install``."""

    def test_fallback_on_unreachable_catalog(self, tmp_path: Path) -> None:
        """When the catalog URL is unreachable, zlib is built from source."""
        prefix = tmp_path / "prefix"
        result = _run_cvcpkg(
            "install",
            "zlib",
            "--prefix",
            str(prefix),
            "--catalog",
            "https://localhost:1/nonexistent-catalog.yaml",
            "--fallback-to-source",
            "--recipes-dir",
            str(_RECIPES_DIR),
            check=False,
        )
        # The command should succeed (exit 0).
        assert result.returncode == 0, (
            f"cvcpkg install --fallback-to-source failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (
            "building" in result.stdout.lower()
            or "building" in result.stderr.lower()
            or "source" in result.stdout.lower()
        )
        # zlib should produce include/zlib.h and a library.
        assert (
            prefix / "include" / "zlib.h"
        ).is_file(), f"zlib.h not found in {prefix / 'include'}; contents: " + str(
            list((prefix / "include").iterdir()) if (prefix / "include").is_dir() else "N/A"
        )

    def test_fallback_on_missing_component(self, tmp_path: Path) -> None:
        """When the catalog exists but has no entry, build from source."""
        # Create a minimal valid empty catalog.
        catalog_file = tmp_path / "empty-catalog.yaml"
        import yaml

        catalog_file.write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [],
                }
            )
        )
        prefix = tmp_path / "prefix"
        result = _run_cvcpkg(
            "install",
            "zlib",
            "--prefix",
            str(prefix),
            "--catalog",
            str(catalog_file),
            "--fallback-to-source",
            "--recipes-dir",
            str(_RECIPES_DIR),
            check=False,
        )
        assert result.returncode == 0, (
            f"install with empty catalog + fallback failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (prefix / "include" / "zlib.h").is_file()

    def test_no_fallback_flag_fails_on_missing(self, tmp_path: Path) -> None:
        """Without --fallback-to-source, a missing component errors out."""
        catalog_file = tmp_path / "empty-catalog.yaml"
        import yaml

        catalog_file.write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [],
                }
            )
        )
        prefix = tmp_path / "prefix"
        result = _run_cvcpkg(
            "install",
            "zlib",
            "--prefix",
            str(prefix),
            "--catalog",
            str(catalog_file),
            check=False,
        )
        # Should report an error — either via exit code or error message.
        combined = result.stdout + result.stderr
        assert result.returncode != 0 or "error" in combined.lower(), (
            f"Expected an error for missing component without fallback:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Prefix should not have been populated.
        assert not (prefix / "include" / "zlib.h").exists()

    def test_fallback_no_recipe_errors(self, tmp_path: Path) -> None:
        """Fallback for a component with no recipe gives a clear error."""
        catalog_file = tmp_path / "empty-catalog.yaml"
        import yaml

        catalog_file.write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [],
                }
            )
        )
        empty_recipes = tmp_path / "empty-recipes"
        empty_recipes.mkdir()
        prefix = tmp_path / "prefix"
        result = _run_cvcpkg(
            "install",
            "nonexistent-pkg",
            "--prefix",
            str(prefix),
            "--catalog",
            str(catalog_file),
            "--fallback-to-source",
            "--recipes-dir",
            str(empty_recipes),
            check=False,
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0 or "error" in combined.lower()
        assert (
            "no recipe found" in combined.lower()
            or "no prebuilt binary" in combined.lower()
            or "error" in combined.lower()
        )

    def test_lockfile_records_source_build(self, tmp_path: Path) -> None:
        """Source-built components appear in the lockfile with source marker."""
        catalog_file = tmp_path / "empty-catalog.yaml"
        import yaml

        catalog_file.write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [],
                }
            )
        )
        prefix = tmp_path / "prefix"
        result = _run_cvcpkg(
            "install",
            "zlib",
            "--prefix",
            str(prefix),
            "--catalog",
            str(catalog_file),
            "--fallback-to-source",
            "--recipes-dir",
            str(_RECIPES_DIR),
            check=False,
        )
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        lockfile = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
        assert lockfile.is_file(), "lockfile not written"
        lock_data = yaml.safe_load(lockfile.read_text())
        bundles = lock_data.get("bundles", [])
        assert len(bundles) == 1
        assert bundles[0]["name"] == "zlib"
        assert bundles[0]["source_release"] == "source-build"
