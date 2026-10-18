"""Public builds, build logs and recipes, and their live updates.

Build jobs, their logs and the recipe list used to need a publisher token, so
the builds, build-detail and recipes pages showed an auth prompt to everyone
else.  They are public reads now, under the rule the builders and packages
already follow: anything not in a private org is visible to anyone, anonymous
callers included; a private org's jobs and recipes only to its members and
admins.  The pages update over ``/v1/live/ws``: the ``builds`` topic pushes job
changes, and a hello's ``log`` tails one job's log.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

import cvcpkg.server.app as app_mod  # noqa: E402
from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402


def _h(tok: str | None) -> dict:
    return {"Authorization": f"Bearer {tok}"} if tok else {}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_LIVE_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(app_mod, "_LIVE_LOG_POLL_SECONDS", 0.05)
    tokens = {"admin": "admin", "pub": "publisher", "member": "publisher", "reader": "reader"}
    with server_on(request.param, tmp_path, monkeypatch, tokens) as (client, raw):
        r = client.post(
            "/v1/orgs",
            json={"slug": "hush", "display_name": "Hush", "is_private": True},
            headers=_h(raw["member"]),
        )
        assert r.status_code == 200, r.text
        yield client, raw


def _submit(client, tok, recipe, org=""):
    body = {"recipe_name": recipe, "platform": "linux", "arch": "x86_64"}
    if org:
        body["org_slug"] = org
    r = client.post("/v1/builds", json=body, headers=_h(tok))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _append_log(client, tok, job_id, text):
    r = client.patch(f"/v1/builds/{job_id}/log", json={"data": text}, headers=_h(tok))
    assert r.status_code == 200, r.text


def _recipe_bundle(name: str) -> bytes:
    files = {
        f"{name}/recipe.yaml": (
            "schema_version: 1\n"
            f'recipe:\n  name: {name}\n  upstream_version: "1.0.0"\n  cvc_revision: 1\n'
            "  license: MIT\n"
            "build:\n  matrix:\n    - platform: linux\n      script: build.sh\n"
        ),
        f"{name}/build.sh": "#!/bin/sh\necho build\n",
        f"{name}/patches/fix.patch": "--- a\n+++ b\n",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _push(client, tok, name, org=""):
    return client.post(
        f"/v1/recipes/{name}",
        params={"org_slug": org} if org else None,
        files={"file": (f"{name}.tar.gz", _recipe_bundle(name), "application/gzip")},
        headers=_h(tok),
    )


# ── Builds and logs ─────────────────────────────────────────────


def test_builds_are_public_except_a_private_orgs(env):
    client, raw = env
    public = _submit(client, raw["pub"], "zlib")
    private = _submit(client, raw["member"], "secret", org="hush")

    def ids(tok=None):
        r = client.get("/v1/builds", headers=_h(tok))
        assert r.status_code == 200, r.text
        return {j["id"] for j in r.json()["jobs"]}

    assert ids() == {public}
    assert ids(raw["reader"]) == {public}
    assert ids(raw["member"]) == {public, private}
    assert ids(raw["admin"]) == {public, private}

    assert client.get(f"/v1/builds/{public}").status_code == 200
    assert client.get(f"/v1/builds/{private}").status_code == 404
    assert client.get(f"/v1/builds/{private}", headers=_h(raw["member"])).status_code == 200


def test_build_logs_are_public_except_a_private_orgs(env):
    client, raw = env
    public = _submit(client, raw["pub"], "zlib")
    private = _submit(client, raw["member"], "secret", org="hush")
    _append_log(client, raw["pub"], public, "compiling zlib\n")
    _append_log(client, raw["member"], private, "compiling secret\n")

    r = client.get(f"/v1/builds/{public}/log")
    assert r.status_code == 200 and "compiling zlib" in r.text
    assert client.get(f"/v1/builds/{private}/log").status_code == 404
    r = client.get(f"/v1/builds/{private}/log", headers=_h(raw["member"]))
    assert r.status_code == 200 and "compiling secret" in r.text


def test_writes_still_need_a_publisher(env):
    client, raw = env
    job = _submit(client, raw["pub"], "zlib")
    assert client.post(f"/v1/builds/{job}/cancel").status_code == 401
    assert client.post(f"/v1/builds/{job}/cancel", headers=_h(raw["reader"])).status_code == 403
    r = client.post(
        "/v1/builds", json={"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"}
    )
    assert r.status_code == 401


# ── Recipes ─────────────────────────────────────────────────────


def test_recipe_list_is_public_except_a_private_orgs(env):
    client, raw = env
    assert _push(client, raw["pub"], "openrecipe").status_code in (200, 201)
    assert _push(client, raw["member"], "hushrecipe", org="hush").status_code in (200, 201)

    def names(tok=None):
        r = client.get("/v1/recipes", headers=_h(tok))
        assert r.status_code == 200, r.text
        return {x["name"] for x in r.json()["recipes"]}

    assert "openrecipe" in names() and "hushrecipe" not in names()
    assert "hushrecipe" in names(raw["member"])
    assert "hushrecipe" not in names(raw["pub"])


def test_a_pushed_recipe_archive_is_a_recipes_dir(env, tmp_path):
    """The package page's download extracts into a recipes dir, files intact."""
    client, raw = env
    assert _push(client, raw["pub"], "widget").status_code in (200, 201)

    r = client.get("/v1/recipe/widget/archive")
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(r.content))
    dest = tmp_path / "recipes"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    assert (dest / "widget" / "build.sh").stat().st_mode & 0o111
    assert (dest / "widget" / "patches" / "fix.patch").is_file()
    # Byte-for-byte what was pushed: the recipe dir round-trips.
    with tarfile.open(fileobj=io.BytesIO(_recipe_bundle("widget")), mode="r:gz") as pushed:
        for member in pushed.getmembers():
            assert (dest / member.name).read_bytes() == pushed.extractfile(member).read()

    z = client.get("/v1/recipe/widget/archive", params={"format": "zip"})
    assert z.status_code == 200
    assert "widget/recipe.yaml" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()


def test_a_server_recipe_archive_brings_its_shared_helpers(env, tmp_path):
    """A recipe from the server's own tree comes with the _common/ it sources."""
    from cvcpkg.builder import list_recipes

    client, _raw = env
    r = client.get("/v1/recipe/zlib/archive")
    assert r.status_code == 200
    dest = tmp_path / "recipes"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
        tar.extractall(dest, filter="data")
    assert "zlib/recipe.yaml" in names
    assert any(n.startswith("_common/") for n in names)
    assert [x.name for x in list_recipes(dest)] == ["zlib"]


def test_a_private_orgs_recipe_archive_is_hidden(env):
    client, raw = env
    assert _push(client, raw["member"], "hushrecipe", org="hush").status_code in (200, 201)
    assert client.get("/v1/recipe/hushrecipe/archive", params={"org": "hush"}).status_code == 404
    r = client.get(
        "/v1/recipe/hushrecipe/archive", params={"org": "hush"}, headers=_h(raw["member"])
    )
    assert r.status_code == 200


# ── Live: builds topic and log tail ─────────────────────────────


def _hello(ws, topics, tok=None, log=None):
    msg = {"type": "hello", "topics": topics}
    if tok:
        msg["token"] = tok
    if log:
        msg["log"] = log
    ws.send_json(msg)
    ready = ws.receive_json()
    assert ready["type"] == "ready", ready
    return ready


def test_live_builds_push_job_changes_to_who_may_see_them(env):
    client, raw = env
    with (
        client.websocket_connect("/v1/live/ws") as anon,
        client.websocket_connect("/v1/live/ws") as member,
    ):
        _hello(anon, ["builds"])
        _hello(member, ["builds"], raw["member"])

        private = _submit(client, raw["member"], "secret", org="hush")
        got = member.receive_json()
        assert got["type"] == "builds"
        assert [j["id"] for j in got["jobs"]] == [private]

        public = _submit(client, raw["pub"], "zlib")
        got = anon.receive_json()
        # The private job never reached the anonymous socket.
        assert [j["id"] for j in got["jobs"]] == [public]
        assert got["jobs"][0]["status"] == "pending"

        assert client.post(f"/v1/builds/{public}/cancel", headers=_h(raw["pub"])).is_success
        got = anon.receive_json()
        assert [(j["id"], j["status"]) for j in got["jobs"]] == [(public, "cancelled")]


def test_live_log_tail_streams_until_the_job_ends(env):
    client, raw = env
    job = _submit(client, raw["pub"], "zlib")
    _append_log(client, raw["pub"], job, "step 1\n")
    with client.websocket_connect("/v1/live/ws") as ws:
        _hello(ws, [], log={"job": job, "offset": 0})
        first = ws.receive_json()
        assert first == {"type": "log", "job": job, "offset": 0, "next": 7, "data": "step 1\n"}

        _append_log(client, raw["pub"], job, "step 2 ✓\n")
        second = ws.receive_json()
        assert second["offset"] == 7 and second["data"] == "step 2 ✓\n"

        assert client.post(f"/v1/builds/{job}/cancel", headers=_h(raw["pub"])).is_success
        assert ws.receive_json() == {"type": "log_end", "job": job, "status": "cancelled"}


def test_live_log_tail_resumes_from_an_offset(env):
    client, raw = env
    job = _submit(client, raw["pub"], "zlib")
    _append_log(client, raw["pub"], job, "aaaa\nbbbb\n")
    assert client.post(f"/v1/builds/{job}/cancel", headers=_h(raw["pub"])).is_success
    with client.websocket_connect("/v1/live/ws") as ws:
        _hello(ws, [], log={"job": job, "offset": 5})
        assert ws.receive_json()["data"] == "bbbb\n"
        assert ws.receive_json()["type"] == "log_end"


def test_live_log_tail_of_a_private_job_needs_membership(env):
    client, raw = env
    job = _submit(client, raw["member"], "secret", org="hush")
    _append_log(client, raw["member"], job, "classified\n")
    with client.websocket_connect("/v1/live/ws") as ws:
        _hello(ws, [], log={"job": job, "offset": 0})
        assert ws.receive_json() == {"type": "log_end", "job": job, "status": "not_found"}
    with client.websocket_connect("/v1/live/ws") as ws:
        _hello(ws, [], raw["member"], log={"job": job, "offset": 0})
        assert ws.receive_json()["data"] == "classified\n"


def test_job_rows_record_when_they_changed(env):
    client, raw = env
    job = _submit(client, raw["pub"], "zlib")
    before = client.get(f"/v1/builds/{job}").json()["updated_at"]
    assert before
    assert client.post(f"/v1/builds/{job}/cancel", headers=_h(raw["pub"])).is_success
    after = client.get(f"/v1/builds/{job}").json()["updated_at"]
    assert after > before


def test_pages_work_without_a_token(env):
    client, _raw = env
    for path, needle in (
        ("/builds", "openLive(['builds']"),
        ("/build/1", "startLive()"),
        ("/recipes", "/v1/recipes?limit=1000"),
        ("/package/zlib", "recipe-howto"),
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert needle in r.text, path
    assert "EventSource" not in client.get("/build/1").text
