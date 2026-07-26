"""Unit tests for the CWD ``./recipes`` auto-overlay and the ``build`` command
dependency default.

These lock in the one-shot contract: from a repo root that has a ``recipes/``
directory, ``cvcpkg build <name>`` (and ``cvcpkg validate``) find the repo-local
recipe with NO ``--recipes-dir``, a same-named local recipe OVERRIDES the
bundled one, and ``cvcpkg build`` skips source-building deps by default
(``--no-deps``), opting in with ``--with-deps``.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _write_recipe(recipes_dir: Path, name: str, *, marker: str = "") -> Path:
    """Create a minimal valid recipe under *recipes_dir*/<name>/."""
    rd = recipes_dir / name
    rd.mkdir(parents=True, exist_ok=True)
    recipe = {
        "schema_version": 1,
        "recipe": {"name": name, "upstream_version": "0.1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "."},
        "patches": [],
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": ["lib/*"]},
    }
    (rd / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    (rd / "build.sh").write_text(f"#!/bin/sh\ntrue\n# {marker}\n")
    return rd


# ── CWD ./recipes overlay ───────────────────────────────────────


class TestCwdRecipesOverlay:
    def test_overlay_detected_when_cwd_has_recipes(self, tmp_path, monkeypatch):
        from cvcpkg.builder import cwd_recipes_overlay

        _write_recipe(tmp_path / "recipes", "mylib")
        monkeypatch.chdir(tmp_path)

        overlay = cwd_recipes_overlay([])
        assert overlay == (tmp_path / "recipes").resolve()

    def test_overlay_none_when_no_recipe_yaml(self, tmp_path, monkeypatch):
        from cvcpkg.builder import cwd_recipes_overlay

        (tmp_path / "recipes").mkdir()  # empty — no */recipe.yaml
        monkeypatch.chdir(tmp_path)

        assert cwd_recipes_overlay([]) is None

    def test_overlay_deduped_when_already_listed(self, tmp_path, monkeypatch):
        from cvcpkg.builder import cwd_recipes_overlay

        recipes = tmp_path / "recipes"
        _write_recipe(recipes, "mylib")
        monkeypatch.chdir(tmp_path)

        # Already on the search path -> not added twice.
        assert cwd_recipes_overlay([recipes]) is None

    def test_resolve_recipes_dirs_includes_cwd(self, tmp_path, monkeypatch):
        from cvcpkg.cli._helpers import _resolve_recipes_dirs

        _write_recipe(tmp_path / "recipes", "mylib")
        monkeypatch.chdir(tmp_path)

        dirs = _resolve_recipes_dirs()
        assert (tmp_path / "recipes").resolve() in dirs
        # The CWD overlay is appended last so it wins on name conflicts.
        assert dirs[-1] == (tmp_path / "recipes").resolve()

    def test_name_lookup_resolves_cwd_recipe_without_flag(self, tmp_path, monkeypatch):
        from cvcpkg.cli._build import _resolve_recipe_dir

        _write_recipe(tmp_path / "recipes", "mylib")
        monkeypatch.chdir(tmp_path)

        resolved = _resolve_recipe_dir("mylib")
        assert resolved == (tmp_path / "recipes" / "mylib").resolve()

    def test_cwd_recipe_overrides_same_named_bundled(self, tmp_path, monkeypatch):
        """A CWD recipe with the same name as a bundled/default one wins."""
        from cvcpkg.builder import find_recipes_dir, list_recipes
        from cvcpkg.cli._build import _resolve_recipe_dir

        # Pick a name that really exists in the default recipe set, so this
        # exercises a genuine conflict rather than a lookup miss.
        default_names = [r.name for r in list_recipes(find_recipes_dir())]
        assert default_names, "expected a non-empty default recipe set"
        clash = default_names[0]

        _write_recipe(tmp_path / "recipes", clash, marker="CWD_WINS")
        monkeypatch.chdir(tmp_path)

        resolved = _resolve_recipe_dir(clash)
        assert resolved == (tmp_path / "recipes" / clash).resolve()
        assert "CWD_WINS" in (resolved / "build.sh").read_text()

    def test_no_default_recipes_suppresses_cwd_overlay(self, tmp_path, monkeypatch):
        """--no-default-recipes drops the auto-detected CWD overlay too."""
        import click
        import pytest

        from cvcpkg.cli._helpers import _resolve_recipes_dirs

        _write_recipe(tmp_path / "recipes", "mylib")
        monkeypatch.chdir(tmp_path)

        # With no_default and no explicit --recipes-dir there is nothing left.
        with pytest.raises(click.ClickException):
            _resolve_recipes_dirs(no_default=True)

        # An explicit overlay is honoured, but the CWD one is NOT auto-added.
        other = tmp_path / "other"
        _write_recipe(other, "other")
        dirs = _resolve_recipes_dirs((str(other),), no_default=True)
        assert dirs == [other.resolve()]
        assert (tmp_path / "recipes").resolve() not in dirs

    def test_validation_resolve_recipe_dirs_mirrors_cwd_overlay(self, tmp_path, monkeypatch):
        """cvcpkg validate uses the same CWD overlay as build (consistency)."""
        from cvcpkg.validation import resolve_recipe_dirs

        _write_recipe(tmp_path / "recipes", "mylib")
        monkeypatch.chdir(tmp_path)

        dirs = resolve_recipe_dirs()
        assert dirs[-1] == (tmp_path / "recipes").resolve()

        # And it, too, honours --no-default-recipes.
        dirs_nd = resolve_recipe_dirs(no_default=True)
        assert (tmp_path / "recipes").resolve() not in dirs_nd


# ── build command deps default ──────────────────────────────────


class TestBuildDepsDefault:
    def _build_params(self):
        import cvcpkg.cli._build  # noqa: F401  (registers the command)
        from cvcpkg.cli import cli

        build_cmd = cli.commands["build"]
        return {p.name: p for p in build_cmd.params}

    def test_no_deps_is_the_default(self):
        params = self._build_params()
        assert "with_deps" in params
        # Default is now --no-deps (with_deps=False); users opt in.
        assert params["with_deps"].default is False

    def test_incremental_default_off(self):
        params = self._build_params()
        assert "incremental" in params
        assert params["incremental"].default is False

    def test_with_deps_flag_exists(self):
        """The opt-in --with-deps flag is still wired up."""
        params = self._build_params()
        opt = params["with_deps"]
        assert "--with-deps" in opt.opts
        assert "--no-deps" in opt.secondary_opts
