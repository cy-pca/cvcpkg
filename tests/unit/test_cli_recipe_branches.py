# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Branch coverage for ``cvcpkg recipe …`` distribution commands (_recipe.py).

Every command bundles a recipe tree and talks to the server over httpx.  These
tests drive each command through ``CliRunner`` with the network mocked at the
``httpx.Client`` boundary — the tarballs are built for real (so extraction /
bundling is genuinely exercised) but nothing leaves the process.

Covered per command: the happy path, the ``recipe not found`` guard, and the
``server returned >= 400`` error branch in both its JSON-``detail`` and
plain-text flavours.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cvcpkg.cli._recipe import (
    _bundle_vendored_source,
    recipe_delete,
    recipe_list,
    recipe_publish,
    recipe_pull,
    recipe_pull_all,
    recipe_push,
    recipe_push_all,
    recipe_sync_common,
)

_ARGS = ["--server", "http://srv", "--token", "tok"]


# ── httpx boundary fakes ────────────────────────────────────────


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class FakeClient:
    def __init__(self, handler, calls):
        self._handler = handler
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        return self._dispatch("get", url, kw)

    def post(self, url, **kw):
        return self._dispatch("post", url, kw)

    def delete(self, url, **kw):
        return self._dispatch("delete", url, kw)

    def _dispatch(self, method, url, kw):
        self._calls.append((method, url, kw))
        return self._handler(method, url, **kw)


def patch_httpx(handler, calls):
    def factory(*a, **k):
        return FakeClient(handler, calls)

    return mock.patch("httpx.Client", factory)


# ── recipe-tree fixtures ────────────────────────────────────────


def _make_recipe(rdir: Path, name: str, *, version="1.2.3", extra_yaml="") -> Path:
    d = rdir / name
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(f"recipe:\n  upstream_version: '{version}'\n{extra_yaml}")
    (d / "build.sh").write_text("#!/bin/sh\necho build\n")
    return d


def _make_tar_gz(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname, body in members.items():
            data = body.encode()
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _run(cmd, args):
    return CliRunner().invoke(cmd, args)


# ── _bundle_vendored_source (pure helper) ───────────────────────


class TestBundleVendoredSource:
    def test_vendored_source_is_added_to_tar(self, tmp_path):
        rdir = tmp_path / "recipes"
        recipe = _make_recipe(
            rdir, "mylib", extra_yaml="source:\n  type: vendored\n  path: vendored/mylib\n"
        )
        vendored = tmp_path / "vendored" / "mylib"
        vendored.mkdir(parents=True)
        (vendored / "a.c").write_text("int a;\n")
        (vendored / "sub").mkdir()
        (vendored / "sub" / "b.c").write_text("int b;\n")

        tar = mock.MagicMock()
        _bundle_vendored_source(tar, recipe, rdir)

        # Normalise the separator: the arcname is built from a PurePath, which
        # renders "\\" on Windows and "/" on the CI ubuntu runner.
        arcnames = sorted(
            c.kwargs.get("arcname").replace("\\", "/") for c in tar.add.call_args_list
        )
        assert arcnames == ["_vendored/vendored/mylib/a.c", "_vendored/vendored/mylib/sub/b.c"]

    def test_non_vendored_recipe_adds_nothing(self, tmp_path):
        rdir = tmp_path / "recipes"
        recipe = _make_recipe(rdir, "mylib", extra_yaml="source:\n  type: git\n  url: x\n")
        tar = mock.MagicMock()
        _bundle_vendored_source(tar, recipe, rdir)
        tar.add.assert_not_called()

    def test_missing_recipe_yaml_returns_early(self, tmp_path):
        rdir = tmp_path / "recipes"
        recipe = rdir / "empty"
        recipe.mkdir(parents=True)
        tar = mock.MagicMock()
        _bundle_vendored_source(tar, recipe, rdir)
        tar.add.assert_not_called()

    def test_vendored_path_absent_on_disk_adds_nothing(self, tmp_path):
        rdir = tmp_path / "recipes"
        recipe = _make_recipe(
            rdir, "mylib", extra_yaml="source:\n  type: vendored\n  path: does/not/exist\n"
        )
        tar = mock.MagicMock()
        _bundle_vendored_source(tar, recipe, rdir)
        tar.add.assert_not_called()


# ── recipe push ─────────────────────────────────────────────────


class TestRecipePush:
    def test_push_happy_path_reports_uploaded(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        (rdir / "_common").mkdir()
        (rdir / "_common" / "env-linux.sh").write_text("shared\n")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"name": "zlib", "version": "1.2.3", "bundle_size": 999})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_push,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        assert "Recipe 'zlib' uploaded" in res.output
        assert "size=999 bytes" in res.output
        # POST went to the recipe endpoint with the version param.
        method, url, kw = calls[0]
        assert method == "post" and url.endswith("/v1/recipes/zlib")
        assert kw["params"]["version"] == "1.2.3"
        assert kw["headers"]["Authorization"] == "Bearer tok"

    def test_push_missing_recipe_dir_errors(self, tmp_path):
        rdir = tmp_path / "recipes"
        rdir.mkdir()
        res = _run(
            recipe_push,
            [*_ARGS, "ghost", "--recipes-dir", str(rdir), "--no-default-recipes"],
        )
        assert res.exit_code != 0
        assert "recipe directory not found" in res.output

    def test_push_server_error_json_detail(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(409, {"detail": "already exists"}, text="ignored")

        with patch_httpx(handler, calls):
            res = _run(
                recipe_push,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code != 0
        assert "server returned 409: already exists" in res.output

    def test_push_server_error_plain_text(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(500, json_data=None, text="boom")

        with patch_httpx(handler, calls):
            res = _run(
                recipe_push,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code != 0
        assert "server returned 500: boom" in res.output


# ── recipe list ─────────────────────────────────────────────────


class TestRecipeList:
    def test_list_empty(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"recipes": []})

        with patch_httpx(handler, calls):
            res = _run(recipe_list, _ARGS)
        assert res.exit_code == 0
        assert "No recipes found." in res.output

    def test_list_with_org_filter_and_rows(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(
                200,
                {
                    "recipes": [
                        {
                            "name": "zlib",
                            "version": "1.2.3",
                            "bundle_size": 1234,
                            "updated_at": "2026-01-01",
                        }
                    ]
                },
            )

        with patch_httpx(handler, calls):
            res = _run(recipe_list, [*_ARGS, "--org", "cvc"])
        assert res.exit_code == 0
        assert "zlib" in res.output and "1,234" in res.output
        assert calls[0][2]["params"] == {"org_slug": "cvc"}

    def test_list_server_error(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(403, {"detail": "forbidden"})

        with patch_httpx(handler, calls):
            res = _run(recipe_list, _ARGS)
        assert res.exit_code != 0
        assert "server returned 403: forbidden" in res.output

    def test_list_server_error_plain_text(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(500, json_data=None, text="internal boom")

        with patch_httpx(handler, calls):
            res = _run(recipe_list, _ARGS)
        assert res.exit_code != 0
        assert "server returned 500: internal boom" in res.output


# ── recipe delete ───────────────────────────────────────────────


class TestRecipeDelete:
    def test_delete_happy(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(recipe_delete, [*_ARGS, "zlib"])
        assert res.exit_code == 0
        assert "Recipe 'zlib' deleted." in res.output
        assert calls[0][0] == "delete"

    def test_delete_server_error(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(404, {"detail": "no such recipe"})

        with patch_httpx(handler, calls):
            res = _run(recipe_delete, [*_ARGS, "ghost"])
        assert res.exit_code != 0
        assert "server returned 404: no such recipe" in res.output

    def test_delete_server_error_plain_text(self):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(502, json_data=None, text="bad gateway")

        with patch_httpx(handler, calls):
            res = _run(recipe_delete, [*_ARGS, "zlib"])
        assert res.exit_code != 0
        assert "server returned 502: bad gateway" in res.output


# ── recipe publish ──────────────────────────────────────────────


class TestRecipePublish:
    def test_publish_push_and_register(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(
            rdir,
            "zlib",
            extra_yaml="cvc_revision: 4\n",
        )
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        assert "Recipe 'zlib' pushed (version=1.2.3)" in res.output
        assert "registered in catalog (version=1.2.3+cvc.4)" in res.output
        # Two POSTs: the bundle push and the placeholder register.
        assert [c[0] for c in calls] == ["post", "post"]
        assert calls[1][1].endswith("/v1/recipes/zlib/register")

    def test_publish_push_failure_aborts(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(500, {"detail": "push exploded"})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code != 0
        assert "recipe push failed (500): push exploded" in res.output
        # Never reached the register call.
        assert len(calls) == 1

    def test_publish_register_failure_is_a_warning_not_fatal(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        calls: list = []

        def handler(method, url, **kw):
            if url.endswith("/register"):
                return FakeResp(400, {"detail": "dup placeholder"})
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        # Register failing is non-fatal — the command still succeeds.
        assert res.exit_code == 0, res.output
        assert "warning: placeholder registration failed: dup placeholder" in res.output

    def test_publish_includes_common_and_masks_register_text_error(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib", extra_yaml="cvc_revision: 2\n")
        (rdir / "_common").mkdir()
        (rdir / "_common" / "env-linux.sh").write_text("shared helper\n")
        calls: list = []

        def handler(method, url, **kw):
            if url.endswith("/register"):
                return FakeResp(500, json_data=None, text="register text boom")
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        assert "warning: placeholder registration failed: register text boom" in res.output

    def test_publish_push_failure_plain_text(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "zlib")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(503, json_data=None, text="push text boom")

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "zlib", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code != 0
        assert "recipe push failed (503): push text boom" in res.output

    def test_publish_missing_recipe_dir_errors(self, tmp_path):
        rdir = tmp_path / "recipes"
        rdir.mkdir()
        res = _run(
            recipe_publish,
            [*_ARGS, "ghost", "--recipes-dir", str(rdir), "--no-default-recipes"],
        )
        assert res.exit_code != 0
        assert "recipe directory not found" in res.output

    def test_publish_missing_recipe_yaml_empty_version(self, tmp_path):
        rdir = tmp_path / "recipes"
        d = rdir / "bare"
        d.mkdir(parents=True)
        (d / "notes.txt").write_text("no recipe.yaml here\n")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_publish,
                [*_ARGS, "bare", "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        assert "Recipe 'bare' pushed (version=)" in res.output


# ── recipe pull / pull-all ──────────────────────────────────────


class TestRecipePull:
    def test_pull_extracts_bundle(self, tmp_path):
        content = _make_tar_gz(
            {
                "zlib/recipe.yaml": "recipe:\n  upstream_version: '1.0'\n",
                "zlib/build.sh": "echo hi\n",
            }
        )
        out = tmp_path / "out"
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, content=content)

        with patch_httpx(handler, calls):
            res = _run(recipe_pull, [*_ARGS, "zlib", "--output-dir", str(out)])
        assert res.exit_code == 0, res.output
        assert (out / "zlib" / "recipe.yaml").is_file()
        # The temporary bundle file is cleaned up after extraction.
        assert not (out / "zlib.tar.gz").exists()
        assert "extracted to" in res.output

    def test_pull_server_error(self, tmp_path):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(404, {"detail": "gone"})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_pull,
                [*_ARGS, "zlib", "--output-dir", str(tmp_path / "o")],
            )
        assert res.exit_code != 0
        assert "failed to download recipe 'zlib': gone" in res.output

    def test_pull_server_error_plain_text(self, tmp_path):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(500, json_data=None, text="pull text boom")

        with patch_httpx(handler, calls):
            res = _run(
                recipe_pull,
                [*_ARGS, "zlib", "--output-dir", str(tmp_path / "o")],
            )
        assert res.exit_code != 0
        assert "pull text boom" in res.output

    def test_pull_with_token_and_org_sets_headers_and_params(self, tmp_path):
        content = _make_tar_gz({"zlib/recipe.yaml": "recipe:\n  upstream_version: '1'\n"})
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, content=content)

        with patch_httpx(handler, calls):
            res = _run(
                recipe_pull,
                [*_ARGS, "zlib", "--org", "cvc", "--output-dir", str(tmp_path / "o")],
            )
        assert res.exit_code == 0, res.output
        _, _, kw = calls[0]
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["params"] == {"org_slug": "cvc"}


class TestRecipePullAll:
    def test_pull_all_counts_recipes(self, tmp_path):
        content = _make_tar_gz(
            {
                "zlib/recipe.yaml": "recipe:\n  upstream_version: '1'\n",
                "boost/recipe.yaml": "recipe:\n  upstream_version: '1'\n",
                "notes.txt": "loose file, not a recipe\n",
            }
        )
        out = tmp_path / "all"
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, content=content)

        with patch_httpx(handler, calls):
            res = _run(recipe_pull_all, [*_ARGS, "--output-dir", str(out)])
        assert res.exit_code == 0, res.output
        assert "2 recipes extracted" in res.output
        assert (out / "zlib" / "recipe.yaml").is_file()

    def test_pull_all_server_error_plain_text(self, tmp_path):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(503, json_data=None, text="unavailable")

        with patch_httpx(handler, calls):
            res = _run(recipe_pull_all, [*_ARGS, "--output-dir", str(tmp_path / "o")])
        assert res.exit_code != 0
        assert "failed to download recipe set: unavailable" in res.output

    def test_pull_all_server_error_json_detail_with_org(self, tmp_path):
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(403, {"detail": "not your org"})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_pull_all,
                [*_ARGS, "--org", "cvc", "--output-dir", str(tmp_path / "o")],
            )
        assert res.exit_code != 0
        assert "failed to download recipe set: not your org" in res.output
        assert calls[0][2]["params"] == {"org_slug": "cvc"}


# ── recipe push-all ─────────────────────────────────────────────


class TestRecipePushAll:
    def test_push_all_mixed_success_failure_and_exception(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "good", version="1")
        _make_recipe(rdir, "bad", version="2")
        _make_recipe(rdir, "boom", version="3")
        # Dirs starting with _ or . and dirs without recipe.yaml are skipped.
        (rdir / "_common").mkdir()
        (rdir / "_common" / "env.sh").write_text("x\n")
        (rdir / "not-a-recipe").mkdir()
        calls: list = []

        def handler(method, url, **kw):
            if url.endswith("/recipes/bad"):
                return FakeResp(500, {"detail": "server hates bad"})
            if url.endswith("/recipes/boom"):
                raise RuntimeError("connection reset")
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_push_all,
                [*_ARGS, "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        assert "good (version=1)" in res.output
        assert "bad: failed (500)" in res.output
        assert "boom: error (connection reset)" in res.output
        assert "pushed 1 recipes (2 failed)" in res.output

    def test_push_all_skips_hidden_and_non_recipe_dirs(self, tmp_path):
        rdir = tmp_path / "recipes"
        _make_recipe(rdir, "keep", version="9")
        (rdir / ".hidden").mkdir(parents=True)
        (rdir / ".hidden" / "recipe.yaml").write_text("recipe: {}\n")
        (rdir / "loose.txt").write_text("not a dir\n")
        calls: list = []

        def handler(method, url, **kw):
            return FakeResp(200, {"ok": True})

        with patch_httpx(handler, calls):
            res = _run(
                recipe_push_all,
                [*_ARGS, "--recipes-dir", str(rdir), "--no-default-recipes"],
            )
        assert res.exit_code == 0, res.output
        # Only "keep" is pushed; ".hidden" and the loose file are skipped.
        assert "pushed 1 recipes (0 failed)" in res.output
        assert len(calls) == 1


# ── recipe sync-common ──────────────────────────────────────────


def _bundled_common(tmp_path, files: dict[str, str]) -> Path:
    """Stand-in for the installed cvcpkg's recipes/ dir (with _common)."""
    recipes = tmp_path / "bundled" / "recipes"
    common = recipes / "_common"
    common.mkdir(parents=True)
    for name, body in files.items():
        (common / name).write_text(body)
    return recipes


def _work_recipes(tmp_path, files: dict[str, str]) -> Path:
    recipes = tmp_path / "work" / "recipes"
    common = recipes / "_common"
    common.mkdir(parents=True)
    for name, body in files.items():
        (common / name).write_text(body)
    return recipes


class TestRecipeSyncCommon:
    def test_adds_and_updates_and_reports(self, tmp_path, monkeypatch):
        bundled = _bundled_common(
            tmp_path, {"env-linux.sh": "new\n", "stage-source.sh": "helper\n"}
        )
        work = _work_recipes(tmp_path, {"env-linux.sh": "old\n"})
        monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)

        res = _run(recipe_sync_common, [str(work)])
        assert res.exit_code == 0, res.output
        assert (work / "_common" / "stage-source.sh").read_text() == "helper\n"
        assert (work / "_common" / "env-linux.sh").read_text() == "new\n"
        assert "added: stage-source.sh" in res.output
        assert "updated: env-linux.sh" in res.output
        assert "synced 2 file(s)" in res.output

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        bundled = _bundled_common(tmp_path, {"a.sh": "content\n"})
        work = _work_recipes(tmp_path, {})
        monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)

        res = _run(recipe_sync_common, [str(work), "--dry-run"])
        assert res.exit_code == 0, res.output
        assert not (work / "_common" / "a.sh").exists()
        assert "would add: a.sh" in res.output
        assert "would sync 1 file(s)" in res.output

    def test_already_up_to_date(self, tmp_path, monkeypatch):
        bundled = _bundled_common(tmp_path, {"a.sh": "same\n"})
        work = _work_recipes(tmp_path, {"a.sh": "same\n"})
        monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)

        res = _run(recipe_sync_common, [str(work)])
        assert res.exit_code == 0, res.output
        assert "already up to date" in res.output

    def test_same_tree_is_a_noop(self, tmp_path, monkeypatch):
        bundled = _bundled_common(tmp_path, {"a.sh": "x\n"})
        monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)

        # Point RECIPES_DIR at the bundled tree itself: source == destination.
        res = _run(recipe_sync_common, [str(bundled)])
        assert res.exit_code == 0, res.output
        assert "nothing to do" in res.output

    def test_missing_bundled_common_errors(self, tmp_path, monkeypatch):
        # A bundled recipes dir with NO _common subdir.
        bundled = tmp_path / "bundled" / "recipes"
        bundled.mkdir(parents=True)
        work = _work_recipes(tmp_path, {})
        monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)

        res = _run(recipe_sync_common, [str(work)])
        assert res.exit_code != 0
        assert "has no bundled _common" in res.output
