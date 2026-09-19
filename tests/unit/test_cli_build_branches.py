# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Branch coverage for the build / pack / recipe-inspection CLI (``cvcpkg.cli._build``).

The heavy machinery in ``cvcpkg.builder`` (real compiles), the revision server,
and platform detection are all patched at the boundary, so these tests exercise
the CLI's option handling, dispatch, and error branches without building
anything or touching a network.
"""

from __future__ import annotations

import io
import tarfile
import types
from pathlib import Path
from unittest import mock

import pytest

from cvcpkg.cli import _build, main

# ── fake recipe objects ──────────────────────────────────────────


class _Matrix:
    def __init__(self, platform):
        self.platform = platform


def fake_recipe(
    name,
    version="1.0.0",
    platforms=("linux",),
    tags=(),
    deps=None,
    recipe_dir=None,
    cvc_revision=1,
    source_type="url",
    source_url="https://example.com/src.tar.gz",
):
    r = types.SimpleNamespace()
    r.name = name
    r.cvc_revision = cvc_revision
    r.full_version = f"{version}+cvc.{cvc_revision}"
    r.build_matrix = [_Matrix(p) for p in platforms]
    r.tags = list(tags)
    r.raw = {"depends": {"build": list(deps or [])}}
    r.recipe_dir = Path(recipe_dir) if recipe_dir else Path(".")
    r.cross_toolchain_env = {}
    r.source = types.SimpleNamespace(type=source_type, url=source_url)
    return r


# ── _auto_platform ───────────────────────────────────────────────


class TestAutoPlatform:
    def test_auto_resolves_via_detect(self, monkeypatch):
        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        assert _build._auto_platform("auto") == "linux"

    def test_explicit_passthrough(self):
        assert _build._auto_platform("windows") == "windows"


# ── _resolve_recipe_dir ──────────────────────────────────────────


class TestResolveRecipeDir:
    def test_recipe_yaml_file_path(self, tmp_path):
        (tmp_path / "recipe.yaml").write_text("recipe: {}\n")
        assert _build._resolve_recipe_dir(str(tmp_path / "recipe.yaml")) == tmp_path.resolve()

    def test_directory_path(self, tmp_path):
        (tmp_path / "recipe.yaml").write_text("recipe: {}\n")
        assert _build._resolve_recipe_dir(str(tmp_path)) == tmp_path.resolve()

    def test_name_lookup_in_recipes_dir(self, tmp_path):
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo" / "recipe.yaml").write_text("recipe: {}\n")
        got = _build._resolve_recipe_dir("foo", (str(tmp_path),), no_default=True)
        assert got == (tmp_path / "foo").resolve()

    def test_not_found_raises(self, tmp_path):
        import click

        with pytest.raises(click.ClickException):
            _build._resolve_recipe_dir("ghost", (str(tmp_path),), no_default=True)


# ── _resolve_bump_revision ───────────────────────────────────────


class TestResolveBumpRevision:
    def test_explicit_cvc_revision_wins(self):
        assert (
            _build._resolve_bump_revision(
                fake_recipe("z"),
                bump=False,
                cvc_revision=5,
                bump_scope="name",
                server="",
                org="",
                token="",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
            )
            == 5
        )

    def test_no_bump_returns_none(self):
        assert (
            _build._resolve_bump_revision(
                fake_recipe("z"),
                bump=False,
                cvc_revision=None,
                bump_scope="name",
                server="",
                org="",
                token="",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
            )
            is None
        )

    def test_bump_queries_server(self, monkeypatch):
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        monkeypatch.setattr("cvcpkg.revisions.compute_pack_revision", lambda *a, **k: 9)
        got = _build._resolve_bump_revision(
            fake_recipe("z"),
            bump=True,
            cvc_revision=None,
            bump_scope="name",
            server="",
            org="cvc",
            token="t",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
        )
        assert got == 9


# ── _try_pull_server_recipes ─────────────────────────────────────


class TestTryPullServerRecipes:
    def _fake_client(self, resp=None, exc=None):
        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, **kw):
                if exc is not None:
                    raise exc
                return resp

        return FakeClient

    def test_non_200_returns_empty(self, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        resp = types.SimpleNamespace(status_code=503, content=b"")
        monkeypatch.setattr("httpx.Client", self._fake_client(resp=resp))
        assert _build._try_pull_server_recipes() == ()
        assert "falling back to local recipes" in capsys.readouterr().err

    def test_exception_returns_empty(self, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        monkeypatch.setattr("httpx.Client", self._fake_client(exc=RuntimeError("no route")))
        assert _build._try_pull_server_recipes() == ()
        assert "could not reach" in capsys.readouterr().err

    def test_token_header_set(self, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_secret")
        seen = {}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, **kw):
                seen.update(kw.get("headers", {}))
                return types.SimpleNamespace(status_code=500, content=b"")

        monkeypatch.setattr("httpx.Client", FakeClient)
        assert _build._try_pull_server_recipes() == ()
        assert seen.get("Authorization") == "Bearer cvctok_secret"

    def test_success_extracts(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"recipe: {}\n"
            info = tarfile.TarInfo(name="zlib/recipe.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        resp = types.SimpleNamespace(status_code=200, content=buf.getvalue())
        monkeypatch.setattr("httpx.Client", self._fake_client(resp=resp))
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        # Pre-create the extract dir to also exercise the rmtree branch.
        (tmp_path / "cvcpkg-server-recipes" / "recipes").mkdir(parents=True)

        result = _build._try_pull_server_recipes()
        assert result == (str(tmp_path / "cvcpkg-server-recipes" / "recipes"),)
        assert (Path(result[0]) / "zlib" / "recipe.yaml").is_file()
        assert "using recipes from" in capsys.readouterr().out


# ── recipes (list / show / validate / tag) ───────────────────────


class TestRecipesCommand:
    def test_list(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "cvcpkg.builder.list_recipes",
            lambda d: [fake_recipe("zlib"), fake_recipe("boost", tags=["math"])],
        )
        ret = main(["recipes", "--recipes-dir", str(tmp_path), "--no-default-recipes"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out and "boost" in out

    def test_list_tag_filter(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "cvcpkg.builder.list_recipes",
            lambda d: [fake_recipe("zlib"), fake_recipe("eigen", tags=["math"])],
        )
        ret = main(
            ["recipes", "--tag", "math", "--recipes-dir", str(tmp_path), "--no-default-recipes"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "eigen" in out
        assert "zlib" not in out

    def test_list_empty_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        ret = main(["recipes", "--recipes-dir", str(tmp_path), "--no-default-recipes"])
        assert ret != 0

    def test_list_tag_no_match_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [fake_recipe("zlib")])
        ret = main(
            ["recipes", "--tag", "nope", "--recipes-dir", str(tmp_path), "--no-default-recipes"]
        )
        assert ret != 0

    def test_show(self, tmp_path, monkeypatch, capsys):
        rec = fake_recipe(
            "grpc",
            tags=["net"],
            deps=["zlib", {"name": "openssl", "org": "cvc", "platforms": ["linux"]}],
        )
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda *a, **k: tmp_path)
        monkeypatch.setattr("cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: rec))
        ret = main(["recipes", "--show", "grpc"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Name:     grpc" in out
        assert "Source:   url" in out
        assert "URL:" in out
        assert "Platforms: linux" in out
        assert "Tags:     net" in out
        assert "openssl" in out  # dict-form dep with org + platforms
        assert "zlib" in out

    def test_validate_delegates(self, tmp_path, monkeypatch):
        fake_validate = mock.MagicMock()
        monkeypatch.setattr(_build, "validate", fake_validate)
        ret = main(["recipes", "--validate"])
        assert ret == 0
        fake_validate.assert_called_once()
        # invoked with target="all"
        assert fake_validate.call_args.kwargs.get("target") == "all"


# ── rev-bump ─────────────────────────────────────────────────────


class TestRevBump:
    def test_rev_bump(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "cvcpkg.builder.rev_bump",
            lambda name, rdir, **kw: [("openssl", 1, 2), ("libssh", 3, 4)],
        )
        ret = main(["rev-bump", "openssl", "--recipes-dir", str(tmp_path), "--no-default-recipes"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "openssl: cvc_revision 1" in out
        assert "2 recipe(s) bumped." in out


# ── next-revision ────────────────────────────────────────────────


class TestNextRevision:
    def test_next_revision(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda *a, **k: tmp_path)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: fake_recipe("libcvc"))
        )
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        monkeypatch.setattr("cvcpkg.revisions.compute_pack_revision", lambda *a, **k: 7)
        ret = main(["next-revision", "libcvc"])
        assert ret == 0
        assert capsys.readouterr().out.strip() == "7"


# ── cascade-bump ─────────────────────────────────────────────────


class TestCascadeBump:
    def test_offline(self, tmp_path, monkeypatch, capsys):
        # rev_bump invokes the revision_for callback; offline -> recipe.cvc_revision + 1.
        def fake_rev_bump(name, rdir, **kw):
            rev_for = kw["revision_for"]
            new = rev_for(fake_recipe("libcvc", cvc_revision=2))
            return [("libcvc", 2, new)]

        monkeypatch.setattr("cvcpkg.builder.rev_bump", fake_rev_bump)
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        ret = main(
            [
                "cascade-bump",
                "libcvc",
                "--offline",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "libcvc: cvc_revision 2 → 3" in out
        assert "1 recipe(s) bumped" in out

    def test_online_queries_server(self, tmp_path, monkeypatch, capsys):
        def fake_rev_bump(name, rdir, **kw):
            rev_for = kw["revision_for"]
            new = rev_for(fake_recipe("libcvc", cvc_revision=2))
            return [("libcvc", 2, new)]

        monkeypatch.setattr("cvcpkg.builder.rev_bump", fake_rev_bump)
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        monkeypatch.setattr("cvcpkg.revisions.compute_pack_revision", lambda *a, **k: 12)
        ret = main(
            ["cascade-bump", "libcvc", "--recipes-dir", str(tmp_path), "--no-default-recipes"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "→ 12" in out

    def test_nothing_to_bump(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.builder.rev_bump", lambda name, rdir, **kw: [])
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")
        ret = main(
            ["cascade-bump", "libcvc", "--recipes-dir", str(tmp_path), "--no-default-recipes"]
        )
        assert ret == 0
        assert "Nothing to bump" in capsys.readouterr().out


# ── world ────────────────────────────────────────────────────────


class TestWorld:
    def _reqs(self, monkeypatch, components):
        comps = types.SimpleNamespace(components=components)
        req_cls = mock.MagicMock()
        req_cls.from_yaml.return_value = comps
        monkeypatch.setattr("cvcpkg.manifest.Requirements", req_cls)

    def test_world_builds_in_order(self, tmp_path, monkeypatch, capsys):
        req = tmp_path / "req.yaml"
        req.write_text("components: []\n")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        self._reqs(
            monkeypatch,
            [
                types.SimpleNamespace(name="zlib", exclude=False),
                types.SimpleNamespace(name="old", exclude=True),
            ],
        )
        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        monkeypatch.setattr("cvcpkg.builder.load_all_recipes", lambda rdirs: [fake_recipe("zlib")])
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda recs, **k: list(recs))
        build_recipe = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        monkeypatch.setattr("cvcpkg.builder.BuildContext", mock.MagicMock())
        monkeypatch.setattr("cvcpkg.builder.Recipe", mock.MagicMock())

        ret = main(
            [
                "world",
                "--from",
                str(req),
                "--recipes-dir",
                str(a),
                "--recipes-dir",
                str(b),
                "--no-default-recipes",
                "--prefix",
                str(tmp_path / "deps"),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "world build complete" in out
        assert build_recipe.called

    def test_world_gathers_transitive_deps(self, tmp_path, monkeypatch, capsys):
        req = tmp_path / "req.yaml"
        req.write_text("components: []\n")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        self._reqs(monkeypatch, [types.SimpleNamespace(name="app", exclude=False)])
        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        # app -> lib (str) + tool (dict) + ghost (absent); lib -> app (cycle).
        app = fake_recipe("app", deps=["lib", {"name": "tool"}, "ghost"])
        lib = fake_recipe("lib", deps=["app"])
        tool = fake_recipe("tool")
        monkeypatch.setattr("cvcpkg.builder.load_all_recipes", lambda rdirs: [app, lib, tool])
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda recs, **k: list(recs))
        build_recipe = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        monkeypatch.setattr("cvcpkg.builder.BuildContext", mock.MagicMock())
        monkeypatch.setattr("cvcpkg.builder.Recipe", mock.MagicMock())

        ret = main(
            [
                "world",
                "--from",
                str(req),
                "--recipes-dir",
                str(a),
                "--recipes-dir",
                str(b),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        # app, lib and tool are all in the closure; ghost is absent and dropped.
        assert build_recipe.call_count == 3

    def test_world_no_matching_recipes(self, tmp_path, monkeypatch, capsys):
        req = tmp_path / "req.yaml"
        req.write_text("components: []\n")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        self._reqs(monkeypatch, [types.SimpleNamespace(name="app", exclude=False)])
        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        # Requested "app" is needed, but no recipe of that name is available.
        monkeypatch.setattr("cvcpkg.builder.load_all_recipes", lambda rdirs: [])
        monkeypatch.setattr("cvcpkg.builder.Recipe", mock.MagicMock())
        ret = main(
            [
                "world",
                "--from",
                str(req),
                "--recipes-dir",
                str(a),
                "--recipes-dir",
                str(b),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        assert "no matching recipes found" in capsys.readouterr().out

    def test_world_no_recipes_match(self, tmp_path, monkeypatch, capsys):
        req = tmp_path / "req.yaml"
        req.write_text("components: []\n")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        # Everything excluded -> requested set is empty -> nothing needed.
        self._reqs(monkeypatch, [types.SimpleNamespace(name="x", exclude=True)])
        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        monkeypatch.setattr("cvcpkg.builder.load_all_recipes", lambda rdirs: [])
        monkeypatch.setattr("cvcpkg.builder.Recipe", mock.MagicMock())
        ret = main(
            [
                "world",
                "--from",
                str(req),
                "--recipes-dir",
                str(a),
                "--recipes-dir",
                str(b),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        assert "no recipes match" in capsys.readouterr().out


# ── build ────────────────────────────────────────────────────────


class TestBuildNoDeps:
    def test_build_single(self, tmp_path, monkeypatch):
        build_recipe = mock.MagicMock()
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda *a, **k: [])
        ret = main(
            ["build", "zlib", "--local", "--platform", "linux", "--prefix", str(tmp_path / "d")]
        )
        assert ret == 0
        assert build_recipe.call_count == 1

    def test_build_multiple(self, tmp_path, monkeypatch):
        build_recipe = mock.MagicMock()
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda *a, **k: [])
        ret = main(["build", "zlib", "boost", "--local", "--platform", "linux"])
        assert ret == 0
        assert build_recipe.call_count == 2

    def test_deprecated_aliases_warn(self, tmp_path, monkeypatch, capsys):
        build_recipe = mock.MagicMock()
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda *a, **k: [])
        ret = main(
            [
                "build",
                "zlib",
                "--local",
                "--platform",
                "linux",
                "--host-tools-prefix",
                str(tmp_path / "ht"),
                "--keep-host-tools",
            ]
        )
        assert ret == 0
        err = capsys.readouterr().err
        assert "--host-tools-prefix is deprecated" in err
        assert "--keep-host-tools/--strip-host-tools is deprecated" in err


class TestBuildWithDeps:
    def _setup(self, tmp_path, monkeypatch):
        app = fake_recipe("app", recipe_dir=tmp_path / "app")
        rt = fake_recipe("rt", recipe_dir=tmp_path / "rt")
        bt = fake_recipe("bt", recipe_dir=tmp_path / "bt")
        ht = fake_recipe("ht", recipe_dir=tmp_path / "ht")
        by_name = {r.name: r for r in (app, rt, bt)}

        monkeypatch.setattr("cvcpkg.platform.detect_platform", lambda: "linux")
        monkeypatch.setattr("cvcpkg.platform.served_by_any_entry", lambda plats, plat: False)
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: list(by_name.values()))
        monkeypatch.setattr(
            "cvcpkg.builder.resolve_dep_closures", lambda names, bn, plat: ({"rt"}, {"bt"})
        )
        monkeypatch.setattr("cvcpkg.builder._collect_host_tools", lambda *a, **k: [ht])
        monkeypatch.setattr("cvcpkg.builder.resolve_build_order", lambda recs, *a, **k: list(recs))
        build_recipe = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.builder.build_recipe", build_recipe)
        return build_recipe

    def test_with_deps_strips_build_prefix(self, tmp_path, monkeypatch, capsys):
        build_recipe = self._setup(tmp_path, monkeypatch)
        write_rec = mock.MagicMock()
        strip = mock.MagicMock(return_value=tmp_path / "deps.build")
        monkeypatch.setattr("cvcpkg.host_tools.write_host_tools_record", write_rec)
        monkeypatch.setattr("cvcpkg.host_tools.strip_host_tools", strip)
        prefix = tmp_path / "deps"
        prefix.mkdir()
        ret = main(
            [
                "build",
                "app",
                "--with-deps",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--prefix",
                str(prefix),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "[host tool" in out
        assert "[build dep" in out
        assert "══ app" in out
        assert build_recipe.call_count >= 3  # host tool + build dep + targets
        assert write_rec.called
        assert strip.called
        assert "stripped build prefix" in out

    def test_with_deps_keep_build_prefix(self, tmp_path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr("cvcpkg.host_tools.write_host_tools_record", mock.MagicMock())
        strip = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.host_tools.strip_host_tools", strip)
        prefix = tmp_path / "deps"
        prefix.mkdir()
        ret = main(
            [
                "build",
                "app",
                "--with-deps",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--prefix",
                str(prefix),
                "--keep-build-prefix",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "build prefix kept" in out
        assert not strip.called


# ── pack ─────────────────────────────────────────────────────────


class TestPackErrors:
    def test_bump_and_cvc_revision_conflict(self, tmp_path):
        ret = main(
            ["pack", "zlib", "--local", "--platform", "linux", "--bump", "--cvc-revision", "3"]
        )
        assert ret != 0

    def test_bump_write_requires_bump(self, tmp_path):
        ret = main(["pack", "zlib", "--local", "--platform", "linux", "--bump-write"])
        assert ret != 0

    def test_from_prefix_requires_single_recipe(self, tmp_path):
        stage = tmp_path / "stage"
        stage.mkdir()
        ret = main(
            ["pack", "a", "b", "--local", "--platform", "linux", "--from-prefix", str(stage)]
        )
        assert ret != 0

    def test_from_prefix_and_prefix_conflict(self, tmp_path):
        stage = tmp_path / "stage"
        stage.mkdir()
        ret = main(
            [
                "pack",
                "a",
                "--local",
                "--platform",
                "linux",
                "--from-prefix",
                str(stage),
                "--prefix",
                str(tmp_path / "p"),
            ]
        )
        assert ret != 0

    def test_from_prefix_incompatible_with_bump_downstream(self, tmp_path):
        stage = tmp_path / "stage"
        stage.mkdir()
        ret = main(
            [
                "pack",
                "a",
                "--local",
                "--platform",
                "linux",
                "--from-prefix",
                str(stage),
                "--bump-downstream",
            ]
        )
        assert ret != 0


class TestPackHappy:
    def test_pack_recipe(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: fake_recipe("zlib"))
        )
        pack_recipe = mock.MagicMock(return_value=(tmp_path / "zlib.tar.gz", "abc123", 1000))
        monkeypatch.setattr("cvcpkg.builder.pack_recipe", pack_recipe)
        monkeypatch.setattr("cvcpkg.builder.pack_from_prefix", mock.MagicMock())
        ret = main(
            [
                "pack",
                "zlib",
                "--local",
                "--platform",
                "linux",
                "--output-dir",
                str(tmp_path / "dist"),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "1,000 bytes" in out
        assert "abc123" in out
        assert pack_recipe.called

    def test_pack_with_signing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: fake_recipe("zlib"))
        )
        archive = tmp_path / "zlib.tar.gz"
        monkeypatch.setattr("cvcpkg.builder.pack_recipe", lambda *a, **k: (archive, "sha", 10))
        sig = types.SimpleNamespace(key_fingerprint="deadbeefcafebabe0000")
        monkeypatch.setattr("cvcpkg.signing.sign_file", lambda a, k: sig)
        monkeypatch.setattr("cvcpkg.signing.write_signature", mock.MagicMock())
        key = tmp_path / "key.pem"
        key.write_text("PRIVATE")
        ret = main(["pack", "zlib", "--local", "--platform", "linux", "--signing-key", str(key)])
        assert ret == 0
        assert "Signed:" in capsys.readouterr().out

    def test_pack_from_prefix(self, tmp_path, monkeypatch, capsys):
        stage = tmp_path / "stage"
        stage.mkdir()
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: fake_recipe("zlib"))
        )
        pfp = mock.MagicMock(return_value=(tmp_path / "zlib.tar.gz", "sha9", 42))
        monkeypatch.setattr("cvcpkg.builder.pack_from_prefix", pfp)
        monkeypatch.setattr("cvcpkg.builder.pack_recipe", mock.MagicMock())
        ret = main(
            [
                "pack",
                "zlib",
                "--local",
                "--platform",
                "linux",
                "--from-prefix",
                str(stage),
                "--version-override",
                "2.0.0",
            ]
        )
        assert ret == 0
        assert "42 bytes" in capsys.readouterr().out
        assert pfp.called

    def test_pack_bump_write(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: fake_recipe("zlib"))
        )
        monkeypatch.setattr(
            "cvcpkg.builder.pack_recipe", lambda *a, **k: (tmp_path / "z.tgz", "s", 1)
        )
        bump_write = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.builder._bump_revision_in_yaml", bump_write)
        ret = main(
            [
                "pack",
                "zlib",
                "--local",
                "--platform",
                "linux",
                "--cvc-revision",
                "8",
                "--bump-write",
            ]
        )
        assert ret == 0
        assert "wrote cvc_revision: 8" in capsys.readouterr().out
        assert bump_write.called

    def test_pack_bump_downstream(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "app").mkdir()
        (tmp_path / "dep").mkdir()
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe",
            types.SimpleNamespace(load=lambda d: fake_recipe(Path(d).name)),
        )
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        monkeypatch.setattr("cvcpkg.builder.get_downstream", lambda base, recs, plat: ["dep"])
        pack_recipe = mock.MagicMock(return_value=(tmp_path / "x.tgz", "s", 5))
        monkeypatch.setattr("cvcpkg.builder.pack_recipe", pack_recipe)
        ret = main(
            [
                "pack",
                "app",
                "--local",
                "--platform",
                "linux",
                "--bump-downstream",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
            ]
        )
        assert ret == 0
        # Both the named recipe and its downstream dependent were packed.
        assert pack_recipe.call_count == 2


# ── build-all ────────────────────────────────────────────────────


class TestBuildAll:
    def test_success(self, tmp_path, monkeypatch):
        build_all = mock.MagicMock(return_value=types.SimpleNamespace(failures=[]))
        monkeypatch.setattr("cvcpkg.builder.build_all", build_all)
        ret = main(
            [
                "build-all",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--prefix",
                str(tmp_path / "deps"),
            ]
        )
        assert ret == 0
        assert build_all.called

    def test_failures_exit_nonzero(self, tmp_path, monkeypatch):
        build_all = mock.MagicMock(return_value=types.SimpleNamespace(failures=["zlib"]))
        monkeypatch.setattr("cvcpkg.builder.build_all", build_all)
        ret = main(
            [
                "build-all",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
            ]
        )
        assert ret != 0


# ── pack-all ─────────────────────────────────────────────────────


class _Contexts(list):
    failures: list = []


class TestPackAll:
    def test_bump_conflict(self, tmp_path):
        ret = main(["pack-all", "--local", "--platform", "linux", "--bump", "--cvc-revision", "2"])
        assert ret != 0

    def test_bump_write_requires_bump(self, tmp_path):
        ret = main(["pack-all", "--local", "--platform", "linux", "--bump-write"])
        assert ret != 0

    def test_invalid_shard(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--shard",
                "abc",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
            ]
        )
        assert ret != 0

    def test_shard_out_of_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--shard",
                "5/3",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
            ]
        )
        assert ret != 0

    def test_empty_contexts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        monkeypatch.setattr("cvcpkg.builder._artifacts_cover", lambda *a, **k: True)
        monkeypatch.setattr("cvcpkg.builder.build_all", lambda *a, **k: _Contexts())
        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--output-dir",
                str(tmp_path / "dist"),
            ]
        )
        assert ret == 0

    def test_failures_exit_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        monkeypatch.setattr("cvcpkg.builder._artifacts_cover", lambda *a, **k: True)
        failed = _Contexts()
        failed.failures = ["zlib"]
        monkeypatch.setattr("cvcpkg.builder.build_all", lambda *a, **k: failed)
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--output-dir",
                str(tmp_path / "dist"),
            ]
        )
        assert ret != 0

    def test_bump_write_stamps_revision(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: [])
        monkeypatch.setattr("cvcpkg.builder._artifacts_cover", lambda *a, **k: True)
        work = tmp_path / "work"
        work.mkdir()
        recipe = fake_recipe("zlib", recipe_dir=tmp_path / "zlib")
        ctx = types.SimpleNamespace(
            platform="linux",
            install_dir=tmp_path / "inst",
            recipe=recipe,
            work_dir=work,
            prefix=tmp_path / "pref",
            build_prefix=tmp_path / "bp",
        )
        monkeypatch.setattr("cvcpkg.builder.build_all", lambda *a, **k: _Contexts([ctx]))
        monkeypatch.setattr("cvcpkg.builder.generate_manifest", lambda *a, **k: {"m": 1})
        monkeypatch.setattr("cvcpkg.builder.stage_bundle", mock.MagicMock())
        monkeypatch.setattr(
            "cvcpkg.builder.create_archive", lambda *a, **k: (tmp_path / "z.tgz", "s", 1)
        )
        bump_write = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.builder._bump_revision_in_yaml", bump_write)
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--cvc-revision",
                "9",
                "--bump-write",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--output-dir",
                str(tmp_path / "dist"),
            ]
        )
        assert ret == 0
        assert bump_write.called
        assert recipe.cvc_revision == 9

    def test_packs_one_context_with_skip_and_signing(self, tmp_path, monkeypatch, capsys):
        # A recipe with no matching artifact is reported as skipped.
        monkeypatch.setattr(
            "cvcpkg.builder.list_recipes", lambda d: [fake_recipe("skip", platforms=["linux"])]
        )
        monkeypatch.setattr("cvcpkg.builder._artifacts_cover", lambda *a, **k: False)

        work = tmp_path / "work"
        work.mkdir()
        ctx = types.SimpleNamespace(
            platform="linux",
            install_dir=tmp_path / "inst",
            recipe=fake_recipe("zlib", recipe_dir=tmp_path / "zlib"),
            work_dir=work,
            prefix=tmp_path / "pref",
            build_prefix=tmp_path / "bp",
        )
        monkeypatch.setattr("cvcpkg.builder.build_all", lambda *a, **k: _Contexts([ctx]))
        monkeypatch.setattr("cvcpkg.builder.generate_manifest", lambda *a, **k: {"m": 1})
        monkeypatch.setattr("cvcpkg.builder.stage_bundle", mock.MagicMock())
        archive = tmp_path / "zlib.tar.gz"
        monkeypatch.setattr("cvcpkg.builder.create_archive", lambda *a, **k: (archive, "shaX", 500))
        sig = types.SimpleNamespace(key_fingerprint="fingerprint00000000")
        monkeypatch.setattr("cvcpkg.signing.sign_file", lambda a, k: sig)
        monkeypatch.setattr("cvcpkg.signing.write_signature", mock.MagicMock())
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        key = tmp_path / "key.pem"
        key.write_text("PRIVATE")

        ret = main(
            [
                "pack-all",
                "--local",
                "--platform",
                "linux",
                "--shard",
                "0/2",
                "--recipes-dir",
                str(tmp_path),
                "--no-default-recipes",
                "--output-dir",
                str(tmp_path / "dist"),
                "--signing-key",
                str(key),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "Skipping 1 recipe(s)" in out
        assert "zlib.tar.gz" in out
        assert "500 bytes" in out
        assert "Signed:" in out
