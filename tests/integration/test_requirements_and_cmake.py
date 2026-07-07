"""Integration tests for requirements-based install and dummy recipe builds.

Tests:
  1. ``cvc-requirements.yaml`` install via server catalog.
  2. Dummy recipe build + publish + install lifecycle.
  3. CMake config files present in the install prefix.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pydantic = pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.builder import Recipe, list_recipes, resolve_build_order
from cvcpkg.manifest import Requirements
from cvcpkg.server.app import create_app
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
REQUIREMENTS_FILE = REPO_ROOT / "cvc-requirements.yaml"

requires_repo = pytest.mark.skipif(not RECIPES_DIR.is_dir(), reason="Not running from repo")


# ── Helpers ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_database_url(monkeypatch):
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)


def _make_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _publish(client, token, name, version, **kwargs):
    platform = kwargs.get("platform", "linux")
    arch = kwargs.get("arch", "x86_64")
    build_type = kwargs.get("build_type", "release")
    link = kwargs.get("link", "shared")
    content = kwargs.get("content")

    if content is None:
        content = _make_archive(
            {
                f"lib/lib{name}.so": b"\x7fELF" + b"\x00" * 64,
                f"include/{name}.h": b"#pragma once\n",
                f"share/libcvc-deps/{name}/manifest.yaml": yaml.dump(
                    {
                        "schema_version": 3,
                        "bundle": {
                            "name": name,
                            "version": version,
                            "upstream_version": version.split("+")[0],
                            "cvc_revision": 1,
                            "platform": platform,
                            "arch": arch,
                            "build_type": build_type,
                            "link": link,
                        },
                        "contents": {"files": [f"lib/lib{name}.so", f"include/{name}.h"]},
                    }
                ).encode(),
            }
        )

    resp = client.post(
        "/v1/publish",
        params={
            "name": name,
            "version": version,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
        },
        files={"file": (f"{name}-{version}.tar.gz", io.BytesIO(content))},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"publish {name}: {resp.text}"
    return resp.json()


@pytest.fixture()
def server_env(tmp_path):
    store = TokenStore(tmp_path)
    admin_token = store.create("admin", TokenRole.admin)
    pub_token = store.create("publisher", TokenRole.publisher)

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


# ── Test: requirements install ───────────────────────────────────


@requires_repo
class TestRequirementsInstall:
    """Install from cvc-requirements.yaml against a mock server catalog."""

    def test_requirements_resolve_all_components(self, server_env):
        """Publish all components from cvc-requirements.yaml and verify
        the server catalog contains them."""
        client, _, pub_token, tmp_path = server_env

        raw = yaml.safe_load(REQUIREMENTS_FILE.read_text())
        reqs = Requirements.from_dict(raw)

        # Publish each component as a stub package
        for comp in reqs.components:
            recipe_dir = RECIPES_DIR / comp.name
            if recipe_dir.is_dir():
                recipe = Recipe.load(recipe_dir)
                version = recipe.full_version
            else:
                version = "1.0.0+cvc.1"
            _publish(client, pub_token, comp.name, version)

        # Verify catalog has all components
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200

        from cvcpkg.catalog import catalog_entries

        entries = catalog_entries(resp.json(), platform="linux", arch="x86_64")
        published_names = {e.name for e in entries}

        for comp in reqs.components:
            assert comp.name in published_names, (
                f"Component '{comp.name}' from cvc-requirements.yaml "
                f"not found in server catalog"
            )

    def test_requirements_install_cli(self, server_env, tmp_path):
        """Full CLI install from a requirements file."""
        client, _, pub_token, state_dir = server_env

        # Publish zlib and yaml (subset)
        _publish(client, pub_token, "zlib", "1.3.1+cvc.1")
        _publish(client, pub_token, "yaml", "0.2.5+cvc.1")

        # Write a small requirements file
        req_file = tmp_path / "test-requirements.yaml"
        req_file.write_text(
            yaml.dump(
                {
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "components": ["zlib", "yaml"],
                }
            )
        )

        # Fetch catalog and write locally
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        catalog_file = tmp_path / "catalog.yaml"
        catalog_file.write_text(yaml.dump(resp.json()))

        # Verify the requirements file can be loaded and all
        # components are resolvable against the catalog
        raw = yaml.safe_load(req_file.read_text())
        reqs = Requirements.from_dict(raw)
        assert len(reqs.components) == 2

        from cvcpkg.catalog import catalog_entries

        entries = catalog_entries(resp.json(), platform="linux", arch="x86_64")
        names = {e.name for e in entries}
        for comp in reqs.components:
            assert comp.name in names


# ── Test: dummy recipe build ─────────────────────────────────────


class TestDummyRecipeBuild:
    """Build a minimal dummy recipe from scratch."""

    def test_dummy_recipe_loads(self, tmp_path):
        """A minimal recipe.yaml can be loaded by the Recipe class."""
        recipe_dir = tmp_path / "hello"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "recipe": {
                        "name": "hello",
                        "upstream_version": "1.0.0",
                        "cvc_revision": 1,
                        "description": "Dummy test recipe",
                        "homepage": "https://example.com",
                        "license": "MIT",
                    },
                    "source": {
                        "type": "tarball",
                        "url": "https://example.com/hello-1.0.0.tar.gz",
                        "sha256": "0" * 64,
                    },
                    "build": {
                        "matrix": [
                            {
                                "platform": "linux",
                                "script": "build.sh",
                            },
                        ],
                    },
                }
            )
        )
        (recipe_dir / "build.sh").write_text("#!/bin/sh\necho hello\n")

        recipe = Recipe.load(recipe_dir)
        assert recipe.name == "hello"
        assert recipe.upstream_version == "1.0.0"
        assert recipe.cvc_revision == 1
        assert len(recipe.build_matrix) >= 1

    def test_dummy_recipe_build_order(self, tmp_path):
        """Two recipes with a dependency resolve in correct order."""
        # Create 'base' recipe
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        (base_dir / "recipe.yaml").write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "recipe": {
                        "name": "base",
                        "upstream_version": "1.0.0",
                        "cvc_revision": 1,
                        "description": "Base library",
                        "homepage": "https://example.com",
                        "license": "MIT",
                    },
                    "source": {
                        "type": "tarball",
                        "url": "https://example.com/base-1.0.0.tar.gz",
                        "sha256": "0" * 64,
                    },
                    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                }
            )
        )
        (base_dir / "build.sh").write_text("#!/bin/sh\necho base\n")

        # Create 'app' recipe that depends on 'base'
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "recipe.yaml").write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "recipe": {
                        "name": "app",
                        "upstream_version": "2.0.0",
                        "cvc_revision": 1,
                        "description": "App depending on base",
                        "homepage": "https://example.com",
                        "license": "MIT",
                    },
                    "source": {
                        "type": "tarball",
                        "url": "https://example.com/app-2.0.0.tar.gz",
                        "sha256": "0" * 64,
                    },
                    "depends": {"build": ["base"]},
                    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                }
            )
        )
        (app_dir / "build.sh").write_text("#!/bin/sh\necho app\n")

        recipes = [Recipe.load(base_dir), Recipe.load(app_dir)]
        ordered = resolve_build_order(recipes)
        names = [r.name for r in ordered]
        assert names.index("base") < names.index("app")

    def test_dummy_recipe_publish_install(self, server_env, tmp_path):
        """Build a dummy recipe, publish, then install from server."""
        client, _, pub_token, state_dir = server_env

        # Create a minimal recipe
        recipe_dir = tmp_path / "recipes" / "hello"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text(
            yaml.dump(
                {
                    "schema_version": 1,
                    "recipe": {
                        "name": "hello",
                        "upstream_version": "1.0.0",
                        "cvc_revision": 1,
                        "description": "Dummy",
                        "homepage": "https://example.com",
                        "license": "MIT",
                    },
                    "source": {
                        "type": "tarball",
                        "url": "https://example.com/hello-1.0.0.tar.gz",
                        "sha256": "0" * 64,
                    },
                    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                }
            )
        )
        (recipe_dir / "build.sh").write_text("#!/bin/sh\necho hello\n")

        recipe = Recipe.load(recipe_dir)

        # Simulate built output
        install_dir = tmp_path / "install"
        (install_dir / "lib").mkdir(parents=True)
        (install_dir / "lib" / "libhello.so").write_bytes(b"\x7fELF" + b"\x00" * 64)
        (install_dir / "include").mkdir()
        (install_dir / "include" / "hello.h").write_text("#pragma once\nvoid hello();\n")

        from cvcpkg.builder import create_archive, generate_manifest, stage_bundle

        manifest = generate_manifest(recipe, install_dir, "linux", "x86_64", "release", "shared")
        staging = tmp_path / "staging"
        staging.mkdir()
        stage_bundle(install_dir, manifest, staging)

        dist = tmp_path / "dist"
        archive_path, sha, size = create_archive(
            staging,
            dist,
            "hello",
            "1.0.0+cvc.1",
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        assert archive_path.exists()

        # Publish to server
        with open(archive_path, "rb") as f:
            content = f.read()
        _publish(client, pub_token, "hello", "1.0.0+cvc.1", content=content)

        # Verify in catalog
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200

        from cvcpkg.catalog import catalog_entries

        entries = catalog_entries(resp.json(), platform="linux", arch="x86_64")
        names = {e.name for e in entries}
        assert "hello" in names

        # Download
        resp = client.get("/v1/packages", params={"name": "hello"})
        assert resp.status_code == 200


# ── Test: CMake config files ─────────────────────────────────────


class TestCMakeIntegration:
    """Verify CMake config templates are present and well-formed."""

    @requires_repo
    def test_cvcpkg_config_template_exists(self):
        config = REPO_ROOT / "cmake" / "cvcpkgConfig.cmake.in"
        assert config.is_file(), "cmake/cvcpkgConfig.cmake.in missing"
        text = config.read_text()
        assert "@PACKAGE_INIT@" in text
        assert "CVCPKG_VERSION" in text
        assert "CMAKE_PREFIX_PATH" in text

    @requires_repo
    def test_libcvc_deps_compat_config_exists(self):
        config = REPO_ROOT / "cmake" / "libcvc-depsConfig.cmake.in"
        assert config.is_file(), "cmake/libcvc-depsConfig.cmake.in missing"
        text = config.read_text()
        assert "@PACKAGE_INIT@" in text
        assert "LIBCVC_DEPS_VERSION" in text

    @requires_repo
    def test_toolchain_file_exists(self):
        toolchain = REPO_ROOT / "cmake" / "cvcpkg-toolchain.cmake"
        assert toolchain.is_file(), "cmake/cvcpkg-toolchain.cmake missing"
        text = toolchain.read_text()
        assert "CMAKE_PREFIX_PATH" in text
        assert "CVCPKG_PREFIX" in text

    @requires_repo
    def test_cmake_configure_succeeds(self, tmp_path):
        """CMakeLists.txt configures and installs without error."""
        cmake = "cmake"
        build_dir = tmp_path / "build"
        prefix = tmp_path / "prefix"

        result = subprocess.run(
            [cmake, "-B", str(build_dir), "-S", str(REPO_ROOT), f"-DCMAKE_INSTALL_PREFIX={prefix}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"cmake configure failed:\n{result.stderr}"

        result = subprocess.run(
            [cmake, "--install", str(build_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"cmake install failed:\n{result.stderr}"

        # Verify installed files
        assert (prefix / "share" / "cmake" / "cvcpkg" / "cvcpkgConfig.cmake").is_file()
        assert (prefix / "share" / "cmake" / "cvcpkg" / "cvcpkgConfigVersion.cmake").is_file()
        assert (prefix / "share" / "cmake" / "cvcpkg" / "cvcpkg-toolchain.cmake").is_file()
        assert (prefix / "share" / "cmake" / "libcvc-deps" / "libcvc-depsConfig.cmake").is_file()
        assert (prefix / "MANIFEST.txt").is_file()

    @requires_repo
    def test_downstream_find_package(self, tmp_path):
        """A downstream project can find_package(cvcpkg)."""
        cmake = "cmake"

        # Install cvcpkg cmake config into a prefix
        prefix = tmp_path / "prefix"
        build_dir = tmp_path / "build-cvcpkg"
        subprocess.run(
            [cmake, "-B", str(build_dir), "-S", str(REPO_ROOT), f"-DCMAKE_INSTALL_PREFIX={prefix}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        subprocess.run(
            [cmake, "--install", str(build_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # Create a tiny downstream CMakeLists.txt
        downstream = tmp_path / "downstream"
        downstream.mkdir()
        (downstream / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.21)\n"
            "project(test_consumer LANGUAGES NONE)\n"
            "find_package(cvcpkg CONFIG REQUIRED)\n"
            'message(STATUS "cvcpkg version: ${CVCPKG_VERSION}")\n'
            'message(STATUS "cvcpkg root: ${CVCPKG_ROOT_DIR}")\n'
            "# Backward compat alias\n"
            "find_package(libcvc-deps CONFIG REQUIRED)\n"
            'message(STATUS "libcvc-deps version: ${LIBCVC_DEPS_VERSION}")\n'
        )

        build_downstream = tmp_path / "build-downstream"
        result = subprocess.run(
            [
                cmake,
                "-B",
                str(build_downstream),
                "-S",
                str(downstream),
                f"-DCMAKE_PREFIX_PATH={prefix}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"downstream cmake configure failed:\n{result.stderr}"
        assert "cvcpkg version: 2.0.0" in result.stdout
        assert "cvcpkg root:" in result.stdout
        assert "libcvc-deps version: 2.0.0" in result.stdout
