"""The activity feed: package events, ``GET /v1/activity`` and ``/v1/live/ws``.

Every publish, yank, unyank, delete and nuke records one event per bundle
variant, in the transaction that made the change.  The feed is public except
for a private org's events, which only its members and admins see -- over REST
and over the live socket alike.  The socket also carries builder changes for
the builders page, including the version and host figures builders report.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from starlette.websockets import WebSocketDisconnect  # noqa: E402

import cvcpkg.server.app as app_mod  # noqa: E402
from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402


def _h(tok: str | None) -> dict:
    return {"Authorization": f"Bearer {tok}"} if tok else {}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    # Poll fast so a missed poke shows up as latency, not as a hung test.
    monkeypatch.setattr(app_mod, "_LIVE_POLL_INTERVAL", 0.05)
    tokens = {"admin": "admin", "pub": "publisher", "member": "publisher"}
    with server_on(request.param, tmp_path, monkeypatch, tokens) as (client, raw):
        for slug, private, owner in (("hush", True, "member"), ("open", False, "pub")):
            r = client.post(
                "/v1/orgs",
                json={"slug": slug, "display_name": slug.title(), "is_private": private},
                headers=_h(raw[owner]),
            )
            assert r.status_code == 200, r.text
        yield client, raw


def _publish(client, tok, name, version="1.0+cvc.1", *, org="", platform="linux", arch="x86_64"):
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
    body = f"{name}{version}{platform}{arch}".encode()
    r = client.post(
        "/v1/publish",
        params=params,
        files={"file": (f"{name}.tar.gz", io.BytesIO(body))},
        headers=_h(tok),
    )
    assert r.status_code in (200, 201), r.text


def _activity(client, tok=None, **params) -> dict:
    r = client.get("/v1/activity", params=params, headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _summary(events: list[dict]) -> list[tuple]:
    return [(e["kind"], e["name"], e["platform"], e["actor"]) for e in events]


# ── Recording ───────────────────────────────────────────────────


def test_each_change_is_one_event_per_variant(env):
    client, raw = env
    _publish(client, raw["pub"], "zlib")
    _publish(client, raw["pub"], "zlib", platform="macos", arch="arm64")
    r = client.post("/v1/packages/zlib/1.0+cvc.1/yank", headers=_h(raw["pub"]))
    assert r.status_code == 200, r.text
    # A wider re-yank changes nothing, so it records nothing.
    client.post("/v1/packages/zlib/1.0+cvc.1/yank", headers=_h(raw["pub"]))
    r = client.post(
        "/v1/packages/zlib/1.0+cvc.1/unyank", params={"platform": "macos"}, headers=_h(raw["admin"])
    )
    assert r.status_code == 200, r.text

    assert _summary(_activity(client)["events"]) == [
        ("unyank", "zlib", "macos", "admin"),
        ("yank", "zlib", "macos", "pub"),
        ("yank", "zlib", "linux", "pub"),
        ("publish", "zlib", "macos", "pub"),
        ("publish", "zlib", "linux", "pub"),
    ]
    publish = _activity(client, kind="publish")["events"][0]
    assert publish["size_bytes"] > 0
    assert publish["build_type"] == "release" and publish["link"] == "shared"


def test_delete_and_nuke_are_recorded(env):
    client, raw = env
    _publish(client, raw["pub"], "gone")
    _publish(client, raw["pub"], "doomed")
    assert client.post("/v1/packages/doomed/1.0+cvc.1/yank", headers=_h(raw["pub"])).is_success
    r = client.post("/v1/packages/doomed/1.0+cvc.1/nuke", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text
    r = client.delete("/v1/packages/gone/1.0+cvc.1", headers=_h(raw["admin"]))
    assert r.status_code == 200, r.text

    events = _activity(client, kind=["delete", "nuke"])["events"]
    assert [(e["kind"], e["name"], e["actor"], e["detail"]) for e in events] == [
        ("delete", "gone", "admin", ""),
        ("nuke", "doomed", "admin", "manual"),
    ]


def test_a_refused_change_records_nothing(env):
    client, raw = env
    _publish(client, raw["pub"], "live")
    before = _activity(client)["total"]
    # Nuke refuses a bundle that is not yanked: no tombstone, no event.
    r = client.post("/v1/packages/live/1.0+cvc.1/nuke", headers=_h(raw["admin"]))
    assert r.status_code == 409
    assert _activity(client)["total"] == before


# ── Visibility and filters ──────────────────────────────────────


def test_private_org_events_reach_only_members_and_admins(env):
    client, raw = env
    _publish(client, raw["pub"], "pubpkg")
    _publish(client, raw["pub"], "openpkg", org="open")
    _publish(client, raw["member"], "secret", org="hush")

    def names(tok=None, **params):
        return {e["name"] for e in _activity(client, tok, **params)["events"]}

    assert names() == {"pubpkg", "openpkg"}
    assert names(raw["pub"]) == {"pubpkg", "openpkg"}
    assert names(raw["member"]) == {"pubpkg", "openpkg", "secret"}
    assert names(raw["admin"]) == {"pubpkg", "openpkg", "secret"}
    # Naming the org does not get around it.
    assert _activity(client, org="hush")["total"] == 0
    assert names(raw["member"], org="hush") == {"secret"}
    assert names(org="") == {"pubpkg"}


def test_filters(env):
    client, raw = env
    _publish(client, raw["pub"], "libfoo")
    _publish(client, raw["pub"], "libfoo", platform="windows")
    _publish(client, raw["pub"], "libbar", platform="macos", arch="arm64")
    _publish(client, raw["admin"], "lib_baz")
    client.post("/v1/packages/libbar/1.0+cvc.1/yank", headers=_h(raw["pub"]))

    def pick(**params):
        return sorted(
            (e["kind"], e["name"], e["platform"]) for e in _activity(client, **params)["events"]
        )

    assert pick(name="FOO") == [("publish", "libfoo", "linux"), ("publish", "libfoo", "windows")]
    assert pick(kind="yank") == [("yank", "libbar", "macos")]
    assert pick(platform="windows") == [("publish", "libfoo", "windows")]
    assert pick(arch="arm64") == [("publish", "libbar", "macos"), ("yank", "libbar", "macos")]
    assert pick(actor="adm") == [("publish", "lib_baz", "linux")]
    # LIKE wildcards in the filter are literal characters.
    assert pick(name="b_") == [("publish", "lib_baz", "linux")]
    assert client.get("/v1/activity", params={"kind": "bogus"}).status_code == 422


def test_paging_through_a_snapshot_is_stable(env):
    client, raw = env
    for i in range(5):
        _publish(client, raw["pub"], f"pkg{i}")
    first = _activity(client, limit=2)
    assert first["total"] == 5
    assert [e["name"] for e in first["events"]] == ["pkg4", "pkg3"]

    _publish(client, raw["pub"], "late")
    page2 = _activity(client, limit=2, offset=2, max_id=first["max_id"])
    assert page2["total"] == 5
    assert [e["name"] for e in page2["events"]] == ["pkg2", "pkg1"]
    assert _activity(client, limit=2)["total"] == 6


# ── Live socket ─────────────────────────────────────────────────


def _hello(ws, topics, tok=None) -> dict:
    msg = {"type": "hello", "topics": topics}
    if tok:
        msg["token"] = tok
    ws.send_json(msg)
    ready = ws.receive_json()
    assert ready["type"] == "ready", ready
    return ready


def test_live_activity_pushes_what_the_viewer_may_see(env):
    client, raw = env
    _publish(client, raw["pub"], "before")
    with (
        client.websocket_connect("/v1/live/ws") as anon,
        client.websocket_connect("/v1/live/ws") as member,
    ):
        ready = _hello(anon, ["activity"])
        assert ready["latest_id"] == _activity(client)["max_id"]
        _hello(member, ["activity"], raw["member"])

        _publish(client, raw["member"], "secret", org="hush")
        got = member.receive_json()
        assert got["type"] == "activity"
        assert [e["name"] for e in got["events"]] == ["secret"]

        _publish(client, raw["pub"], "public")
        got = anon.receive_json()
        # The private event never reached the anonymous socket: the first
        # message it gets is the public publish, alone.
        assert [e["name"] for e in got["events"]] == ["public"]
        assert got["events"][0]["kind"] == "publish"
        assert [e["name"] for e in member.receive_json()["events"]] == ["public"]


def test_live_socket_refuses_bad_hellos(env):
    client, raw = env
    for hello, code in (
        ({"type": "hello", "topics": ["activity"], "token": "cvctok_nope"}, 4001),
        ({"type": "hello", "topics": ["weather"]}, 4000),
        ({"type": "subscribe"}, 4000),
    ):
        with client.websocket_connect("/v1/live/ws") as ws:
            ws.send_json(hello)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == code, hello


def test_live_builders_snapshot_and_changes(env):
    client, raw = env
    r = client.post(
        "/v1/builders/register",
        json={"name": "pub-b", "platform": "linux", "arch": "x86_64", "labels": ["cvcpkg=9.9.9"]},
        headers=_h(raw["pub"]),
    )
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    r = client.post(
        "/v1/builders/register",
        json={"name": "hush-b", "platform": "linux", "arch": "x86_64", "org_slug": "hush"},
        headers=_h(raw["member"]),
    )
    assert r.status_code == 200, r.text
    hush_id = r.json()["id"]

    with client.websocket_connect("/v1/live/ws") as ws:
        _hello(ws, ["builders"])
        snap = ws.receive_json()
        assert snap["type"] == "builders"
        assert [b["name"] for b in snap["builders"]] == ["pub-b"]
        assert snap["builders"][0]["version"] == "9.9.9"

        # A private builder's beat is not news to an outsider; a public one's is.
        client.post(f"/v1/builders/{hush_id}/heartbeat", json={}, headers=_h(raw["member"]))
        r = client.post(
            f"/v1/builders/{bid}/heartbeat",
            json={"current_jobs": 0, "system": {"cpu_percent": 42.5, "cpu_count": 8}},
            headers=_h(raw["pub"]),
        )
        assert r.status_code == 200, r.text
        got = ws.receive_json()
        assert got["type"] == "builder"
        assert got["builder"]["id"] == bid
        assert got["builder"]["system"]["cpu_percent"] == 42.5

        assert client.delete(f"/v1/builders/{bid}", headers=_h(raw["admin"])).is_success
        assert ws.receive_json() == {"type": "builder_removed", "id": bid}


# ── Builder version and host figures ────────────────────────────


def test_builder_reports_version_and_system_stats(env):
    client, raw = env
    r = client.post(
        "/v1/builders/register",
        json={
            "name": "b1",
            "platform": "linux",
            "arch": "x86_64",
            "labels": ["gpu", "cvcpkg=2.5.1@abc123"],
            "system": {"cpu_count": 4, "mem_total_bytes": 1 << 30, "junk": "dropped"},
        },
        headers=_h(raw["pub"]),
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["version"] == "2.5.1@abc123"
    assert b["system"]["cpu_count"] == 4
    assert "junk" not in b["system"]

    stats = {"cpu_percent": 12.5, "disk_free_bytes": 10, "disk_total_bytes": 100}
    r = client.post(
        f"/v1/builders/{b['id']}/heartbeat", json={"system": stats}, headers=_h(raw["pub"])
    )
    assert r.status_code == 200, r.text
    # A beat without figures keeps the last ones.
    client.post(f"/v1/builders/{b['id']}/heartbeat", json={}, headers=_h(raw["pub"]))
    got = client.get(f"/v1/builders/{b['id']}").json()
    assert got["system"]["cpu_percent"] == 12.5 and got["system"]["disk_total_bytes"] == 100

    # Out-of-range figures are refused over REST...
    r = client.post(
        f"/v1/builders/{b['id']}/heartbeat",
        json={"system": {"cpu_percent": 900}},
        headers=_h(raw["pub"]),
    )
    assert r.status_code == 422
    # ...and dropped, not fatal, over the builder socket.
    with client.websocket_connect(f"/v1/builders/{b['id']}/ws", headers=_h(raw["pub"])) as ws:
        ws.send_json({"type": "heartbeat", "current_jobs": 0, "system": {"cpu_percent": 900}})
        assert ws.receive_json()["type"] == "heartbeat_ack"
        ws.send_json({"type": "heartbeat", "current_jobs": 0, "system": {"cpu_percent": 7}})
        assert ws.receive_json()["type"] == "heartbeat_ack"
    system = client.get(f"/v1/builders/{b['id']}").json()["system"]
    assert {k: v for k, v in system.items() if v is not None} == {"cpu_percent": 7.0}


def test_a_builder_without_a_version_label_has_none(env):
    client, raw = env
    r = client.post(
        "/v1/builders/register",
        json={"name": "old", "platform": "linux", "arch": "x86_64", "labels": ["gpu"]},
        headers=_h(raw["pub"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] is None
    assert r.json()["system"] is None


def test_pages_render(env):
    client, _ = env
    for path, needle in (("/activity", "openLive(['activity']"), ("/builders", "builder-modal")):
        r = client.get(path)
        assert r.status_code == 200
        assert needle in r.text
    assert 'href="/activity"' in client.get("/builders").text
