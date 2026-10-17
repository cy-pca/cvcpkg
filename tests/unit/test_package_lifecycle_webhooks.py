"""Webhooks for the whole package lifecycle, not just publishes.

``package.yanked``, ``package.unyanked``, ``package.deleted`` and
``package.nuked`` fire after the change commits, once per package version and
org, naming the bundles that changed.  They are scoped like
``package.published``: an org's events reach only that org's webhooks and
public ones only global webhooks.  A refused or no-op change fires nothing.
"""

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

import cvcpkg.server.app as app_mod  # noqa: E402
from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402

GLOBAL_URL = "https://hooks.example.com/global"
HUSH_URL = "https://hooks.example.com/hush"


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    sent: list[tuple[str, str, dict]] = []

    def record(webhook_id, url, secret, event, payload):
        sent.append((url, event, json.loads(payload)["data"]))

        async def _done():
            return None

        return _done()

    monkeypatch.setattr(app_mod, "_deliver_with_retries", record)
    tokens = {"admin": "admin", "pub": "publisher", "member": "publisher"}
    with server_on(request.param, tmp_path, monkeypatch, tokens) as (client, raw):
        r = client.post(
            "/v1/orgs",
            json={"slug": "hush", "display_name": "Hush", "is_private": True},
            headers=_h(raw["member"]),
        )
        assert r.status_code == 200, r.text
        for url, org in ((GLOBAL_URL, ""), (HUSH_URL, "hush")):
            r = client.post(
                "/v1/webhooks",
                json={"url": url, "events": ["*"], "org_slug": org},
                headers=_h(raw["admin"]),
            )
            assert r.status_code == 200, r.text
        yield client, raw, sent


def _publish(client, tok, name, *, version="1.0+cvc.1", org="", platform="linux", arch="x86_64"):
    params = {
        "name": name,
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "shared",
    }
    if org:
        params["org"] = org
    r = client.post(
        "/v1/publish",
        params=params,
        files={"file": (f"{name}.tar.gz", io.BytesIO(f"{name}{platform}{arch}".encode()))},
        headers=_h(tok),
    )
    assert r.status_code in (200, 201), r.text


def _lifecycle(sent, event=None):
    """Recorded non-publish deliveries, as (url, event, data), optionally of one event."""
    return [s for s in sent if s[1] != "package.published" and (event is None or s[1] == event)]


def test_yank_and_unyank_name_the_variants_they_changed(env):
    client, raw, sent = env
    _publish(client, raw["pub"], "zlib")
    _publish(client, raw["pub"], "zlib", platform="macos", arch="arm64")
    path = "/v1/packages/zlib/1.0+cvc.1"
    assert client.post(
        f"{path}/yank", params={"platform": "linux"}, headers=_h(raw["pub"])
    ).is_success
    assert client.post(f"{path}/yank", headers=_h(raw["pub"])).is_success
    # Everything is yanked already: no change, no webhook.
    assert client.post(f"{path}/yank", headers=_h(raw["pub"])).is_success
    assert client.post(
        f"{path}/unyank", params={"platform": "macos"}, headers=_h(raw["admin"])
    ).is_success

    got = _lifecycle(sent)
    assert [(url, ev, d["variants"], d["actor"]) for url, ev, d in got] == [
        (GLOBAL_URL, "package.yanked", [_variant("linux", "x86_64")], "pub"),
        (GLOBAL_URL, "package.yanked", [_variant("macos", "arm64")], "pub"),
        (GLOBAL_URL, "package.unyanked", [_variant("macos", "arm64")], "admin"),
    ]
    data = got[0][2]
    assert data["name"] == "zlib" and data["version"] == "1.0+cvc.1"
    assert data["org"] == "" and data["count"] == 1


def _variant(platform, arch):
    return {"platform": platform, "arch": arch, "build_type": "release", "link": "shared"}


def test_private_org_events_reach_only_the_org_webhook(env):
    client, raw, sent = env
    _publish(client, raw["member"], "secret", org="hush")
    assert client.post("/v1/packages/secret/1.0+cvc.1/yank", headers=_h(raw["member"])).is_success
    r = client.post("/v1/packages/secret/1.0+cvc.1/nuke", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text

    urls = {url for url, _, _ in sent}
    assert urls == {HUSH_URL}
    assert [ev for _, ev, _ in sent] == ["package.published", "package.yanked", "package.nuked"]
    assert all(d["org"] == "hush" for _, _, d in sent)


def test_chunked_publish_is_scoped_to_its_org(env):
    client, raw, sent = env
    hdrs = _h(raw["member"])
    r = client.post(
        "/v1/upload/init",
        params={
            "name": "chunky",
            "version": "1.0",
            "platform": "linux",
            "arch": "x86_64",
            "org": "hush",
        },
        headers=hdrs,
    )
    assert r.status_code == 201, r.text
    upload_id = r.json()["upload_id"]
    r = client.patch(
        f"/v1/upload/{upload_id}",
        content=b"C" * 2048,
        headers={**hdrs, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/v1/upload/{upload_id}/complete", headers=hdrs)
    assert r.status_code in (200, 201), r.text

    assert [(url, ev) for url, ev, _ in sent] == [(HUSH_URL, "package.published")]


def test_delete_and_nuke_carry_a_reason(env):
    client, raw, sent = env
    _publish(client, raw["pub"], "gone")
    _publish(client, raw["pub"], "doomed")
    r = client.delete("/v1/packages/gone/1.0+cvc.1", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text
    assert client.post("/v1/packages/doomed/1.0+cvc.1/yank", headers=_h(raw["pub"])).is_success
    r = client.post("/v1/packages/doomed/1.0+cvc.1/nuke", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text

    got = [
        (ev, d["name"], d["reason"], d["actor"]) for _, ev, d in _lifecycle(sent) if "reason" in d
    ]
    assert got == [
        ("package.deleted", "gone", "deleted", "admin"),
        ("package.nuked", "doomed", "manual", "admin"),
    ]


def test_refused_changes_fire_nothing(env):
    client, raw, sent = env
    _publish(client, raw["pub"], "live")
    # Nuke refuses a bundle that is not yanked; delete and yank of nothing 404/no-op.
    assert (
        client.post("/v1/packages/live/1.0+cvc.1/nuke", headers=_h(raw["admin"])).status_code == 409
    )
    assert client.delete("/v1/packages/nope/1.0", headers=_h(raw["admin"])).status_code == 404
    assert client.post("/v1/packages/nope/1.0/yank", headers=_h(raw["admin"])).json()["count"] == 0
    assert _lifecycle(sent) == []


def test_cache_gc_and_retention_purge_fire_with_their_reason(env):
    client, raw, sent = env
    _publish(client, raw["pub"], "old", version="1.0")
    _publish(client, raw["pub"], "old", version="2.0")
    # DELETE /v1/cache keeps each coordinate's newest bundle, so 1.0 goes.
    r = client.delete("/v1/cache", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text
    deleted = _lifecycle(sent, "package.deleted")
    assert [(d["name"], d["version"], d["reason"]) for _, _, d in deleted] == [("old", "1.0", "gc")]

    # A real retention purge needs a yank older than the cutoff, which the API
    # cannot make; stand in for the store and check the endpoint's wiring.
    purged = [
        {
            **_variant("linux", "x86_64"),
            "name": "old",
            "version": "2.0",
            "org_slug": "",
            "size_bytes": 0,
            "yanked_at": None,
            "archive_deleted": True,
        }
    ]

    async def fake_purge(**kw):
        return [] if kw.get("dry_run") else purged

    monkeypatch_target = app_mod._db_packages
    original = monkeypatch_target.purge_yanked
    monkeypatch_target.purge_yanked = fake_purge
    try:
        r = client.post(
            "/v1/admin/gc/yanked", params={"older_than_days": 30}, headers=_h(raw["admin"])
        )
        assert r.status_code == 200, r.text
        assert _lifecycle(sent, "package.nuked") == []  # dry run is the default
        r = client.post(
            "/v1/admin/gc/yanked",
            params={"older_than_days": 30, "dry_run": False},
            headers=_h(raw["admin"]),
        )
        assert r.status_code == 200, r.text
    finally:
        monkeypatch_target.purge_yanked = original
    nuked = _lifecycle(sent, "package.nuked")
    assert [(d["name"], d["version"], d["reason"], d["actor"]) for _, _, d in nuked] == [
        ("old", "2.0", "retention", "admin")
    ]
