"""`redistributable: false` -- recipes cvcpkg builds where they are installed and never publishes.

The NVIDIA CUDA toolkit is the case in point: its EULA allows redistributing a
handful of runtime libraries inside an application, never nvcc or the SDK as a
stand-alone product, so recipes/cuda-toolkit downloads NVIDIA's own archives on
the installing machine.  These tests pin every path that would otherwise turn
such a recipe into a bundle or a fleet job (pack, pack --from-prefix, pack-all,
the build-cache push, submit-dag, and the server's /v1/publish, /v1/builds and
/v1/builds/dag), the install path that builds it instead, and the recipe itself.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cvcpkg.builder import NotRedistributableError, Recipe

REPO = Path(__file__).resolve().parents[2]


def _write_recipe(root: Path, name: str, *, redistributable=None, deps=(), platforms=("linux",)):
    rdir = root / name
    rdir.mkdir(parents=True)
    flag = "" if redistributable is None else f"redistributable: {str(redistributable).lower()}\n"
    build_deps = "".join(f"\n    - name: {d}" for d in deps) or " []"
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
  license: MIT
{flag}source:
  type: none
depends:
  build:{build_deps}
build:
  matrix:
{matrix}package:
  files:
    - bin/
"""
    )
    (rdir / "build.sh").write_text("#!/bin/sh\nexit 0\n")
    return rdir


# ── recipe model and schema ─────────────────────────────────────


class TestRecipeModel:
    def test_default_is_redistributable(self, tmp_path):
        assert Recipe.load(_write_recipe(tmp_path, "zlib")).redistributable is True

    def test_false_is_read(self, tmp_path):
        r = Recipe.load(_write_recipe(tmp_path, "vendorkit", redistributable=False))
        assert r.redistributable is False
        assert "cvcpkg install vendorkit" in r.not_redistributable_reason

    def test_schema_accepts_a_bool_and_rejects_anything_else(self, tmp_path):
        from cvcpkg.validation import validate_recipe_dir

        assert validate_recipe_dir(_write_recipe(tmp_path, "a", redistributable=False)) == []
        bad = _write_recipe(tmp_path, "b")
        text = (bad / "recipe.yaml").read_text()
        (bad / "recipe.yaml").write_text(text.replace("source:", 'redistributable: "no"\nsource:'))
        assert any("redistributable" in e for e in validate_recipe_dir(bad))


# ── every path that makes a bundle refuses ──────────────────────


class TestPackingRefuses:
    def test_pack_recipe_refuses_before_building(self, tmp_path, monkeypatch):
        from cvcpkg import builder

        def _no_build(*a, **kw):
            raise AssertionError("pack_recipe built a recipe it must refuse")

        monkeypatch.setattr(builder, "build_recipe", _no_build)
        rdir = _write_recipe(tmp_path, "vendorkit", redistributable=False)
        with pytest.raises(NotRedistributableError, match="vendorkit is not redistributable"):
            builder.pack_recipe(rdir, output_dir=tmp_path / "dist")
        assert not (tmp_path / "dist").exists()

    def test_pack_from_prefix_refuses(self, tmp_path):
        from cvcpkg.builder import pack_from_prefix

        rdir = _write_recipe(tmp_path, "vendorkit", redistributable=False)
        prefix = tmp_path / "prefix"
        (prefix / "bin").mkdir(parents=True)
        with pytest.raises(NotRedistributableError):
            pack_from_prefix(rdir, prefix, output_dir=tmp_path / "dist")
        assert not (tmp_path / "dist").exists()

    def test_generate_manifest_is_the_backstop(self, tmp_path):
        # Every staging site generates a manifest first, so a future packing
        # path that forgets the up-front check still cannot produce a bundle.
        from cvcpkg.builder import generate_manifest

        recipe = Recipe.load(_write_recipe(tmp_path, "vendorkit", redistributable=False))
        with pytest.raises(NotRedistributableError):
            generate_manifest(recipe, tmp_path, "linux", "x86_64", "release", "shared")

    def test_generate_manifest_still_works_for_ordinary_recipes(self, tmp_path):
        from cvcpkg.builder import generate_manifest

        recipe = Recipe.load(_write_recipe(tmp_path, "zlib"))
        install = tmp_path / "install"
        (install / "bin").mkdir(parents=True)
        manifest = generate_manifest(recipe, install, "linux", "x86_64", "release", "shared")
        assert manifest["bundle"]["name"] == "zlib"

    def test_build_all_push_publishes_nothing(self, tmp_path, monkeypatch):
        # build-all --server-cache-push publishes the bundles it packs, and
        # creates catalog rows doing so: a redistributable:false build is
        # never one of them.
        from click.testing import CliRunner

        from cvcpkg import builder
        from cvcpkg.cli import _build, cli

        rdir = _write_recipe(tmp_path / "recipes", "vendorkit", redistributable=False)
        work = tmp_path / "work"
        work.mkdir()
        ctx = SimpleNamespace(
            recipe=Recipe.load(rdir),
            platform="linux",
            work_dir=work,
            prefix=tmp_path / "prefix",
            install_dir=work / "install",
        )

        class _Contexts(list):
            failures: list = []

        seen: dict = {}

        def fake_build_all(*a, **kw):
            seen.update(kw)
            return _Contexts([ctx])

        def no_push(*a, **kw):
            raise AssertionError("pushed a non-redistributable build")

        monkeypatch.setattr(builder, "build_all", fake_build_all)
        monkeypatch.setattr(_build, "_push_packed", no_push)
        res = CliRunner().invoke(
            cli,
            [
                "build-all",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                "--local",
                "--server-cache",
                "http://s.example",
                "--server-cache-token",
                "tok",
                "--server-cache-push",
            ],
        )
        assert res.exit_code == 0, res.output
        assert "not pushing" in res.output and "vendorkit" in res.output
        # Pushing packs each recipe's own tree: built per component, kept.
        assert seen["per_component"] is True and seen["cleanup_work_dirs"] is False

    def test_pack_all_builds_but_does_not_pack(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from cvcpkg import builder
        from cvcpkg.cli import cli

        rdir = _write_recipe(tmp_path / "recipes", "vendorkit", redistributable=False)
        work = tmp_path / "work"
        work.mkdir()
        ctx = SimpleNamespace(
            recipe=Recipe.load(rdir),
            platform="linux",
            work_dir=work,
            prefix=tmp_path / "prefix",
            install_dir=work / "install",
        )

        class _Contexts(list):
            failures: list = []

        monkeypatch.setattr(builder, "build_all", lambda *a, **kw: _Contexts([ctx]))
        out = tmp_path / "dist"
        res = CliRunner().invoke(
            cli,
            [
                "pack-all",
                "--platform",
                "linux",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                "--output-dir",
                str(out),
                "--local",
            ],
        )
        assert res.exit_code == 0, res.output
        assert "not packing" in res.output and "vendorkit" in res.output
        assert list(out.iterdir()) == []


# ── submit-dag never makes it a job ─────────────────────────────


def _fake_client(posted, builders):
    class _Resp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, **kw):
            if "/v1/builders" in url:
                return _Resp({"builders": builders})
            if "/v1/packages" in url:
                return _Resp({"total": 0, "packages": []})
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url, json=None, **kw):
            posted.append(json)
            return _Resp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

    return _Client


def _submit_dag(tmp_path, monkeypatch, capsys, *names, deps=False):
    from cvcpkg.cli import main

    posted: list = []
    builder = {"platform": "linux", "arch": "x86_64", "capabilities": {}}
    monkeypatch.setattr("httpx.Client", _fake_client(posted, [builder]))
    argv = [
        "builds",
        "submit-dag",
        "--server",
        "http://s.example",
        "--token",
        "tok",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--recipes-dir",
        str(tmp_path / "recipes"),
        "--no-default-recipes",
        *([] if deps else ["--no-deps"]),
        *names,
    ]
    rc = main(argv)
    jobs = [j["recipe_name"] for body in posted for j in body["jobs"]]
    return rc, jobs, capsys.readouterr().out


class TestSubmitDag:
    def test_named_recipe_is_skipped_and_the_rest_still_submit(self, tmp_path, monkeypatch, capsys):
        _write_recipe(tmp_path / "recipes", "vendorkit", redistributable=False)
        _write_recipe(tmp_path / "recipes", "zlib")
        rc, jobs, out = _submit_dag(tmp_path, monkeypatch, capsys, "vendorkit", "zlib")
        assert rc == 0
        assert jobs == ["zlib"]
        assert "Skipping 1 non-redistributable recipe(s)" in out

    def test_only_a_non_redistributable_recipe_submits_nothing(self, tmp_path, monkeypatch, capsys):
        # What build-on-dev does for a PR touching only such a recipe.
        _write_recipe(tmp_path / "recipes", "vendorkit", redistributable=False)
        rc, jobs, _out = _submit_dag(tmp_path, monkeypatch, capsys, "vendorkit")
        assert rc == 0
        assert jobs == []

    def test_auto_deps_never_adds_it_as_a_job(self, tmp_path, monkeypatch, capsys):
        _write_recipe(tmp_path / "recipes", "vendorkit", redistributable=False)
        _write_recipe(tmp_path / "recipes", "app", deps=("vendorkit",))
        rc, jobs, out = _submit_dag(tmp_path, monkeypatch, capsys, "app", deps=True)
        assert rc == 0
        assert jobs == ["app"]
        assert "non-redistributable dependency(ies) vendorkit" in out


# ── the server refuses too ──────────────────────────────────────


def _recipe_bundle(name: str, *, redistributable: bool) -> bytes:
    body = (
        f'schema_version: 1\nrecipe:\n  name: {name}\n  upstream_version: "1.0.0"\n'
        f"  cvc_revision: 1\nredistributable: {str(redistributable).lower()}\n"
        "build:\n  matrix:\n    - platform: linux\n      script: build.sh\n"
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"{name}/recipe.yaml")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture(scope="module")
def server_env(tmp_path_factory):
    """One DB-backed server for the module (see test_server_deps_graph.py for
    why per-test apps are avoided)."""
    pytest.importorskip("fastapi", reason="server extras not installed")
    import os

    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.models import TokenRole

    tmp_path = tmp_path_factory.mktemp("redistributable")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"
    prev_db = os.environ.get("CVCPKG_DATABASE_URL")
    prev_mirror = os.environ.get("CVCPKG_MIRROR_MODE")
    os.environ["CVCPKG_DATABASE_URL"] = db_url
    os.environ.pop("CVCPKG_MIRROR_MODE", None)

    async def _seed():
        init_db(db_url)
        await create_tables()
        admin = await DbTokenStore(tmp_path).create("test-admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    try:
        with TestClient(app) as client:
            hdr = {"Authorization": f"Bearer {admin_token}"}
            for name, redist in (("vendorkit", False), ("plainlib", True)):
                resp = client.post(
                    f"/v1/recipes/{name}",
                    headers=hdr,
                    files={
                        "file": (
                            f"{name}.tar.gz",
                            _recipe_bundle(name, redistributable=redist),
                            "application/gzip",
                        )
                    },
                )
                assert resp.status_code in (200, 201), resp.text
            yield client, hdr
    finally:
        try:
            asyncio.run(dispose_engine())
        except Exception:
            pass
        if prev_db is None:
            os.environ.pop("CVCPKG_DATABASE_URL", None)
        else:
            os.environ["CVCPKG_DATABASE_URL"] = prev_db
        if prev_mirror is not None:
            os.environ["CVCPKG_MIRROR_MODE"] = prev_mirror


def _publish(client, hdr, name):
    return client.post(
        "/v1/publish",
        params={
            "name": name,
            "version": "1.0.0+cvc.1",
            "platform": "linux",
            "arch": "x86_64",
        },
        files={"file": (f"{name}.tar.zst", io.BytesIO(b"payload"))},
        headers=hdr,
    )


class TestServerRefuses:
    def test_publish_of_a_pushed_non_redistributable_recipe_is_forbidden(self, server_env):
        client, hdr = server_env
        resp = _publish(client, hdr, "vendorkit")
        assert resp.status_code == 403, resp.text
        assert "redistributable: false" in resp.json()["detail"]

    def test_publish_of_an_ordinary_recipe_is_unaffected(self, server_env):
        client, hdr = server_env
        assert _publish(client, hdr, "plainlib").status_code == 200

    def test_publish_of_a_package_with_no_recipe_is_unaffected(self, server_env):
        client, hdr = server_env
        assert _publish(client, hdr, "norecipe-xyz").status_code == 200

    def test_publish_checks_the_server_s_own_recipe_tree_too(
        self, server_env, tmp_path, monkeypatch
    ):
        # Not pushed, but vendored in the server's recipes directory.
        client, hdr = server_env
        _write_recipe(tmp_path / "recipes", "localkit", redistributable=False)
        monkeypatch.setattr(
            "cvcpkg.builder.find_recipes_dir", lambda *a, **kw: tmp_path / "recipes"
        )
        assert _publish(client, hdr, "localkit").status_code == 403

    def test_single_build_job_is_rejected(self, server_env):
        client, hdr = server_env
        resp = client.post(
            "/v1/builds",
            headers=hdr,
            json={"recipe_name": "vendorkit", "platform": "linux", "arch": "x86_64"},
        )
        assert resp.status_code == 422, resp.text

    def test_dag_containing_one_is_rejected(self, server_env):
        client, hdr = server_env
        jobs = [
            {"recipe_name": "plainlib", "platform": "linux", "arch": "x86_64"},
            {"recipe_name": "vendorkit", "platform": "linux", "arch": "x86_64"},
        ]
        resp = client.post("/v1/builds/dag", headers=hdr, json={"jobs": jobs})
        assert resp.status_code == 422, resp.text
        assert "vendorkit" in resp.json()["detail"]
        ok = client.post("/v1/builds/dag", headers=hdr, json={"jobs": jobs[:1]})
        assert ok.status_code == 200, ok.text


# ── install builds it instead ───────────────────────────────────


def _bundle(name, deps=()):
    return {
        "name": name,
        "version": "1.0.0+cvc.1",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "sha256": "abc",
        "size_bytes": 1,
        "archive_url": f"https://cvcpkg.org/a/{name}.tar.gz",
        "required_deps": [{"name": d} for d in deps],
    }


class TestResolverLocalOnly:
    def _entries(self, *bundles):
        from cvcpkg.catalog import catalog_entries

        entries = catalog_entries(
            {"schema_version": 1, "revision": 1, "bundles": list(bundles)},
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
        )
        out: dict = {}
        for e in entries:
            out.setdefault(e.name, []).append(e)
        return out

    def test_a_catalog_bundle_may_depend_on_a_local_only_name(self):
        from cvcpkg.manifest import ComponentReq
        from cvcpkg.resolver import resolve

        cands = self._entries(_bundle("torch", ["nvwheel"]), _bundle("python311"))
        local = {"nvwheel": ["python311"]}
        result = resolve(
            [ComponentReq(name="torch")], cands, capabilities=set(), local_only=local.get
        )
        # The local name's own deps come from the catalog, like any other dep.
        assert sorted(result.picked) == ["python311", "torch"]
        assert result.local == ["nvwheel"]

    def test_without_the_hook_a_missing_dep_still_fails(self):
        from cvcpkg.errors import ResolveError
        from cvcpkg.manifest import ComponentReq
        from cvcpkg.resolver import resolve

        cands = self._entries(_bundle("torch", ["nvwheel"]))
        with pytest.raises(ResolveError, match="nvwheel"):
            resolve([ComponentReq(name="torch")], cands, capabilities=set())

    def test_local_names_come_out_dependencies_first(self):
        from cvcpkg.manifest import ComponentReq
        from cvcpkg.resolver import resolve

        local = {"a": ["b"], "b": ["c"], "c": []}
        result = resolve([ComponentReq(name="a")], {}, capabilities=set(), local_only=local.get)
        assert result.local == ["c", "b", "a"]

    def test_a_capability_filtered_name_is_not_built_locally(self):
        # Its catalog providers exist but this host cannot use them; building
        # it from source instead would silently change what the user gets.
        from cvcpkg.errors import ResolveError
        from cvcpkg.manifest import ComponentReq
        from cvcpkg.resolver import resolve

        gpu = dict(_bundle("kit"), requires_capabilities=["cuda"])
        cands = self._entries(gpu)
        with pytest.raises(ResolveError, match="host is missing: cuda"):
            resolve(
                [ComponentReq(name="kit")],
                cands,
                capabilities=set(),
                local_only=lambda n: [],
            )


class TestInstall:
    def test_not_redistributable_deps(self, tmp_path):
        from cvcpkg.installer import not_redistributable_deps

        root = tmp_path / "recipes"
        _write_recipe(root, "vendorkit", redistributable=False, deps=("python311",))
        _write_recipe(root, "zlib")
        assert not_redistributable_deps("vendorkit", [root], "linux") == ["python311"]
        assert not_redistributable_deps("zlib", [root], "linux") is None
        assert not_redistributable_deps("missing", [root], "linux") is None

    def test_a_dependency_of_a_catalog_bundle_is_built_after_the_catalog_installs(
        self, tmp_path, monkeypatch
    ):
        # torch-cp311-cuda (published) -> nvidia-*-cu12 wheels (not
        # redistributable) -> python311 (published): the wheels are built
        # locally, after python311 is in the prefix.
        from click.testing import CliRunner

        from cvcpkg import installer
        from cvcpkg.cli import cli

        root = tmp_path / "recipes"
        _write_recipe(root, "nvwheel", redistributable=False, deps=("python311",))
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [_bundle("torch", ["nvwheel", "python311"]), _bundle("python311")],
                }
            )
        )
        events: list[str] = []
        monkeypatch.setattr(
            installer,
            "install_entry",
            lambda entry, *a, **kw: events.append(f"install {entry.name}"),
        )
        monkeypatch.setattr(
            installer,
            "build_from_source_fallback",
            lambda name, *a, **kw: events.append(f"build {name}"),
        )
        prefix = tmp_path / "prefix"
        res = CliRunner().invoke(
            cli,
            [
                "install",
                "torch",
                "--catalog",
                str(catalog),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(root),
                "--no-default-recipes",
                "--prefix",
                str(prefix),
            ],
        )
        assert res.exit_code == 0, res.output
        assert events == ["install python311", "install torch", "build nvwheel"]
        assert "nvwheel is not redistributable" in res.output
        assert "3 component(s) installed" in res.output

    def test_install_builds_it_without_fallback_to_source(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from cvcpkg import installer
        from cvcpkg.cli import cli

        root = tmp_path / "recipes"
        _write_recipe(root, "vendorkit", redistributable=False)
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "bundles": [
                        {
                            "name": "zlib",
                            "version": "1.3.1+cvc.1",
                            "platform": "linux",
                            "arch": "x86_64",
                            "build_type": "release",
                            "link": "shared",
                            "sha256": "abc",
                            "size_bytes": 1,
                            "archive_url": "https://cvcpkg.org/a/zlib.tar.gz",
                        }
                    ],
                }
            )
        )
        built: list[str] = []
        monkeypatch.setattr(
            installer, "build_from_source_fallback", lambda name, *a, **kw: built.append(name)
        )
        prefix = tmp_path / "prefix"
        res = CliRunner().invoke(
            cli,
            [
                "install",
                "vendorkit",
                "--catalog",
                str(catalog),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(root),
                "--no-default-recipes",
                "--prefix",
                str(prefix),
            ],
        )
        assert res.exit_code == 0, res.output
        assert built == ["vendorkit"]
        assert "not redistributable" in res.output
        assert "1 component(s) installed" in res.output
        lock = yaml.safe_load((prefix / "share" / "libcvc-deps" / "lockfile.yaml").read_text())
        assert [(b["name"], b["source_release"]) for b in lock["bundles"]] == [
            ("vendorkit", "source-build")
        ]


# ── the cuda-toolkit recipe itself ──────────────────────────────

_CUDA = REPO / "recipes" / "cuda-toolkit"
_LOCK_LINE = re.compile(
    r"^(?P<plat>[a-z0-9_-]+) (?P<comp>[a-z0-9_]+) (?P<ver>[0-9.]+) (?P<sha>[0-9a-f]{64}) "
    r"(?P<path>\S+) (?P<name>.+)$"
)


def _lock():
    release, rows = None, []
    for line in (_CUDA / "redist.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("release "):
            release = line.split()[1]
            continue
        m = _LOCK_LINE.match(line)
        assert m, f"malformed redist.lock line: {line!r}"
        rows.append(m.groupdict())
    return release, rows


class TestCudaToolkitRecipe:
    def test_recipe_validates_and_is_not_redistributable(self):
        from cvcpkg.validation import validate_recipe_dir

        assert validate_recipe_dir(_CUDA) == []
        recipe = Recipe.load(_CUDA)
        assert recipe.redistributable is False
        # It PROVIDES nvcc; requiring the capability would be circular.
        assert recipe.requires_capabilities == []

    def test_lock_matches_the_recipe(self):
        recipe = Recipe.load(_CUDA)
        release, rows = _lock()
        assert release == recipe.upstream_version
        assert (
            {r["plat"] for r in rows}
            == set(recipe.source.artifacts)
            == {
                "linux-x86_64",
                "windows-x86_64",
            }
        )
        for plat in ("linux-x86_64", "windows-x86_64"):
            comps = {r["comp"] for r in rows if r["plat"] == plat}
            assert {"cuda_nvcc", "cuda_cudart", "cuda_cccl"} <= comps
        for r in rows:
            assert r["path"].startswith(f"{r['comp']}/{r['plat']}/{r['comp']}-{r['plat']}-")
            assert r["ver"] in r["path"]
        # The manifest the lock was generated from is the one the recipe names.
        header = (_CUDA / "redist.lock").read_text()
        for art in recipe.source.artifacts.values():
            assert art["url"] in header and art["sha256"] in header

    def test_lock_script_round_trips(self, tmp_path):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import cuda_redist_lock as lockgen
        finally:
            sys.path.remove(str(REPO / "scripts"))

        manifest: dict = {"release_label": "12.9.9"}
        for plat, comps in lockgen.COMPONENTS.items():
            for comp in comps:
                ext = "zip" if plat.startswith("windows") else "tar.xz"
                manifest.setdefault(comp, {"name": f"{comp} name", "version": "1.2.3"})[plat] = {
                    "relative_path": f"{comp}/{plat}/{comp}-{plat}-1.2.3-archive.{ext}",
                    "sha256": "ab" * 32,
                }
        raw = json.dumps(manifest).encode()
        text = lockgen.render("12.9.9", raw)
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert lines[0] == "release 12.9.9"
        assert all(_LOCK_LINE.match(ln) for ln in lines[1:])
        assert len(lines) - 1 == sum(len(c) for c in lockgen.COMPONENTS.values())

        with pytest.raises(SystemExit, match="not '12.9.8'"):
            lockgen.render("12.9.8", raw)
        del manifest["cuda_nvcc"]
        with pytest.raises(SystemExit, match="cuda_nvcc"):
            lockgen.render("12.9.9", json.dumps(manifest).encode())
