"""Publish metadata that grows with the bundle can travel in the request body.

wezterm-crates declares 645 runtime deps.  Sent as the ``required_deps`` query
parameter they make a ~50 KB request line, which cvcpkg.org's front end refuses
with 414 before the app sees it, so the fleet builder logged only "publish
completed with 1 error(s)" (job 24063, 2026-10-27).  The server now also takes
``required_deps`` / ``provides`` as multipart fields on ``/v1/publish`` and as a
JSON body on ``/v1/upload/init``, and advertises that in ``/healthz``.  The
client moves them there only when the query would be too long, and only to a
server that advertises it: an older server would ignore the body and record no
deps.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from unittest import mock

import click
import pytest

from cvcpkg.cli._publish import (
    _MAX_PUBLISH_QUERY_BYTES,
    PUBLISH_METADATA_BODY_FEATURE,
    _publish_chunked,
    _publish_simple,
    _split_publish_metadata,
)

MANY_DEPS = [{"name": f"crate-some-long-crate-name--{i}-0-0"} for i in range(700)]


def _params(name: str, deps: list[dict], provides: list[str] | None = None) -> dict:
    return {
        "name": name,
        "version": "1.0.0+cvc.1",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "required_deps": json.dumps(deps),
        "provides": json.dumps(provides or []),
    }


# ── Client: when the metadata moves ─────────────────────────────


class TestSplitPublishMetadata:
    def test_short_metadata_stays_in_the_query_without_asking_the_server(self):
        params = _params("small", [{"name": "zlib"}])
        with mock.patch("cvcpkg.cli._publish._server_features") as features:
            assert _split_publish_metadata("https://pkg.example.com", params) == (params, {})
        features.assert_not_called()

    def test_long_metadata_moves_to_the_body_when_advertised(self):
        params = _params("big", MANY_DEPS, ["slot"])
        with mock.patch(
            "cvcpkg.cli._publish._server_features",
            return_value={PUBLISH_METADATA_BODY_FEATURE},
        ):
            query, body = _split_publish_metadata("https://pkg.example.com", params)
        assert "required_deps" not in query and "provides" not in query
        assert query["name"] == "big"
        assert json.loads(body["required_deps"]) == MANY_DEPS
        assert json.loads(body["provides"]) == ["slot"]

    def test_long_metadata_to_an_older_server_fails_instead_of_dropping_deps(self):
        params = _params("big", MANY_DEPS)
        with (
            mock.patch("cvcpkg.cli._publish._server_features", return_value=set()),
            pytest.raises(click.ClickException, match=PUBLISH_METADATA_BODY_FEATURE),
        ):
            _split_publish_metadata("https://pkg.example.com", params)

    def test_the_wezterm_crates_case_is_over_the_limit(self):
        from urllib.parse import urlencode

        assert len(urlencode(_params("wezterm-crates", MANY_DEPS))) > _MAX_PUBLISH_QUERY_BYTES


# ── Server ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server_env(tmp_path_factory):
    pytest.importorskip("fastapi", reason="server extras not installed")
    pytest.importorskip("aiosqlite", reason="aiosqlite required")
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.models import TokenRole

    tmp_path = tmp_path_factory.mktemp("metadata_body")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
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
            yield client, {"Authorization": f"Bearer {admin_token}"}
    finally:
        if prev_db is None:
            os.environ.pop("CVCPKG_DATABASE_URL", None)
        else:
            os.environ["CVCPKG_DATABASE_URL"] = prev_db
        if prev_mirror is not None:
            os.environ["CVCPKG_MIRROR_MODE"] = prev_mirror


def _row(client, name: str) -> dict:
    return client.get(f"/v1/packages/{name}").json()["packages"][0]


class TestServer:
    def test_healthz_advertises_the_feature(self, server_env):
        client, _ = server_env
        assert PUBLISH_METADATA_BODY_FEATURE in client.get("/healthz").json()["features"]

    def test_publish_takes_the_lists_as_multipart_fields(self, server_env):
        client, hdr = server_env
        params = _params("form-deps", MANY_DEPS, ["form-slot"])
        query = {k: v for k, v in params.items() if k not in ("required_deps", "provides")}
        resp = client.post(
            "/v1/publish",
            params=query,
            data={"required_deps": params["required_deps"], "provides": params["provides"]},
            files={"file": ("form-deps.tar.zst", io.BytesIO(b"payload"))},
            headers=hdr,
        )
        assert resp.status_code == 200, resp.text
        row = _row(client, "form-deps")
        assert row["required_deps"] == MANY_DEPS
        assert row["provides"] == ["form-slot"]

    def test_the_multipart_field_wins_over_the_query(self, server_env):
        client, hdr = server_env
        resp = client.post(
            "/v1/publish",
            params=_params("both-deps", [{"name": "from-query"}]),
            data={"required_deps": json.dumps([{"name": "from-body"}])},
            files={"file": ("both-deps.tar.zst", io.BytesIO(b"payload"))},
            headers=hdr,
        )
        assert resp.status_code == 200, resp.text
        assert _row(client, "both-deps")["required_deps"] == [{"name": "from-body"}]

    def test_query_only_publishes_still_work(self, server_env):
        client, hdr = server_env
        resp = client.post(
            "/v1/publish",
            params=_params("query-deps", [{"name": "zlib"}]),
            files={"file": ("query-deps.tar.zst", io.BytesIO(b"payload"))},
            headers=hdr,
        )
        assert resp.status_code == 200, resp.text
        assert _row(client, "query-deps")["required_deps"] == [{"name": "zlib"}]

    @pytest.mark.parametrize("bad", ["not json", '{"name": "x"}', '"zlib"'])
    def test_a_value_that_is_not_a_json_list_is_a_422(self, server_env, bad):
        client, hdr = server_env
        resp = client.post(
            "/v1/publish",
            params={**_params("bad-deps", []), "required_deps": bad},
            files={"file": ("bad-deps.tar.zst", io.BytesIO(b"payload"))},
            headers=hdr,
        )
        assert resp.status_code == 422, resp.text
        assert "required_deps" in resp.json()["detail"]
        assert client.get("/v1/packages/bad-deps").json()["total"] == 0

    def test_upload_init_takes_the_lists_as_a_json_body(self, server_env):
        client, hdr = server_env
        params = _params("chunked-deps", [])
        query = {k: v for k, v in params.items() if k not in ("required_deps", "provides")}
        resp = client.post(
            "/v1/upload/init",
            params={**query, "total_size": 7},
            json={"required_deps": MANY_DEPS, "provides": ["chunk-slot"]},
            headers=hdr,
        )
        assert resp.status_code == 201, resp.text
        upload_id = resp.json()["upload_id"]
        content = b"payload"
        resp = client.patch(
            f"/v1/upload/{upload_id}",
            content=content,
            headers={**hdr, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": hashlib.sha256(content).hexdigest()},
            headers=hdr,
        )
        assert resp.status_code == 200, resp.text
        row = _row(client, "chunked-deps")
        assert row["required_deps"] == MANY_DEPS
        assert row["provides"] == ["chunk-slot"]


# ── Client against the server ───────────────────────────────────


class _Borrowed:
    """``httpx.Client(...)`` stand-in that hands out the TestClient without closing it."""

    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("upload", [_publish_simple, _publish_chunked])
def test_client_publishes_a_bundle_with_hundreds_of_deps(server_env, tmp_path, upload):
    client, hdr = server_env
    name = f"many-deps-{upload.__name__.strip('_')}"
    archive = tmp_path / f"{name}.tar.zst"
    archive.write_bytes(b"payload")
    params = _params(name, MANY_DEPS)
    args = (archive.stat().st_size,) if upload is _publish_chunked else ()
    with mock.patch("httpx.Client", side_effect=lambda **kw: _Borrowed(client)):
        assert upload("http://testserver", hdr, params, archive, *args) == "published"
    assert _row(client, name)["required_deps"] == MANY_DEPS
