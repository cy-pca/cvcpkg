# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the package page's recipe section endpoints.

``/v1/recipe/{name}`` used to read only the repo-vendored ``recipes/``
directory, so the package page showed no recipe at all for anything pushed
after release or owned by an org.  These cover the replacement: the file
listing, per-file reads, and the downloadable archive.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole

# 1x1 transparent GIF — a stand-in for recipe media.
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

RECIPE_YAML = """\
schema_version: 1
recipe:
  name: widget
  upstream_version: "1.2.3"
  cvc_revision: 1
build:
  matrix:
    - platform: linux
      script: build.sh
"""


@pytest.fixture()
def recipes_root(tmp_path, monkeypatch):
    """A recipes/ tree with one recipe, a script, media, and a _common sibling."""
    root = tmp_path / "recipes"
    rdir = root / "widget"
    rdir.mkdir(parents=True)
    # write_bytes, not write_text: on Windows text mode rewrites \n to \r\n,
    # which would make the byte-for-byte archive comparison below fail for a
    # reason that has nothing to do with the server.
    (rdir / "recipe.yaml").write_bytes(RECIPE_YAML.encode())
    (rdir / "build.sh").write_bytes(b"#!/bin/sh\necho building widget\n")
    (rdir / "fix-config.patch").write_bytes(b"--- a/x\n+++ b/x\n")
    (rdir / "diagram.gif").write_bytes(_GIF)
    common = root / "_common"
    common.mkdir()
    (common / "env-linux.sh").write_bytes(b"export CC=cc\n")

    import cvcpkg.builder as builder_mod

    monkeypatch.setattr(builder_mod, "find_recipes_dir", lambda *a, **k: root)
    return root


@pytest.fixture()
def client(tmp_path, recipes_root):
    state = tmp_path / "state"
    state.mkdir()
    TokenStore(state).create("admin", TokenRole.admin)
    with TestClient(create_app(state_dir=state)) as c:
        yield c


class TestRecipeYaml:
    def test_serves_recipe_yaml(self, client):
        resp = client.get("/v1/recipe/widget")
        assert resp.status_code == 200
        assert "upstream_version" in resp.text

    def test_unknown_recipe_404(self, client):
        assert client.get("/v1/recipe/nosuch").status_code == 404

    @pytest.mark.parametrize("bad", ["../etc", ".hidden", "a/b"])
    def test_rejects_traversal(self, client, bad):
        # A traversing name must never reach the filesystem: 400 (rejected by
        # the name pattern) or 404 (no route match), never 200.
        assert client.get(f"/v1/recipe/{bad}").status_code in (400, 404)


class TestRecipeFileListing:
    def test_lists_every_artifact(self, client):
        data = client.get("/v1/recipe/widget/files").json()
        names = {f["name"] for f in data["files"]}
        assert {"recipe.yaml", "build.sh", "fix-config.patch", "diagram.gif"} <= names
        # The shared helper is included, and flagged as shared rather than
        # pretending to be part of this recipe's own directory.
        shared = [f for f in data["files"] if f["shared"]]
        assert [f["name"] for f in shared] == ["_common/env-linux.sh"]

    def test_classifies_kinds(self, client):
        by_name = {f["name"]: f for f in client.get("/v1/recipe/widget/files").json()["files"]}
        assert by_name["recipe.yaml"]["kind"] == "recipe"
        assert by_name["build.sh"]["kind"] == "script"
        assert by_name["fix-config.patch"]["kind"] == "patch"
        assert by_name["diagram.gif"]["kind"] == "image"
        assert by_name["diagram.gif"]["media_type"] == "image/gif"


class TestRecipeFileContent:
    def test_reads_a_script(self, client):
        resp = client.get("/v1/recipe/widget/file", params={"path": "widget/build.sh"})
        assert resp.status_code == 200
        assert "echo building widget" in resp.text

    def test_reads_shared_helper(self, client):
        resp = client.get("/v1/recipe/widget/file", params={"path": "_common/env-linux.sh"})
        assert resp.status_code == 200
        assert "export CC=cc" in resp.text

    def test_image_served_with_media_type(self, client):
        resp = client.get("/v1/recipe/widget/file", params={"path": "widget/diagram.gif"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/gif")
        assert resp.content == _GIF

    def test_path_outside_recipe_rejected(self, client):
        # Only paths the listing actually offered are readable, so a made-up
        # one is a miss rather than a filesystem read.
        resp = client.get("/v1/recipe/widget/file", params={"path": "../../etc/passwd"})
        assert resp.status_code == 404


class TestRecipeArchive:
    def test_targz_extracts_to_a_usable_recipe_dir(self, client):
        resp = client.get("/v1/recipe/widget/archive")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"
        assert "widget-recipe.tar.gz" in resp.headers["content-disposition"]

        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            names = set(tar.getnames())
            # The whole point: extracting into recipes/ yields recipes/widget/…
            assert "widget/recipe.yaml" in names
            assert "widget/build.sh" in names
            assert "_common/env-linux.sh" in names
            body = tar.extractfile("widget/recipe.yaml").read().decode()
            assert body == RECIPE_YAML
            # Build scripts have to survive the round trip executable.
            assert tar.getmember("widget/build.sh").mode & 0o111

    def test_zip_format(self, client):
        resp = client.get("/v1/recipe/widget/archive", params={"format": "zip"})
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert "widget/recipe.yaml" in zf.namelist()

    def test_rejects_unknown_format(self, client):
        assert client.get("/v1/recipe/widget/archive", params={"format": "rar"}).status_code == 422

    def test_unknown_recipe_404(self, client):
        assert client.get("/v1/recipe/nosuch/archive").status_code == 404


class TestOrgScope:
    def test_org_recipe_not_served_from_repo_recipes(self, client):
        """An org query must not fall through to the public vendored dir.

        Otherwise ``/v1/recipe/widget?org=acme`` would happily serve the
        public 'widget' recipe as if it belonged to acme.
        """
        assert client.get("/v1/recipe/widget", params={"org": "acme"}).status_code == 404
        assert client.get("/v1/recipe/widget/files", params={"org": "acme"}).status_code == 404
