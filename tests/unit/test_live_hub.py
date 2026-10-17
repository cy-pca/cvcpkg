"""LiveHub: the fan-out behind /v1/live/ws, against fake database reads."""

from __future__ import annotations

import asyncio
import datetime

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")

from cvcpkg.server.live import EVENT_LOOKBACK, QUEUE_MAX, LiveHub, Subscriber  # noqa: E402
from cvcpkg.server.models import BuilderInfo, PackageEvent  # noqa: E402

NOW = datetime.datetime(2026, 10, 17, tzinfo=datetime.timezone.utc)


def _ev(i: int, org: str = "") -> PackageEvent:
    return PackageEvent(id=i, created_at=NOW, kind="publish", name=f"p{i}", org_slug=org)


class FakeDb:
    """Committed events and builders; tests mutate them between ticks."""

    def __init__(self) -> None:
        self.events: list[PackageEvent] = []
        self.builders: list[BuilderInfo] = []
        self.private = {"hush"}

    async def since(self, after: int) -> list[PackageEvent]:
        return sorted((e for e in self.events if e.id > after), key=lambda e: e.id)

    async def latest(self) -> int:
        return max((e.id for e in self.events), default=0)

    async def list_builders(self) -> list[BuilderInfo]:
        return list(self.builders)

    async def private_orgs(self) -> set[str]:
        return self.private


def _hub(db: FakeDb) -> LiveHub:
    # A long interval: these tests drive tick() themselves.
    return LiveHub(
        fetch_events=db.since,
        fetch_latest_id=db.latest,
        fetch_builders=db.list_builders,
        fetch_private_orgs=db.private_orgs,
        poll_interval=3600,
        builders_interval=3600,
    )


def _drain(sub: Subscriber) -> list[dict]:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def _ids(msgs: list[dict]) -> list[int]:
    return [e["id"] for m in msgs if m["type"] == "activity" for e in m["events"]]


def _run(coro):
    return asyncio.run(coro)


def test_a_new_subscriber_gets_only_later_events():
    async def main():
        db = FakeDb()
        db.events = [_ev(1), _ev(2)]
        hub = _hub(db)
        sub = Subscriber(topics=frozenset({"activity"}))
        await hub.subscribe(sub)
        assert _drain(sub) == [{"type": "ready", "topics": ["activity"], "latest_id": 2}]
        db.events.append(_ev(3))
        async with hub._lock:
            await hub.tick()
        assert _ids(_drain(sub)) == [3]
        async with hub._lock:
            await hub.tick()
        assert _drain(sub) == []
        await hub.close()

    _run(main())


def test_an_event_that_commits_late_is_sent_once():
    async def main():
        db = FakeDb()
        hub = _hub(db)
        sub = Subscriber(topics=frozenset({"activity"}))
        await hub.subscribe(sub)
        _drain(sub)
        # id 2 was allocated before 3 but commits after it.
        db.events += [_ev(1), _ev(3)]
        async with hub._lock:
            await hub.tick()
        db.events.append(_ev(2))
        async with hub._lock:
            await hub.tick()
            await hub.tick()
        assert sorted(_ids(_drain(sub))) == [1, 2, 3]
        await hub.close()

    _run(main())


def test_the_cursor_moves_on_past_the_lookback():
    async def main():
        db = FakeDb()
        hub = _hub(db)
        sub = Subscriber(topics=frozenset({"activity"}))
        await hub.subscribe(sub)
        _drain(sub)
        n = EVENT_LOOKBACK * 3
        db.events = [_ev(i) for i in range(1, n + 1)]
        async with hub._lock:
            await hub.tick()
        assert _ids(_drain(sub)) == list(range(1, n + 1))
        assert hub._floor == n - EVENT_LOOKBACK
        assert all(i > hub._floor for i in hub._sent)
        await hub.close()

    _run(main())


def test_private_events_go_to_members_and_admins_only():
    async def main():
        db = FakeDb()
        hub = _hub(db)
        anon = Subscriber(topics=frozenset({"activity"}))
        member = Subscriber(topics=frozenset({"activity"}), member_orgs=frozenset({"hush"}))
        admin = Subscriber(topics=frozenset({"activity"}), is_admin=True)
        for s in (anon, member, admin):
            await hub.subscribe(s)
            _drain(s)
        db.events = [_ev(1, "hush"), _ev(2, "open"), _ev(3)]
        async with hub._lock:
            await hub.tick()
        assert _ids(_drain(anon)) == [2, 3]
        assert _ids(_drain(member)) == [1, 2, 3]
        assert _ids(_drain(admin)) == [1, 2, 3]
        await hub.close()

    _run(main())


def test_a_slow_viewer_is_told_to_resync():
    async def main():
        sub = Subscriber(topics=frozenset({"activity"}))
        for i in range(QUEUE_MAX + 5):
            sub.offer({"type": "activity", "events": [{"id": i}]})
        # The backlog is replaced by one resync; what arrives after it queues
        # behind it (the page dedupes by id once it has reloaded).
        msgs = _drain(sub)
        assert msgs[0] == {"type": "resync"}
        assert [m["events"][0]["id"] for m in msgs[1:]] == list(range(QUEUE_MAX + 1, QUEUE_MAX + 5))

    _run(main())


def _builder(i: int, org: str = "", jobs: int = 0) -> BuilderInfo:
    return BuilderInfo(
        id=i,
        name=f"b{i}",
        org_slug=org,
        platform="linux",
        arch="x86_64",
        current_jobs=jobs,
        created_at=NOW,
    )


def test_builders_snapshot_then_changes_and_removals():
    async def main():
        db = FakeDb()
        db.builders = [_builder(1), _builder(2, "hush")]
        hub = _hub(db)
        anon = Subscriber(topics=frozenset({"builders"}))
        member = Subscriber(topics=frozenset({"builders"}), member_orgs=frozenset({"hush"}))
        await hub.subscribe(anon)
        await hub.subscribe(member)
        snap = _drain(anon)
        assert snap[0]["type"] == "ready"
        assert [b["id"] for b in snap[1]["builders"]] == [1]
        assert [b["id"] for b in _drain(member)[1]["builders"]] == [1, 2]

        # Nothing changed: a poke costs a read, not a message.
        hub.poke("builders")
        async with hub._lock:
            await hub.tick()
        assert _drain(anon) == [] and _drain(member) == []

        db.builders = [_builder(1, jobs=1), _builder(2, "hush", jobs=1)]
        hub.poke("builders")
        async with hub._lock:
            await hub.tick()
        assert [(m["type"], m["builder"]["id"]) for m in _drain(anon)] == [("builder", 1)]
        assert len(_drain(member)) == 2

        db.builders = [_builder(1, jobs=1)]
        hub.poke("builders")
        async with hub._lock:
            await hub.tick()
        assert _drain(anon) == []
        assert _drain(member) == [{"type": "builder_removed", "id": 2}]
        await hub.close()

    _run(main())


def test_the_loop_stops_with_its_last_subscriber():
    async def main():
        db = FakeDb()
        hub = LiveHub(
            fetch_events=db.since,
            fetch_latest_id=db.latest,
            fetch_builders=None,
            fetch_private_orgs=db.private_orgs,
            poll_interval=0.01,
        )
        sub = Subscriber(topics=frozenset({"activity"}))
        await hub.subscribe(sub)
        db.events.append(_ev(1))
        for _ in range(100):
            if 1 in _ids(list(sub.queue._queue)):
                break
            await asyncio.sleep(0.01)
        assert _ids(_drain(sub)) == [1]
        hub.unsubscribe(sub)
        for _ in range(100):
            if hub._task is None or hub._task.done():
                break
            await asyncio.sleep(0.01)
        assert hub._task is None or hub._task.done()
        assert hub._floor is None
        await hub.close()

    _run(main())
