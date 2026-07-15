"""Tests verifying CLI submodule import integrity after the cli/ package refactor.

Regression suite for the _publish_to_server NameError bug: the monolithic
cli.py was split into submodules, but _builder.py called _publish_to_server
without importing it.  Tests went through __init__.py re-exports and never
caught the missing import.

These tests ensure:
1. Every CLI submodule can be imported independently (not via __init__.py)
2. Cross-module references in function bodies actually resolve
3. No submodule accidentally relies on __init__.py's namespace for names
"""

from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

# ── Constants ───────────────────────────────────────────────────

CLI_PKG = "cvcpkg.cli"
CLI_DIR = Path(__file__).resolve().parents[2] / "src" / "cvcpkg" / "cli"

# Submodules that should exist in the cli/ package
EXPECTED_SUBMODULES = [
    "_helpers",
    "_build",
    "_builder",
    "_builds",
    "_cache",
    "_catalog",
    "_doctor",
    "_init",
    "_install",
    "_publish",
    "_recipe",
    "_search",
    "_server",
    "_signing",
    "_telemetry",
    "_webhook",
]

# Python builtins — use the builtins module for reliability
import builtins as _builtins_mod

_BUILTINS = set(dir(_builtins_mod))


# ── 1. Independent import smoke tests ──────────────────────────


class TestSubmoduleImports:
    """Each CLI submodule must be importable on its own without NameError."""

    @pytest.fixture(autouse=True)
    def _isolate_imports(self):
        """Remove cli package from sys.modules to force fresh imports."""
        # Snapshot modules so we can restore after each test
        snapshot = {k: v for k, v in sys.modules.items() if k.startswith(CLI_PKG)}
        yield
        # Restore original module state
        for k in list(sys.modules):
            if k.startswith(CLI_PKG) and k not in snapshot:
                del sys.modules[k]
        sys.modules.update(snapshot)

    @pytest.mark.parametrize("submodule", EXPECTED_SUBMODULES)
    def test_submodule_imports_independently(self, submodule):
        """Import cvcpkg.cli.<submodule> directly — must not raise."""
        mod = importlib.import_module(f"{CLI_PKG}.{submodule}")
        assert isinstance(mod, types.ModuleType)

    def test_all_expected_submodules_exist(self):
        """Every submodule listed in EXPECTED_SUBMODULES has a .py file."""
        for name in EXPECTED_SUBMODULES:
            path = CLI_DIR / f"{name}.py"
            assert path.is_file(), f"Missing submodule file: {path}"

    def test_no_unexpected_submodules(self):
        """If a new submodule is added, it should be in EXPECTED_SUBMODULES."""
        actual = set()
        for p in CLI_DIR.glob("_*.py"):
            if p.stem == "__init__":
                continue
            actual.add(p.stem)
        expected = set(EXPECTED_SUBMODULES)
        extra = actual - expected
        assert not extra, (
            f"New CLI submodule(s) {extra} not in EXPECTED_SUBMODULES — "
            f"add them to the list so import tests cover them"
        )


# ── 2. AST-based cross-module reference verification ───────────


def _collect_all_defined_names(tree: ast.Module) -> set[str]:
    """Collect ALL names defined anywhere in the module (any nesting level).

    This handles closures: a function defined inside another function
    is accessible from sibling nested functions.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
            # Also add parameter names
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                names.add(arg.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.For | ast.AsyncFor):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _find_bare_name_calls(func_node: ast.FunctionDef) -> list[tuple[str, int]]:
    """Find all bare Name references used as function calls in a function body.

    Returns list of (name, lineno) for calls like ``_publish_to_server(...)``
    but NOT ``self.method()`` or ``module.func()``.
    """
    calls: list[tuple[str, int]] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append((node.func.id, node.lineno))
    return calls


class TestCrossModuleReferences:
    """Verify that function-call references in each submodule resolve."""

    @pytest.mark.parametrize("submodule", EXPECTED_SUBMODULES)
    def test_function_calls_resolve(self, submodule):
        """Every bare function call in each CLI submodule must be defined,
        imported, or a builtin — not relying on __init__.py namespace."""
        source_path = CLI_DIR / f"{submodule}.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))

        # Collect ALL names defined anywhere in the module (handles closures)
        all_names = _collect_all_defined_names(tree)

        unresolved: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call_name, lineno in _find_bare_name_calls(node):
                if call_name not in all_names and call_name not in _BUILTINS:
                    unresolved.append(f"{submodule}.py:{lineno} — {call_name}()")

        assert not unresolved, (
            f"Unresolved function calls in {submodule}.py "
            f"(not imported or defined locally):\n" + "\n".join(f"  {u}" for u in unresolved)
        )


# ── 3. Specific regression: _builder → _publish_to_server ──────


class TestBuilderPublishImport:
    """Regression test for the _publish_to_server NameError."""

    def test_publish_to_server_in_builder_namespace(self):
        """_builder module must have _publish_to_server in its own namespace."""
        from cvcpkg.cli import _builder

        assert hasattr(_builder, "_publish_to_server"), (
            "_builder.py does not have _publish_to_server in its namespace. "
            "It must import it from _publish, not rely on __init__.py re-exports."
        )

    def test_api_request_in_builder_namespace(self):
        """_builder module must have _api_request in its own namespace."""
        from cvcpkg.cli import _builder

        assert hasattr(
            _builder, "_api_request"
        ), "_builder.py does not have _api_request in its namespace."

    def test_builder_cross_imports_match_source(self):
        """Verify _builder.py's import statements include all cross-module refs."""
        source = (CLI_DIR / "_builder.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Collect all ImportFrom that reference sibling modules
        cross_imports: dict[str, set[str]] = {}
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "cvcpkg.cli." in node.module:
                sibling = node.module.split(".")[-1]
                cross_imports[sibling] = {alias.name for alias in node.names}

        # _publish_to_server must be imported from _publish
        assert "_publish" in cross_imports, "_builder.py missing import from _publish"
        assert (
            "_publish_to_server" in cross_imports["_publish"]
        ), "_builder.py does not import _publish_to_server from _publish"


# ── 4. __init__.py re-exports coverage ──────────────────────────


class TestInitReexports:
    """Ensure __init__.py imports all submodules (so commands register)."""

    def test_all_submodules_imported_in_init(self):
        """__init__.py must import every submodule to register CLI commands.

        _helpers is a utility module (no commands) so it may be imported
        via ``from cvcpkg.cli._helpers import ...`` rather than as a
        bare submodule import.
        """
        source = (CLI_DIR / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == CLI_PKG:
                    for alias in node.names:
                        imported_modules.add(alias.name)
                elif node.module and node.module.startswith(f"{CLI_PKG}."):
                    # e.g. from cvcpkg.cli._helpers import ...
                    imported_modules.add(node.module.split(".")[-1])

        for submodule in EXPECTED_SUBMODULES:
            assert (
                submodule in imported_modules
            ), f"__init__.py does not import {submodule} — its CLI commands won't be registered"
