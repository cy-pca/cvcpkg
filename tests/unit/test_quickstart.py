# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the package page's per-kind Quick Start.

The Quick Start used to tell every package's visitors to point CMake at the
prefix.  These pin what each kind of package gets instead -- a Python column
is imported from the prefix's interpreter, a tool goes on PATH, a source
package is built from -- and that the C/C++ flow stays as it was.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from cvcpkg.server.landing import package_detail_html
from cvcpkg.server.quickstart import profile

RECIPES = Path(__file__).resolve().parents[2] / "recipes"


def _doc(name: str) -> dict:
    return yaml.safe_load((RECIPES / name / "recipe.yaml").read_text(encoding="utf-8"))


def _quick_start(html: str) -> str:
    """The Quick Start box's content, without the rest of the page."""
    start = html.index('id="quick-start"')
    return html[start : html.index("<!-- Manual install guide -->", start)]


class TestProfileFromRecipe:
    """Classification of real recipes from this repo's recipes/ tree."""

    def test_python_column(self):
        qs = profile("attrs-cp313", _doc("attrs-cp313"))
        assert qs.kind == "python"
        assert (qs.python_cmd, qs.windows_exe, qs.abi) == ("python3.13", "python.exe", "cp313")
        assert qs.interpreter == "python313"
        assert (qs.base, qs.module) == ("attrs", "attrs")

    @pytest.mark.parametrize(
        ("name", "module"),
        [
            # The import name is what the recipe ships, not the package name.
            ("beautifulsoup4-cp313", "bs4"),
            ("pyyaml-cp313", "yaml"),
            ("pillow-cp313", "PIL"),
            ("python-dateutil-cp313", "dateutil"),
            # A namespace package keeps the levels below the namespace.
            ("protobuf-cp313", "google.protobuf"),
            ("google-cloud-storage-cp313", "google.cloud.storage"),
            # A compiled module file rather than a package directory.
            ("pybullet-cp313", "pybullet"),
        ],
    )
    def test_import_name_comes_from_the_shipped_files(self, name, module):
        assert profile(name, _doc(name)).module == module

    def test_free_threaded_column(self):
        qs = profile("numpy-cp313t", _doc("numpy-cp313t"))
        assert qs.kind == "python"
        assert (qs.python_cmd, qs.windows_exe, qs.interpreter) == (
            "python3.13t",
            "python3.13t.exe",
            "python313t",
        )

    def test_console_scripts_are_listed(self):
        assert profile("black-cp313", _doc("black-cp313")).commands == ("black", "blackd")

    def test_interpreter(self):
        qs = profile("python313", _doc("python313"))
        assert qs.kind == "interpreter"
        assert (qs.python_cmd, qs.abi) == ("python3.13", "cp313")

    def test_variant_column_keeps_its_suffix(self):
        qs = profile("torch-cp311-cuda", _doc("torch-cp311-cuda"))
        assert (qs.kind, qs.base, qs.variant, qs.abi) == ("python", "torch", "-cuda", "cp311")

    @pytest.mark.parametrize(
        ("name", "command"),
        [
            ("ninja", "ninja"),
            ("cmake", "cmake"),
            ("nodejs22", "node"),
            ("go", "go"),
            # A command may start with a digit.
            ("sevenzip", "7z"),
            # Servers ship lib/ plugins and runtime libraries beside the
            # commands that are the point of them, but no headers.
            ("mariadb-server", "mariadbd"),
            ("postgresql-server", "postgres"),
        ],
    )
    def test_tools_and_language_runtimes(self, name, command):
        qs = profile(name, _doc(name))
        assert qs.kind == "tool"
        assert command in qs.commands

    def test_python_built_tool_is_a_tool_not_a_column(self):
        # ruff carries a python: block (its build uses python311), but what it
        # ships is a binary, and its name is not a -cpNNN column.
        qs = profile("ruff", _doc("ruff"))
        assert qs.kind == "tool"
        assert qs.commands == ("ruff",)

    def test_source_package(self):
        qs = profile("attrs-src", _doc("attrs-src"))
        assert qs.kind == "source"
        assert qs.upstream_version == "26.1.0"

    def test_data_package(self):
        qs = profile("carlito-fonts", _doc("carlito-fonts"))
        assert qs.kind == "files"
        assert "share/fonts/truetype/carlito/" in qs.paths

    # openmp is tagged compiler and ships Windows DLLs under bin/, which are
    # not commands; it is a library consumed through find_package(OpenMP).
    @pytest.mark.parametrize("name", ["zlib", "boost", "hdf5", "llvm", "lua", "openmp"])
    def test_libraries_keep_the_cmake_flow(self, name):
        assert profile(name, _doc(name)).kind == "library"

    def test_every_python_column_has_an_import_name(self):
        missing = []
        for path in sorted(RECIPES.glob("*-cp3*/recipe.yaml")):
            name = path.parent.name
            qs = profile(name, _doc(name))
            if qs.kind == "python" and not qs.module:
                missing.append(name)
        assert missing == []


class TestProfileFromName:
    """No recipe on hand: the name decides, and defaults to the CMake flow."""

    def test_column(self):
        qs = profile("widget-cp312")
        assert (qs.kind, qs.python_cmd, qs.base, qs.module) == (
            "python",
            "python3.12",
            "widget",
            "",
        )

    def test_suffixed_column(self):
        qs = profile("torch-cp311-cuda")
        assert (qs.kind, qs.base, qs.variant, qs.abi) == ("python", "torch", "-cuda", "cp311")

    def test_source(self):
        assert profile("widget-src").kind == "source"

    def test_interpreter(self):
        assert profile("python312").kind == "interpreter"

    def test_anything_else(self):
        assert profile("widget").kind == "library"

    def test_python_meta_package_is_not_an_interpreter(self):
        # python / python3 own the unversioned commands; they are not a column's
        # interpreter, so the versioned flow does not apply.
        assert profile("python3").kind == "library"


class TestProfileEdgeCases:
    def _column(self, *globs: str) -> dict:
        return {
            "recipe": {"name": "widget-cp313", "tags": ["python"]},
            "python": {"interpreter": "python313", "abi": "cp313"},
            "package": {"files": [{"glob": g, "platforms": ["any"]} for g in globs]},
        }

    def test_metadata_and_wildcards_are_not_modules(self):
        doc = self._column(
            "lib/python3.13/site-packages/*.dist-info/",
            "lib/python3.13/site-packages/widget-*.dist-info/",
            "lib/python3.13/site-packages/widget/",
        )
        assert profile("widget-cp313", doc).module == "widget"

    def test_private_module_only_when_nothing_public(self):
        doc = self._column(
            "lib/python3.13/site-packages/_widget_impl/",
            "lib/python3.13/site-packages/widget.py",
        )
        assert profile("widget-cp313", doc).module == "widget"
        doc = self._column("lib/python3.13/site-packages/_widget/")
        assert profile("widget-cp313", doc).module == "_widget"

    def test_abi_comes_from_the_python_block(self):
        doc = self._column("lib/python3.13t/site-packages/widget/")
        doc["python"]["abi"] = "cp313t"
        assert profile("widget-cp313", doc).python_cmd == "python3.13t"

    def test_prefix_globs_name_no_command(self):
        doc = {
            "recipe": {"name": "widget", "tags": ["tools"]},
            "package": {"files": ["bin/widget*", "bin/fc-*", "bin/widget.exe", "bin/"]},
        }
        assert profile("widget", doc).commands == ("widget",)


class TestUntrustedRecipes:
    """Pushed recipes are any publisher's YAML; none may break the page."""

    @pytest.mark.parametrize(
        "doc",
        [
            {"recipe": "widget"},
            {"recipe": ["widget"]},
            {"recipe": {"tags": 5}},
            {"recipe": {"tags": {"a": 1}}},
            {"recipe": {"upstream_version": ["1.0"]}},
            {"package": "lib/libwidget.a"},
            {"package": ["bin/widget"]},
            {"package": {"files": 5}},
            {"package": {"files": "bin/widget"}},
            {"package": {"files": [None, 3, {"glob": 7}, {"glob": ["x"]}]}},
            {"package": {"cmake_packages": 5}},
            {"package": {"cmake_packages": ["Widget", {"name": ["Widget"]}]}},
            {"python": ["cp313"]},
            {"python": {"abi": 313, "interpreter": {"x": 1}}},
        ],
    )
    @pytest.mark.parametrize("name", ["widget", "widget-cp313", "python313", "widget-src"])
    def test_malformed_shapes_render(self, name, doc):
        assert 'id="quick-start"' in package_detail_html(name, org="acme", recipe=doc)

    def test_alias_trees_are_never_stringified(self):
        # A nested YAML alias tree stays small in memory but expands
        # exponentially through str(): 2**40 leaves here.  Rendering must
        # not touch it.
        tree: list = ["x"]
        for _ in range(40):
            tree = [tree, tree]
        doc = {
            "recipe": {"tags": tree, "upstream_version": tree},
            "python": {"abi": tree, "interpreter": tree},
            "package": {"files": ["src/widget-src/"], "cmake_packages": [{"name": tree}]},
        }
        for name in ("widget", "widget-cp313", "widget-src"):
            assert 'id="quick-start"' in package_detail_html(name, recipe=doc)


class TestQuickStartHtml:
    def test_library_flow_is_unchanged(self):
        h = package_detail_html("zlib", recipe=_doc("zlib"))
        qs = _quick_start(h)
        assert "cvcpkg install zlib --prefix /path/to/prefix" in qs
        assert "cvcpkg build zlib --prefix /path/to/prefix" in qs
        assert "Use in a downstream CMake project:" in qs
        assert "cmake -DCMAKE_PREFIX_PATH=/path/to/prefix .." in qs
        tail = re.search(r'id="manual-script" data-tail="([^"]*)"', h).group(1)
        assert tail == "# Point CMake at the prefix:\ncmake -DCMAKE_PREFIX_PATH=/opt/cvcpkg .."

    def test_python_column(self):
        h = package_detail_html("attrs-cp313", recipe=_doc("attrs-cp313"))
        qs = _quick_start(h)
        assert "cmake" not in qs.lower()
        assert "cvcpkg install attrs-cp313 --prefix ./deps" in qs
        assert 'source ./deps/bin/activate\npython3.13 -c "import attrs"' in qs
        assert "python3.13 -m venv --system-site-packages .venv" in qs
        assert ".\\deps\\python.exe" in qs
        assert "components:\n  - attrs-cp313" in qs
        # The other interpreter columns are linked by the page's JS.
        assert 'id="qs-columns" data-base="attrs" data-variant=""' in qs

    def test_variant_column_links_only_its_variant(self):
        qs = _quick_start(package_detail_html("torch-cp311-cuda", recipe=_doc("torch-cp311-cuda")))
        assert 'data-base="torch" data-variant="-cuda"' in qs

    def test_org_page_links_no_sibling_columns(self):
        # The page's recipe list carries no org, so it cannot tell which
        # columns this org publishes.
        qs = _quick_start(package_detail_html("widget-cp313", org="acme"))
        assert "qs-columns" not in qs

    def test_python_manual_install_tail(self):
        h = package_detail_html("attrs-cp313", recipe=_doc("attrs-cp313"))
        tail = re.search(r'id="manual-script" data-tail="([^"]*)"', h).group(1)
        assert "/opt/cvcpkg/bin/python3.13 -c &quot;import attrs&quot;" in tail
        manual = h[h.index('id="manual-install"') : h.index("<!-- Build Notes -->")]
        assert "cmake" not in manual.lower()

    def test_name_only_page_still_gets_the_python_flow(self):
        h = package_detail_html("widget-cp312")
        qs = _quick_start(h)
        # No import name without the recipe, and no pip-metadata check either:
        # some columns (vtk-python) ship no dist-info for pip to find.
        assert "source ./deps/bin/activate\npython3.12</pre>" in qs
        assert "pip show" not in h
        assert "cmake" not in qs.lower()
        tail = re.search(r'id="manual-script" data-tail="([^"]*)"', h).group(1)
        assert tail.endswith("/opt/cvcpkg/bin/python3.12 --version")

    def test_tool(self):
        qs = _quick_start(package_detail_html("ninja", recipe=_doc("ninja")))
        assert "cvcpkg install ninja --prefix ./deps" in qs
        assert "Activate.ps1" in qs
        assert "<code>ninja</code>" in qs
        assert "cmake" not in qs.lower()

    def test_source(self):
        qs = _quick_start(package_detail_html("attrs-src", recipe=_doc("attrs-src")))
        assert "type: package\n  package: attrs-src" in qs
        assert 'version: "==26.1.0"' in qs
        assert "src/attrs-src/" in qs

    def test_org_source_package_has_no_recipe_snippet(self):
        # source.package must match a depends.build name exactly and the schema
        # allows no org/ in it, so no snippet would build; only the install.
        qs = _quick_start(package_detail_html("attrs-src", org="acme", recipe=_doc("attrs-src")))
        assert "type: package" not in qs
        assert "cvcpkg install acme/attrs-src --prefix ./deps" in qs

    def test_interpreter(self):
        qs = _quick_start(package_detail_html("python313", recipe=_doc("python313")))
        assert "python3.13 --version" in qs
        assert "cvcpkg install &lt;package&gt;-cp313 --prefix ./deps" in qs
        # Embedding goes through CMake's FindPython3, hinted at the prefix.
        assert "find_package(Python3 COMPONENTS Development)" in qs
        assert 'Python3_ROOT_DIR="$PWD/deps"' in qs
        # No search link: a substring search for -cp313 also returns -cp313t
        # columns and -src packages.
        assert "/search" not in qs

    def test_free_threaded_interpreter_has_no_embed_step(self):
        qs = _quick_start(package_detail_html("python313t", recipe=_doc("python313t")))
        assert "python3.13t --version" in qs
        assert "find_package" not in qs

    def test_org_package_installs_by_qualified_name(self):
        qs = _quick_start(package_detail_html("widget-cp313", org="acme"))
        assert "cvcpkg install acme/widget-cp313 --prefix ./deps" in qs

    def test_recipe_values_are_escaped(self):
        doc = {
            "recipe": {"name": "widget", "tags": ["data"]},
            "package": {"files": ["share/<script>alert(1)</script>/"]},
        }
        qs = _quick_start(package_detail_html("widget", recipe=doc))
        assert "<script>alert" not in qs
        assert "&lt;script&gt;" in qs

    def test_canonical_link_is_escaped(self):
        h = package_detail_html('x"><img src=x onerror=alert(1)>')
        assert "<img src=x" not in h

    def test_detail_page_defines_what_its_script_calls(self):
        # renderDeps builds the manual-install script with detectVisitorPlatform;
        # it was defined only on the landing page, so the call threw and the
        # Manual Install section never appeared.
        assert "function detectVisitorPlatform" in package_detail_html("zlib")


fastapi = pytest.importorskip("fastapi", reason="server extras not installed")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.server.app import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A server whose recipe tree holds one Python column."""
    root = tmp_path / "recipes"
    (root / "widget-cp313").mkdir(parents=True)
    (root / "widget-cp313" / "recipe.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "recipe": {"name": "widget-cp313", "upstream_version": "1.0", "cvc_revision": 1},
                "python": {"interpreter": "python313", "abi": "cp313"},
                "package": {"files": ["lib/python3.13/site-packages/gizmo/"]},
            }
        ),
        encoding="utf-8",
    )
    import cvcpkg.builder as builder_mod

    monkeypatch.setattr(builder_mod, "find_recipes_dir", lambda *a, **k: root)
    state = tmp_path / "state"
    state.mkdir()
    with TestClient(create_app(state_dir=state)) as c:
        yield c


class TestPackagePageRoute:
    def test_quick_start_reads_the_recipe(self, client):
        # The import name (gizmo) is only in the recipe, not in the package name.
        resp = client.get("/package/widget-cp313")
        assert resp.status_code == 200
        assert 'python3.13 -c "import gizmo"' in _quick_start(resp.text)

    def test_unknown_package_falls_back_to_the_name(self, client):
        resp = client.get("/package/nosuch-cp312")
        assert resp.status_code == 200
        assert "source ./deps/bin/activate\npython3.12</pre>" in _quick_start(resp.text)

    @pytest.mark.parametrize(
        "path",
        [
            # A name the recipe namespace cannot hold, here one that would
            # break out of the canonical link's attribute.
            "/package/x%22%3E%3Cimg%20src=x%20onerror=alert(1)%3E",
            # A newline-split name drove the column pattern quadratic.
            "/package/" + "-cp31" * 200 + "%0Ax",
        ],
    )
    def test_unscoped_route_rejects_bad_names(self, client, path):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 404
        assert "<img" not in resp.text

    def test_deeply_nested_recipe_falls_back_to_the_name(self, client, tmp_path):
        # PyYAML's composer recurses, so this raises RecursionError, which is
        # not a YAMLError; the page must still render.
        root = tmp_path / "recipes"
        (root / "deep-cp313").mkdir()
        depth = 5000
        (root / "deep-cp313" / "recipe.yaml").write_text(
            "recipe:\n  name: deep-cp313\nother: " + "[" * depth + "]" * depth + "\n",
            encoding="utf-8",
        )
        resp = client.get("/package/deep-cp313")
        assert resp.status_code == 200
        assert "source ./deps/bin/activate\npython3.13</pre>" in _quick_start(resp.text)
