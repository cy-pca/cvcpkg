"""Integration tests for the local build cache.

These tests exercise the build cache end-to-end via the CLI and
builder, using synthetic recipes that produce real (but tiny) outputs.
No server or Docker is required.
"""

from __future__ import annotations

import hashlib
import os
import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from cvcpkg.build_cache import BuildCache
from cvcpkg.builder import build_all, chain_hash, list_recipes
from cvcpkg.cli import cli

# ── Helpers ─────────────────────────────────────────────────────


def _create_vendored_recipe(
    recipes_dir: Path,
    name: str,
    deps: list[str] | None = None,
) -> Path:
    """Create a minimal vendored recipe that writes a marker file.

    The recipe's build.sh creates ``lib/lib<name>.txt`` in the install
    directory so we can verify cache restore produces the same layout.

    The vendored source is placed at ``<repo_root>/third-party/<name>``
    since ``_resolve_vendored`` resolves relative to
    ``recipe_dir.parent.parent`` (i.e. the repo root).
    """
    recipe_dir = recipes_dir / name
    recipe_dir.mkdir(parents=True, exist_ok=True)

    # Create vendored source at repo_root / third-party / name.
    repo_root = recipes_dir.parent
    src = repo_root / "third-party" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "README").write_text(f"source for {name}")

    recipe = {
        "schema_version": 1,
        "recipe": {
            "name": name,
            "upstream_version": "1.0.0",
            "cvc_revision": 1,
        },
        "source": {"type": "vendored", "path": f"third-party/{name}"},
        "patches": [],
        "build": {
            "matrix": [
                {"platform": "linux", "script": "build.sh"},
            ],
        },
        "package": {
            "files": ["lib/*"],
            "cmake_packages": [],
        },
    }
    if deps:
        recipe["depends"] = {"build": deps}

    (recipe_dir / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    (recipe_dir / "build.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -e
            mkdir -p "$CVC_INSTALL_DIR/lib"
            echo "built-{name}" > "$CVC_INSTALL_DIR/lib/lib{name}.txt"
        """
        )
    )
    os.chmod(recipe_dir / "build.sh", 0o755)
    return recipe_dir


# ── Build cache CLI integration ─────────────────────────────────


class TestBuildCacheCLI:
    """Test the ``cvcpkg cache`` subcommand group end-to-end."""

    def test_cache_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))
        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "list"])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_cache_purge_all_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))
        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "purge", "--all"])
        assert result.exit_code == 0
        assert "0" in result.output

    def test_cache_list_after_store(self, tmp_path, monkeypatch):
        """Store an entry via the API and verify `cvcpkg cache list` shows it."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        bc = BuildCache(cache_dir)
        install = tmp_path / "install"
        install.mkdir()
        (install / "lib").mkdir()
        (install / "lib" / "libtest.so").write_text("fake")

        ch = hashlib.sha256(b"test-content").hexdigest()
        bc.store(install, "testpkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "list"])
        assert result.exit_code == 0
        assert "testpkg" in result.output
        assert "1.0" in result.output

    def test_cache_purge_removes_entries(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        bc = BuildCache(cache_dir)
        install = tmp_path / "install"
        install.mkdir()
        (install / "lib").mkdir()
        (install / "lib" / "libtest.so").write_text("fake")

        ch = hashlib.sha256(b"test-content").hexdigest()
        bc.store(install, "testpkg", "1.0", ch, "linux", "x86_64", "release", "shared")
        assert len(bc.list_entries()) == 1

        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "purge", "--all"])
        assert result.exit_code == 0
        assert bc.list_entries() == []


# ── build_all with cache ────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Runs real builds — too slow for CI",
)
class TestBuildAllWithCache:
    """Exercise build_all() with cache integration using synthetic recipes."""

    def test_build_populates_cache(self, tmp_path, monkeypatch):
        """First build_all stores results in the cache."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "mypkg")

        prefix = tmp_path / "prefix"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        bc = BuildCache(cache_dir)
        entries = bc.list_entries()
        assert len(entries) == 1
        assert entries[0].name == "mypkg"

    def test_rebuild_uses_cache(self, tmp_path, monkeypatch, capsys):
        """Second build_all should use cached artifacts."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "mypkg")

        # First build.
        prefix1 = tmp_path / "prefix1"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )

        # Second build into a different prefix.
        prefix2 = tmp_path / "prefix2"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix2,
            per_component=True,
        )

        captured = capsys.readouterr()
        assert "cache hit" in captured.out
        # Verify the restored files.
        assert (prefix2 / "lib" / "libmypkg.txt").is_file()

    def test_recipe_change_invalidates_cache(self, tmp_path, monkeypatch, capsys):
        """Modifying a recipe should cause a cache miss."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "mypkg")

        # First build.
        prefix1 = tmp_path / "prefix1"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )

        # Modify the recipe.
        recipe_yaml = recipes_dir / "mypkg" / "recipe.yaml"
        data = yaml.safe_load(recipe_yaml.read_text())
        data["recipe"]["cvc_revision"] = 2
        recipe_yaml.write_text(yaml.dump(data, default_flow_style=False))

        # Second build — should miss cache.
        prefix2 = tmp_path / "prefix2"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix2,
            per_component=True,
        )

        captured = capsys.readouterr()
        # Should NOT contain "cache hit" for the second build.
        lines = captured.out.strip().split("\n")
        second_build_lines = []
        found_second = False
        for line in lines:
            if "mypkg" in line and found_second:
                second_build_lines.append(line)
            elif "mypkg" in line:
                found_second = True

        # Two entries in cache now (old and new hash).
        bc = BuildCache(cache_dir)
        assert len(bc.list_entries()) == 2

    def test_no_cache_flag_bypasses(self, tmp_path, monkeypatch):
        """--no-cache should prevent cache lookup and storage."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "mypkg")

        prefix = tmp_path / "prefix"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
            no_cache=True,
        )

        bc = BuildCache(cache_dir)
        assert bc.list_entries() == []

    def test_force_clean_skips_lookup_but_stores(self, tmp_path, monkeypatch, capsys):
        """--force-clean should skip lookup but store results."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "mypkg")

        # First build to populate cache.
        prefix1 = tmp_path / "prefix1"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )

        # Force-clean rebuild — should NOT use cache.
        prefix2 = tmp_path / "prefix2"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix2,
            per_component=True,
            force_clean=True,
        )

        captured = capsys.readouterr()
        assert "cache hit" not in captured.out

    def test_dep_change_cascades(self, tmp_path, monkeypatch):
        """Changing a dep recipe invalidates downstream cache entries."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "baselib")
        _create_vendored_recipe(recipes_dir, "app", deps=["baselib"])

        # First build.
        prefix1 = tmp_path / "prefix1"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )

        bc = BuildCache(cache_dir)
        initial_entries = len(bc.list_entries())
        assert initial_entries == 2

        # Get chain hashes before mutation.
        all_recipes_before = {r.name: r for r in list_recipes(recipes_dir)}
        app_hash_before = chain_hash(all_recipes_before["app"], all_recipes_before, "linux")

        # Modify baselib.
        recipe_yaml = recipes_dir / "baselib" / "recipe.yaml"
        data = yaml.safe_load(recipe_yaml.read_text())
        data["recipe"]["cvc_revision"] = 2
        recipe_yaml.write_text(yaml.dump(data, default_flow_style=False))

        # Verify app chain_hash changed.
        all_recipes_after = {r.name: r for r in list_recipes(recipes_dir)}
        app_hash_after = chain_hash(all_recipes_after["app"], all_recipes_after, "linux")
        assert app_hash_before != app_hash_after

        # Second build — both should miss cache.
        prefix2 = tmp_path / "prefix2"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix2,
            per_component=True,
        )

        # Now we should have 4 entries (2 old + 2 new).
        assert len(bc.list_entries()) == 4


# ── Server cache integration ───────────────────────────────────


fastapi = pytest.importorskip("fastapi", reason="server extras not installed")


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Runs real builds — too slow for CI",
)
class TestServerCacheIntegration:
    """End-to-end tests: build → push to server → pull from server."""

    @staticmethod
    def _start_server(tmp_path):
        """Bootstrap a cvcpkg test server and return (client, pub_token)."""
        from fastapi.testclient import TestClient

        from cvcpkg.server.app import create_app
        from cvcpkg.server.auth import TokenStore
        from cvcpkg.server.models import TokenRole

        store = TokenStore(tmp_path)
        pub_token = store.create("ci-bot", TokenRole.publisher)
        app = create_app(state_dir=tmp_path)
        client = TestClient(app)
        client.__enter__()
        return client, pub_token

    @staticmethod
    def _route_to_server(monkeypatch, client):
        """Send the builder's urllib calls and ``cvcpkg publish``'s httpx
        calls to the in-process TestClient."""
        import io
        import urllib.error
        import urllib.parse
        import urllib.request

        import httpx

        def fake_urlopen(req, timeout=None):
            url = urllib.parse.urlsplit(req.full_url)
            resp = client.request(
                req.get_method(),
                f"{url.path}?{url.query}" if url.query else url.path,
                content=req.data,
                headers=dict(req.header_items()),
            )
            if resp.status_code >= 400:
                raise urllib.error.HTTPError(req.full_url, resp.status_code, resp.text, None, None)
            out = io.BytesIO(resp.content)
            out.status = resp.status_code
            return out

        class PatchedClient(httpx.Client):
            def __init__(self, **kw):
                kw.setdefault("transport", client._transport)
                super().__init__(**kw)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr("httpx.Client", PatchedClient)

    def test_build_and_push_to_server(self, tmp_path, monkeypatch):
        """A build cache archive posted to /v1/publish lands in the catalog.

        Exercises the server's publish endpoint with an archive from a real
        build; the CLI never pushes such an archive itself (pack-all and
        build-all publish the packed bundle -- see test_push_records_runtime_deps).
        """

        from cvcpkg.builder import chain_hash as compute_chain_hash
        from cvcpkg.builder import list_recipes

        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))
        # Ensure in-process server uses YAML backend (matching TokenStore)
        # even when CVCPKG_DATABASE_URL is set by the Docker test env.
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)

        srv_dir = tmp_path / "server"
        srv_dir.mkdir()
        client, pub_token = self._start_server(srv_dir)

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libfoo")

        # Build first (without push) to populate local cache.
        prefix = tmp_path / "prefix"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        # Now push the cached archive to the server directly.
        all_recipes = {r.name: r for r in list_recipes(recipes_dir)}
        ch = compute_chain_hash(all_recipes["libfoo"], all_recipes, "linux")
        bc = BuildCache(cache_dir)
        arc = bc.lookup(ch, "linux", "x86_64", "release", "shared")
        assert arc is not None

        resp = client.post(
            "/v1/publish",
            params={
                "name": "libfoo",
                "version": "1.0.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "recipe_version": ch,
            },
            files={"file": ("libfoo.tar.zst", open(arc, "rb"))},  # noqa: SIM115
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200

        # Verify package was published to the server.
        resp = client.get("/v1/packages", params={"name": "libfoo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["packages"][0]["name"] == "libfoo"

        client.__exit__(None, None, None)

    def test_push_records_runtime_deps(self, tmp_path, monkeypatch):
        """``build-all --server-cache-push`` publishes packed bundles that
        record the recipe's runtime deps.

        Regression: the push sent the build cache's raw install tree with no
        ``required_deps`` (nor ``provides``); the server stored ``[]``, and the
        CI's later ``cvcpkg publish --all`` skipped the packed bundle as
        already published.  Over a thousand CI-built catalog rows
        (jsonschema-cp312, anyio-cp312, httpx-cp312, ...) reached the catalog
        with no dependency edges, so installing them left their deps out of
        the prefix.  build-all now packs each build, as pack-all does, and
        publishes that bundle -- manifest included.
        """
        import tarfile

        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))
        # Ensure in-process server uses YAML backend (matching TokenStore)
        # even when CVCPKG_DATABASE_URL is set by the Docker test env.
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)

        srv_dir = tmp_path / "server"
        srv_dir.mkdir()
        client, pub_token = self._start_server(srv_dir)

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libfoo")
        app_dir = _create_vendored_recipe(recipes_dir, "libapp")
        raw = yaml.safe_load((app_dir / "recipe.yaml").read_text())
        raw["depends"] = {
            "build": ["libfoo"],
            # The windows-only dep must not be recorded on a linux bundle.
            "runtime": ["libfoo", {"name": "libwin", "platforms": ["windows"]}],
        }
        raw["provides"] = ["app-slot"]
        (app_dir / "recipe.yaml").write_text(yaml.dump(raw))

        self._route_to_server(monkeypatch, client)

        result = CliRunner().invoke(
            cli,
            [
                "build-all",
                "--local",
                "--no-default-recipes",
                "--recipes-dir",
                str(recipes_dir),
                "--platform",
                "linux",
                "--prefix",
                str(tmp_path / "prefix"),
                "--server-cache",
                "http://testserver",
                "--server-cache-token",
                pub_token,
                "--server-cache-push",
            ],
        )
        assert result.exit_code == 0, result.output

        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        bundles = {b["name"]: b for b in resp.json()["bundles"]}
        assert bundles["libapp"]["required_deps"] == [{"name": "libfoo"}]
        assert bundles["libapp"]["provides"] == ["app-slot"]
        assert bundles["libfoo"]["required_deps"] == []

        # The packed bundle, not the build cache's raw install tree.
        archive = tmp_path / "libapp.tar.gz"
        archive.write_bytes(client.get(bundles["libapp"]["archive_url"]).content)
        with tarfile.open(archive) as tar:
            assert "share/libcvc-deps/libapp/manifest.yaml" in tar.getnames()

        client.__exit__(None, None, None)

    def test_pack_all_pushes_the_packed_bundle(self, tmp_path, monkeypatch):
        """``pack-all --server-cache-push`` publishes the bundle it packs.

        It used to publish the build cache's raw install tarball: no
        manifest.yaml, shebangs pointing into the CI runner's prefix, the
        package.files gate not yet run -- and that catalog row made the
        workflow's later ``cvcpkg publish`` of the real bundle a no-op.  A
        noarch recipe must also keep the any/noarch identity build_all probes
        the server cache under, so the next run's cache hit finds it.
        """
        import tarfile

        # Ensure in-process server uses YAML backend (matching TokenStore)
        # even when CVCPKG_DATABASE_URL is set by the Docker test env.
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        srv_dir = tmp_path / "server"
        srv_dir.mkdir()
        client, pub_token = self._start_server(srv_dir)
        self._route_to_server(monkeypatch, client)

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libfoo")
        tool_dir = _create_vendored_recipe(recipes_dir, "pytool")
        raw = yaml.safe_load((tool_dir / "recipe.yaml").read_text())
        raw["depends"] = {"build": ["libfoo"], "runtime": ["libfoo"]}
        raw["build"]["matrix"] = [{"platform": "any", "script": "build.sh"}]
        raw["package"]["files"] = ["bin/*"]
        (tool_dir / "recipe.yaml").write_text(yaml.dump(raw))
        # A console script whose shebang points into the build prefix, as pip
        # writes them; packing rewrites it to the relocatable /usr/bin/env form.
        (tool_dir / "build.sh").write_text(
            textwrap.dedent(
                """\
                #!/bin/bash
                set -e
                mkdir -p "$CVC_INSTALL_DIR/bin"
                printf '#!%s/bin/python3\\nprint(1)\\n' "$CVC_PREFIX" > "$CVC_INSTALL_DIR/bin/pytool"
                chmod +x "$CVC_INSTALL_DIR/bin/pytool"
                """
            )
        )

        def pack_all(run: int) -> str:
            monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / f"cache{run}"))
            result = CliRunner().invoke(
                cli,
                [
                    "pack-all",
                    "--local",
                    "--no-default-recipes",
                    "--recipes-dir",
                    str(recipes_dir),
                    "--platform",
                    "linux",
                    "--prefix",
                    str(tmp_path / f"prefix{run}"),
                    "--output-dir",
                    str(tmp_path / f"dist{run}"),
                    "--server-cache",
                    "http://testserver",
                    "--server-cache-token",
                    pub_token,
                    "--server-cache-push",
                ],
            )
            assert result.exit_code == 0, result.output
            return result.output

        pack_all(1)

        bundles = {b["name"]: b for b in client.get("/v1/catalog").json()["bundles"]}
        tool = bundles["pytool"]
        assert (tool["platform"], tool["arch"]) == ("any", "noarch")
        assert tool["required_deps"] == [{"name": "libfoo"}]
        all_recipes = {r.name: r for r in list_recipes(recipes_dir)}
        assert tool["recipe_version"] == chain_hash(all_recipes["pytool"], all_recipes, "any")

        archive = tmp_path / "pytool.tar.gz"
        archive.write_bytes(client.get(tool["archive_url"]).content)
        with tarfile.open(archive) as tar:
            assert "share/libcvc-deps/pytool/manifest.yaml" in tar.getnames()
            shebang = tar.extractfile("bin/pytool").readline()
        assert shebang == b"#!/usr/bin/env python3\n"

        # A second run from empty local caches restores both packed bundles
        # from the server instead of rebuilding them.
        out = pack_all(2)
        assert out.count("<- server cache hit") == 2, out

        client.__exit__(None, None, None)

    def test_pack_all_push_failure_is_not_fatal(self, tmp_path, monkeypatch):
        """A push that can't reach the server warns; the packed bundles stay
        in --output-dir for the workflow's ``cvcpkg publish`` to retry."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libfoo")

        result = CliRunner().invoke(
            cli,
            [
                "pack-all",
                "--local",
                "--no-default-recipes",
                "--recipes-dir",
                str(recipes_dir),
                "--platform",
                "linux",
                "--prefix",
                str(tmp_path / "prefix"),
                "--output-dir",
                str(tmp_path / "dist"),
                "--server-cache",
                "http://127.0.0.1:1",  # nothing listens here
                "--server-cache-token",
                "tok",
                "--server-cache-push",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "WARNING: server-cache push of libfoo-" in result.output
        assert list((tmp_path / "dist").glob("libfoo-*.tar.gz"))

    def test_server_cache_pull(self, tmp_path, monkeypatch, capsys):
        """Publish manually, then verify cache/status returns a hit."""
        from cvcpkg.builder import chain_hash as compute_chain_hash
        from cvcpkg.builder import list_recipes

        # Ensure in-process server uses YAML backend (matching TokenStore)
        # even when CVCPKG_DATABASE_URL is set by the Docker test env.
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)

        srv_dir = tmp_path / "server"
        srv_dir.mkdir()
        client, pub_token = self._start_server(srv_dir)

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libbar")

        # Compute the chain hash for this recipe.
        all_recipes = {r.name: r for r in list_recipes(recipes_dir)}
        ch = compute_chain_hash(all_recipes["libbar"], all_recipes, "linux")

        # Build first to get a real archive via cache.
        cache1 = tmp_path / "cache1"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache1))
        prefix1 = tmp_path / "prefix1"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )

        # Manually publish the cached archive to the server.
        bc1 = BuildCache(cache1)
        arc = bc1.lookup(ch, "linux", "x86_64", "release", "shared")
        assert arc is not None, "expected local cache to have the archive"
        resp = client.post(
            "/v1/publish",
            params={
                "name": "libbar",
                "version": "1.0.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "recipe_version": ch,
            },
            files={"file": ("libbar.tar.zst", open(arc, "rb"))},  # noqa: SIM115
            headers={"Authorization": f"Bearer {pub_token}"},
        )
        assert resp.status_code == 200

        # Verify cache/status endpoint returns a hit.
        resp = client.get(
            "/v1/cache/status",
            params={
                "name": "libbar",
                "chain_hash": ch,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is True
        assert data["name"] == "libbar"
        assert data["chain_hash"] == ch

        client.__exit__(None, None, None)

    def test_no_server_cache_flag(self, tmp_path, monkeypatch, capsys):
        """--no-server-cache disables all server cache interaction."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_vendored_recipe(recipes_dir, "libqux")

        prefix = tmp_path / "prefix"
        build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
            server_cache_url="http://127.0.0.1:1",  # would fail if used
            server_cache_token="fake",
            no_server_cache=True,
        )

        captured = capsys.readouterr()
        assert "server cache" not in captured.out
