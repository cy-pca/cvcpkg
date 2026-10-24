"""A builder job builds its `redistributable: false` deps into its own prefix.

Such a recipe (the NVIDIA CUDA toolkit, NVIDIA's proprietary wheels) is never
in the catalog, and the builder agent used to install a job's deps from the
catalog only -- logging "not found on server (skipping)" -- so any fleet-built
recipe depending on one could not build (torch-cp311-cuda on NVIDIA's wheels).
``builder run`` executes a job against a faked server here, the same harness
as test_builder_reports_identity.py, and the order of what happens to the
job's prefix is checked: catalog deps extracted, then the local-only deps
built from their recipes, dependencies first, then the job packed.
"""

from __future__ import annotations

import io
import json
import signal
import tarfile
import textwrap
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """builder_run installs process-wide SIGINT/SIGTERM handlers."""
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_drain_settle(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _recipe(name: str, deps=(), redistributable=True) -> bytes:
    flag = "" if redistributable else "redistributable: false\n"
    dep_lines = "".join(f"    - name: {d}\n" for d in deps) or "    []\n"
    text = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
        """
    )
    text += f"{flag}depends:\n  runtime:\n{dep_lines}"
    return _tar({"recipe.yaml": text.replace("  runtime:\n    []\n", "  runtime: []\n").encode()})


class _Resp:
    def __init__(self, status: int = 200, data=None, content: bytes = b""):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = content
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


def _fake_server(job, recipes, packages, posted, lookups=None):
    """*recipes*: name -> recipe bundle; *packages*: name -> (status, rows)."""
    handed_out = {"done": False}
    interp_archive = _tar({"bin/interp": b"#!/bin/sh\n"})
    patchelf_archive = _tar({"bin/patchelf": b"#!/bin/sh\n"})

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def post(self, url, json=None, **k):
            verb = url.rstrip("/").rsplit("/", 1)[-1]
            posted.setdefault(verb, []).append(json)
            if verb == "register":
                return _Resp(200, {"id": 7})
            if verb == "claim":
                return _Resp(200, {"id": job["id"], "status": "running"})
            return _Resp(200, {})

        def patch(self, url, **k):
            return _Resp(200, {})

        def get(self, url, **k):
            if "/v1/recipes/" in url:
                name = url.rsplit("/", 1)[-1]
                return _Resp(200, content=recipes[name]) if name in recipes else _Resp(404)
            if "/v1/packages/" in url:
                if lookups is not None:
                    lookups.append(url.rsplit("/", 1)[-1])
                status, rows = packages.get(url.rsplit("/", 1)[-1], (404, []))
                return _Resp(status, {"packages": rows})
            if url.endswith("/v1/download/interp.tar.gz"):
                return _Resp(200, content=interp_archive)
            if url.endswith("/v1/download/patchelf.tar.gz"):
                return _Resp(200, content=patchelf_archive)
            if "/v1/download/" in url:
                return _Resp(404)
            if url.endswith(("/next-job", "/next-claimable")):
                if not handed_out["done"]:
                    handed_out["done"] = True
                    return _Resp(200, job)
                return _Resp(204)
            return _Resp(204)

        def delete(self, url, **k):
            return _Resp(200, {})

    return _Client


_INTERP_ROW = {
    "name": "interp",
    "version": "1.0.0+cvc.1",
    "platform": "linux",
    "arch": "x86_64",
    "build_type": "release",
    "link": "shared",
    "archive_url": "/v1/download/interp.tar.gz",
}


def _run(
    monkeypatch,
    tmp_path,
    recipes,
    packages,
    *,
    build_fails=False,
    job_extra=None,
    at_pack=None,
    lookups=None,
):
    job = {"id": 42, "recipe_name": "app", "platform": "linux", "arch": "x86_64"}
    job.update(job_extra or {})
    posted: dict[str, list[dict]] = {}
    events: list[str] = []
    monkeypatch.setattr(httpx, "Client", _fake_server(job, recipes, packages, posted, lookups))

    def fake_build_recipe(recipe_dir, *, prefix, **k):
        name = Path(recipe_dir).name.split("-")[0]
        has_interp = (Path(prefix) / "bin" / "interp").is_file()
        events.append(f"build {name} (interp in prefix: {has_interp})")
        if build_fails:
            raise RuntimeError("vendor download refused")
        (Path(prefix) / f"{name}.built").write_text("ok")

    def fake_pack(recipe_dir, *, output_dir, prefix, **k):
        events.append("pack with " + ",".join(sorted(p.name for p in Path(prefix).glob("*.built"))))
        if at_pack is not None:
            at_pack["patchelf"] = (Path(prefix) / "bin" / "patchelf").is_file()
        out = Path(output_dir) / "app-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    monkeypatch.setattr("cvcpkg.builder.build_recipe", fake_build_recipe)
    monkeypatch.setattr("cvcpkg.builder.pack_recipe", fake_pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)

    args = [
        "--server",
        "http://test",
        "--token",
        "t",
        "--name",
        "w-local-only",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--no-websocket",
        "--no-auto-capabilities",
        "--no-free-disk",
        "--max-jobs",
        "2",
        "--exit-when-empty",
        "--max-runtime",
        "60",
        "--work-dir",
        str(tmp_path / "wd"),
        "--recipe-cache-dir",
        str(tmp_path / "rc"),
        "--pidfile",
        str(tmp_path / "b.pid"),
    ]
    result = CliRunner().invoke(builder_run, args)
    assert result.exit_code == 0, result.output
    return posted, events, result.output


@pytest.mark.parametrize(
    "absent",
    [(404, []), (200, [])],
    ids=["unknown-to-the-catalog", "every-variant-yanked"],
)
def test_local_only_deps_are_built_into_the_job_prefix_after_catalog_deps(
    monkeypatch, tmp_path, absent
):
    recipes = {
        "app": _recipe("app", ["vendorlib", "interp"]),
        # vendorlib needs vendorbase, both local-only: vendorbase builds first.
        "vendorlib": _recipe("vendorlib", ["vendorbase", "interp"], redistributable=False),
        "vendorbase": _recipe("vendorbase", ["interp"], redistributable=False),
        "interp": _recipe("interp"),
    }
    packages = {"interp": (200, [_INTERP_ROW]), "vendorlib": absent, "vendorbase": absent}
    posted, events, output = _run(monkeypatch, tmp_path, recipes, packages)
    assert events == [
        "build vendorbase (interp in prefix: True)",
        "build vendorlib (interp in prefix: True)",
        "pack with vendorbase.built,vendorlib.built",
    ], output
    assert posted.get("complete"), output
    assert "fail" not in posted, output


def test_an_ordinary_missing_dep_is_still_skipped(monkeypatch, tmp_path):
    recipes = {"app": _recipe("app", ["ghost"]), "ghost": _recipe("ghost")}
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {})
    assert events == ["pack with "], output
    assert posted.get("complete"), output


def test_a_failed_catalog_lookup_fails_the_job(monkeypatch, tmp_path):
    # The server answers an unknown name with 200 and no rows, so an error
    # status is a failed lookup; the job must not build without the dep.
    recipes = {"app": _recipe("app", ["fribidi"]), "fribidi": _recipe("fribidi")}
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {"fribidi": (403, [])})
    assert events == [], output
    assert not posted.get("complete"), output
    [fail] = posted.get("fail") or [None]
    assert fail is not None, output
    assert "dep fribidi: catalog lookup failed (HTTP 403)" in fail["error_message"]


def test_a_failed_dep_download_fails_the_job(monkeypatch, tmp_path):
    row = dict(_INTERP_ROW, name="fribidi", archive_url="/v1/download/fribidi.tar.gz")
    recipes = {"app": _recipe("app", ["fribidi"]), "fribidi": _recipe("fribidi")}
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {"fribidi": (200, [row])})
    assert events == [], output
    assert not posted.get("complete"), output
    [fail] = posted.get("fail") or [None]
    assert fail is not None, output
    assert "dep fribidi: download failed (HTTP 404)" in fail["error_message"]


def test_a_published_variant_still_wins(monkeypatch, tmp_path):
    # Before its bundles are yanked, a non-redistributable dep that IS in the
    # catalog installs from there, matching the client resolver.
    row = dict(_INTERP_ROW, name="vendorlib", archive_url="/v1/download/interp.tar.gz")
    recipes = {
        "app": _recipe("app", ["vendorlib"]),
        "vendorlib": _recipe("vendorlib", redistributable=False),
    }
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {"vendorlib": (200, [row])})
    assert events == ["pack with "], output


def test_a_local_build_failure_fails_the_job(monkeypatch, tmp_path):
    recipes = {
        "app": _recipe("app", ["vendorlib"]),
        "vendorlib": _recipe("vendorlib", redistributable=False),
    }
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {}, build_fails=True)
    assert not posted.get("complete"), output
    [fail] = posted.get("fail") or [None]
    assert fail is not None, output
    assert "vendorlib (redistributable: false) failed to build locally" in fail["error_message"]
    assert "vendor download refused" in fail["error_message"]


_PATCHELF_ROW = dict(
    _INTERP_ROW, name="patchelf", version="0.18.0+cvc.1", archive_url="/v1/download/patchelf.tar.gz"
)


def test_the_job_prefix_gets_cvcpkgs_own_patchelf_before_pack(monkeypatch, tmp_path):
    """Packaging's $ORIGIN pass runs patchelf, found in the job's prefix first.
    It is cvcpkg's own package from the registry -- never the build host's:
    the BSD builders have none, and with none the pass silently did nothing,
    so readline and sqlite failed the relocatable check on NetBSD.  The
    recipe does not declare it."""
    recipes = {"app": _recipe("app")}
    at_pack: dict = {}
    posted, events, output = _run(
        monkeypatch, tmp_path, recipes, {"patchelf": (200, [_PATCHELF_ROW])}, at_pack=at_pack
    )
    assert at_pack == {"patchelf": True}, output
    assert posted.get("complete"), output


def test_a_static_job_does_not_fetch_patchelf(monkeypatch, tmp_path):
    recipes = {"app": _recipe("app")}
    at_pack: dict = {}
    _run(
        monkeypatch,
        tmp_path,
        recipes,
        {"patchelf": (200, [dict(_PATCHELF_ROW, link="static")])},
        job_extra={"link": "static"},
        at_pack=at_pack,
    )
    assert at_pack == {"patchelf": False}


def _recipe_roles(name: str, *, build=(), runtime=(), redistributable=True) -> bytes:
    """A recipe with deps in both the build and runtime roles."""
    flag = "" if redistributable else "redistributable: false\n"

    def block(role, deps):
        if not deps:
            return f"  {role}: []\n"
        return f"  {role}:\n" + "".join(f"    - name: {d}\n" for d in deps)

    text = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
        """
    )
    text += f"{flag}depends:\n" + block("build", build) + block("runtime", runtime)
    return _tar({"recipe.yaml": text.encode()})


def test_a_deps_own_build_deps_are_not_installed(monkeypatch, tmp_path):
    """Only the runtime deps beneath the job's own deps come along, as
    ``cvcpkg install`` resolves them.  A dep's build deps were for building it;
    on Windows they put a second CPython into wheel prefixes (python312 via
    pkg-config's build edge), and its python.exe replaced the wheel's own."""
    recipes = {
        "app": _recipe_roles("app", build=["tool"], runtime=["lib"]),
        "tool": _recipe_roles("tool", build=["tool-src", "otherpython"], runtime=["toollib"]),
        "lib": _recipe_roles("lib", build=["lib-src"], runtime=["zlib"]),
        "toollib": _recipe_roles("toollib"),
        "zlib": _recipe_roles("zlib", build=["zlib-src"]),
    }
    lookups: list[str] = []
    posted, events, output = _run(monkeypatch, tmp_path, recipes, {}, lookups=lookups)
    looked = set(lookups) - {"patchelf"}
    assert looked == {"tool", "lib", "toollib", "zlib"}, (lookups, output)
    assert posted.get("complete"), output


def test_a_local_only_deps_build_deps_still_come_along(monkeypatch, tmp_path):
    """A ``redistributable: false`` dep is built in the job, so its build deps
    are installed for it."""
    recipes = {
        "app": _recipe_roles("app", runtime=["vendorlib"]),
        "vendorlib": _recipe_roles(
            "vendorlib", build=["vendortool"], runtime=["interp"], redistributable=False
        ),
        "vendortool": _recipe_roles("vendortool", build=["vendortool-src"]),
        "interp": _recipe_roles("interp"),
    }
    lookups: list[str] = []
    posted, events, output = _run(
        monkeypatch, tmp_path, recipes, {"interp": (200, [_INTERP_ROW])}, lookups=lookups
    )
    looked = set(lookups) - {"patchelf"}
    assert looked == {"vendorlib", "vendortool", "interp"}, (lookups, output)
    assert events == [
        "build vendorlib (interp in prefix: True)",
        "pack with vendorlib.built",
    ], output
