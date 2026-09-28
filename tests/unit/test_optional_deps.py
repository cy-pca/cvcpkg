"""Tests for the core-vs-extras packaging split (:mod:`cvcpkg.optional`).

Two things are guarded here, and they are the whole point of the split:

1. The *metadata* — ``pip install cvcpkg`` must keep resolving to ``click`` +
   ``PyYAML`` and nothing else.  A new mandatory dependency is exactly the
   regression that made cvcpkg uninstallable on Haiku, and it is invisible in
   code review because nothing imports it on the client path.
2. The *failure mode* — reaching a command whose extra is not installed must
   name the extra, not raise ModuleNotFoundError.
"""

from __future__ import annotations

import builtins
import re
from pathlib import Path

import click
import pytest

from cvcpkg.errors import CvcpkgError
from cvcpkg.optional import (
    MissingDependencyError,
    require_cryptography,
    require_httpx,
    require_jsonschema,
    require_pydantic,
    require_sqlalchemy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# ── 1. Packaging metadata ───────────────────────────────────────


class TestCoreDependencies:
    def test_only_click_and_pyyaml_are_mandatory(self):
        """Everything except click + PyYAML must be optional = true."""
        deps = _load_pyproject()["tool"]["poetry"]["dependencies"]
        mandatory = {
            name
            for name, spec in deps.items()
            if name != "python" and not (isinstance(spec, dict) and spec.get("optional"))
        }
        assert mandatory == {"click", "PyYAML"}

    def test_every_extra_names_a_declared_dependency(self):
        poetry = _load_pyproject()["tool"]["poetry"]
        deps = poetry["dependencies"]
        for extra, members in poetry["extras"].items():
            for dist in members:
                assert dist in deps, f"extra {extra!r} names undeclared dependency {dist!r}"

    def test_all_extra_covers_every_optional_dependency(self):
        """[all] is the documented one-word migration; it must miss nothing."""
        poetry = _load_pyproject()["tool"]["poetry"]
        optional = {
            name
            for name, spec in poetry["dependencies"].items()
            if isinstance(spec, dict) and spec.get("optional")
        }
        assert optional - set(poetry["extras"]["all"]) == set()

    def test_previously_mandatory_deps_are_reachable_from_an_extra(self):
        """The four demoted in 2.0.2 each keep a role-named home."""
        extras = _load_pyproject()["tool"]["poetry"]["extras"]
        assert "httpx" in extras["remote"]
        assert "httpx" in extras["publish"]
        assert "httpx" in extras["builder"]
        assert "cryptography" in extras["signing"]
        assert {"sqlalchemy", "greenlet"} <= set(extras["server"])

    def test_validate_extra_carries_jsonschema(self):
        """`cvcpkg validate` is a recipe-maintainer role, not part of the client."""
        extras = _load_pyproject()["tool"]["poetry"]["extras"]
        assert extras["validate"] == ["jsonschema"]

    def test_fallback_version_matches_pyproject(self):
        """__init__'s no-metadata fallback must not drift from the real version."""
        from cvcpkg import _FALLBACK_VERSION

        assert _load_pyproject()["tool"]["poetry"]["version"] == _FALLBACK_VERSION


# ── 2. The missing-extra failure mode ───────────────────────────


@pytest.fixture
def hide_module(monkeypatch):
    """Make ``import <name>`` raise ImportError, as on a core install."""

    def _hide(name: str) -> None:
        real_import = builtins.__import__

        def fake_import(mod, *args, **kwargs):
            if mod == name or mod.startswith(name + "."):
                raise ImportError(f"No module named {name!r}")
            return real_import(mod, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

    return _hide


class TestMissingDependencyError:
    def test_is_both_a_cvcpkg_error_and_a_click_exception(self):
        """CvcpkgError for programmatic callers; ClickException for one clean line."""
        exc = MissingDependencyError("httpx", "to do the thing", "remote")
        assert isinstance(exc, CvcpkgError)
        assert isinstance(exc, click.ClickException)
        assert exc.message == (
            "httpx is required to do the thing. Install it with: pip install 'cvcpkg[remote]'"
        )

    @pytest.mark.parametrize("extra", ["remote", "publish", "builder"])
    def test_require_httpx_names_the_callers_extra(self, hide_module, extra):
        hide_module("httpx")
        with pytest.raises(MissingDependencyError) as ei:
            require_httpx(extra)
        assert f"pip install 'cvcpkg[{extra}]'" in str(ei.value)

    def test_require_cryptography_points_at_the_signing_extra(self, hide_module):
        hide_module("cryptography")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[signing\]"):
            require_cryptography()

    def test_require_sqlalchemy_points_at_the_server_extra(self, hide_module):
        hide_module("sqlalchemy")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[server\]"):
            require_sqlalchemy()

    def test_require_pydantic_points_at_the_server_extra(self, hide_module):
        """`cvcpkg-server bootstrap|token create|audit …` reach pydantic, not the db."""
        hide_module("pydantic")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[server\]"):
            require_pydantic()

    def test_require_jsonschema_points_at_the_validate_extra(self, hide_module):
        hide_module("jsonschema")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[validate\]"):
            require_jsonschema()

    def test_require_httpx_returns_the_module_when_present(self):
        httpx = pytest.importorskip("httpx")
        assert require_httpx() is httpx

    def test_require_jsonschema_returns_the_module_when_present(self):
        jsonschema = pytest.importorskip("jsonschema")
        assert require_jsonschema() is jsonschema

    def test_signing_reports_the_extra_instead_of_a_traceback(self, hide_module):
        """cvcpkg.signing's guards are what `cvcpkg key generate` hits."""
        from cvcpkg import signing

        hide_module("cryptography")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[signing\]"):
            signing.generate_keypair("demo", keys_dir=Path("/nonexistent"))

    def test_validation_reports_the_extra_instead_of_a_traceback(self, hide_module, tmp_path):
        """`cvcpkg init` ends by telling you to run `cvcpkg validate recipes/NAME`.

        Both validators are guarded: the recipe one is what `cvcpkg validate`
        reaches for a recipe target, the components one what it reaches for
        ``packaging/components.yaml``.
        """
        from cvcpkg import validation

        recipe_dir = tmp_path / "demo"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "recipe:\n  name: demo\n  upstream_version: '1.0'\n  cvc_revision: 1\n",
            encoding="utf-8",
        )
        components = tmp_path / "components.yaml"
        components.write_text("components: {}\n", encoding="utf-8")

        hide_module("jsonschema")
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[validate\]"):
            validation.validate_recipe_dir(recipe_dir)
        with pytest.raises(MissingDependencyError, match=r"cvcpkg\[validate\]"):
            validation.validate_components_file(components)


# ── 3. No client-path command may import an extra eagerly ───────


class TestLazyImports:
    @pytest.mark.parametrize(
        "dist", ["httpx", "cryptography", "sqlalchemy", "greenlet", "jsonschema"]
    )
    def test_no_module_level_import_of_an_extra_in_the_client(self, dist):
        """The guards only help if nothing imports these at module scope.

        ``cvcpkg.server`` is exempt: it *is* the [server] extra.
        """
        # ``^`` under MULTILINE anchors at column 0, which for an import is
        # exactly "module scope" — a guarded import is inside a function body
        # and therefore indented.
        pattern = re.compile(rf"^(?:import {dist}\b|from {dist}[. ])", re.MULTILINE)
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "src" / "cvcpkg").rglob("*.py")
            if "/server/" not in path.relative_to(REPO_ROOT).as_posix()
            and "/migrations/" not in path.relative_to(REPO_ROOT).as_posix()
            and pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []
