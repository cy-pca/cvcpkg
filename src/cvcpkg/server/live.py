# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Live updates for browser pages: the activity feed and the builders page.

A page opens ``/v1/live/ws`` and sends a hello naming its topics (and, to see
private orgs, its token).  The hub then pushes JSON messages:

* ``{"type": "ready", "topics": [...], "latest_id": N}`` once subscribed.
  ``latest_id`` is the newest package event; a page that loaded an older
  snapshot re-fetches it.
* ``{"type": "activity", "events": [...]}``: new package events (``activity``).
* ``{"type": "builders", "builders": [...]}``: every builder the viewer may see,
  sent once on subscribe (``builders``).
* ``{"type": "builder", "builder": {...}}``: one builder changed.
* ``{"type": "builder_removed", "id": N}``: one builder was unregistered.
* ``{"type": "builds", "jobs": [...]}``: build jobs that changed (``builds``).
* ``{"type": "log", "job": N, "offset": B, "data": "..."}``,
  ``{"type": "log_end", "job": N, "status": "..."}``: a build log tailed for
  this connection alone (the hello's ``log``; see the endpoint).
* ``{"type": "resync"}``: the viewer fell too far behind and messages were
  dropped; reload from the REST endpoints.

The hub reads the database only while someone is subscribed: package events by
id (a primary-key range) and the builders table.  A local change calls
:meth:`LiveHub.poke` to wake it at once; the poll interval bounds the delay for
changes it was not told about (another worker, the reaper, a CLI-side GC).
A private org's events and builders go only to its members and admins, the
rule the REST endpoints apply.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TOPICS = frozenset({"activity", "builders", "builds"})

#: Messages a slow viewer may have queued before it is told to resync.
QUEUE_MAX = 256

#: Ids below the high-water mark re-read on each poll.  Two transactions can
#: commit out of id order (another worker, or a populate import racing a
#: publish), so an id just under the mark may become visible after the mark
#: moved past it.
EVENT_LOOKBACK = 32

#: How far before the newest change seen the builds poll looks again, for the
#: same reason: a transaction that started earlier can commit a little later.
JOB_LOOKBACK = datetime.timedelta(seconds=3)


class HubFullError(Exception):
    """Raised by :meth:`LiveHub.subscribe` at the subscriber cap."""


@dataclass(eq=False)
class Subscriber:
    """One connected page: what it asked for and which orgs it may see."""

    topics: frozenset[str]
    is_admin: bool = False
    member_orgs: frozenset[str] = frozenset()
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))

    def can_see(self, org_slug: str, private: Collection[str]) -> bool:
        return (
            not org_slug or self.is_admin or org_slug not in private or org_slug in self.member_orgs
        )

    def offer(self, msg: dict) -> None:
        """Queue *msg*; on overflow, replace the backlog with one resync."""
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync"})


class LiveHub:
    """Fans package events and builder changes out to subscribed pages."""

    def __init__(
        self,
        *,
        fetch_events: Callable[[int], Awaitable[list]],
        fetch_latest_id: Callable[[], Awaitable[int]],
        fetch_builders: Callable[[], Awaitable[list]] | None,
        fetch_private_orgs: Callable[[], Awaitable[Collection[str]]],
        fetch_jobs_since: Callable[[datetime.datetime], Awaitable[list]] | None = None,
        fetch_latest_job_update: Callable[[], Awaitable[datetime.datetime | None]] | None = None,
        poll_interval: float = 2.0,
        builders_interval: float = 10.0,
        max_subscribers: int = 500,
    ) -> None:
        self._fetch_events = fetch_events
        self._fetch_latest_id = fetch_latest_id
        self._fetch_builders = fetch_builders
        self._fetch_private_orgs = fetch_private_orgs
        self._fetch_jobs_since = fetch_jobs_since
        self._fetch_latest_job_update = fetch_latest_job_update
        self.poll_interval = poll_interval
        self.builders_interval = builders_interval
        self.max_subscribers = max_subscribers

        self._subs: set[Subscriber] = set()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        # Activity: everything at or below _floor counts as already sent;
        # _sent holds the ids above it that were.
        self._floor: int | None = None
        self._sent: set[int] = set()
        # Builders: id -> (JSON-ready dict, canonical JSON, org_slug).
        self._builders: dict[int, tuple[dict, str, str]] | None = None
        self._builders_at = 0.0
        self._builders_dirty = False
        # Builds: the newest job change pushed so far, and the (id, updated_at)
        # pairs pushed within JOB_LOOKBACK of it, so a re-read sends nothing twice.
        self._jobs_cursor: datetime.datetime | None = None
        self._jobs_sent: set[tuple[int, datetime.datetime]] = set()

    # ── Subscription ────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    async def subscribe(self, sub: Subscriber) -> None:
        """Register *sub* and queue its ``ready`` (and builders snapshot)."""
        if len(self._subs) >= self.max_subscribers:
            raise HubFullError
        async with self._lock:
            if "activity" in sub.topics and self._floor is None:
                self._floor = await self._fetch_latest_id()
                self._sent.clear()
            if (
                "builds" in sub.topics
                and self._jobs_cursor is None
                and self._fetch_jobs_since is not None
            ):
                latest = (
                    await self._fetch_latest_job_update()
                    if self._fetch_latest_job_update is not None
                    else None
                )
                self._jobs_cursor = latest or datetime.datetime.now(datetime.timezone.utc)
                self._jobs_sent.clear()
            snapshot: list[dict] | None = None
            if "builders" in sub.topics and self._fetch_builders is not None:
                if self._builders is None or self._builders_stale():
                    await self._poll_builders()
                private = await self._private_for(o for _, _, o in self._builders.values())
                snapshot = [d for d, _, org in self._builders.values() if sub.can_see(org, private)]
            self._subs.add(sub)
            sub.offer(
                {
                    "type": "ready",
                    "topics": sorted(sub.topics),
                    "latest_id": self._floor if self._floor is not None else 0,
                }
            )
            if snapshot is not None:
                sub.offer({"type": "builders", "builders": snapshot})
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def poke(self, topic: str | None = None) -> None:
        """Wake the hub now: something in *topic* (or anything) changed."""
        if topic in (None, "builders"):
            self._builders_dirty = True
        self._wake.set()

    async def close(self) -> None:
        self._subs.clear()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ── Loop ───────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            while self._subs:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if not self._subs:
                    break
                try:
                    async with self._lock:
                        await self.tick()
                except Exception:  # noqa: BLE001 - a DB blip must not end the feed
                    logger.exception("live hub: poll failed")
                    await asyncio.sleep(self.poll_interval)
        finally:
            # Nobody is listening: forget the cursors, so the next subscriber
            # starts from the database's state then, not from a stale mark.
            if not self._subs:
                self._floor = None
                self._sent.clear()
                self._builders = None
                self._jobs_cursor = None
                self._jobs_sent.clear()

    async def tick(self) -> None:
        """One poll: push new events and changed builders.  Hold the lock."""
        if self._floor is not None and any("activity" in s.topics for s in self._subs):
            await self._poll_events()
        if (
            self._fetch_builders is not None
            and any("builders" in s.topics for s in self._subs)
            and (self._builders is None or self._builders_dirty or self._builders_stale())
        ):
            await self._poll_builders()
        if (
            self._jobs_cursor is not None
            and self._fetch_jobs_since is not None
            and any("builds" in s.topics for s in self._subs)
        ):
            await self._poll_jobs()

    def _builders_stale(self) -> bool:
        return time.monotonic() - self._builders_at >= self.builders_interval

    async def _private_for(self, org_slugs) -> Collection[str]:
        """The private org slugs, fetched only when *org_slugs* has an org."""
        if any(org_slugs):
            return await self._fetch_private_orgs()
        return ()

    async def _poll_events(self) -> None:
        floor = self._floor or 0
        top = max([floor, *self._sent])
        events = await self._fetch_events(max(floor, top - EVENT_LOOKBACK))
        fresh = [e for e in events if e.id > floor and e.id not in self._sent]
        if not fresh:
            return
        self._sent.update(e.id for e in fresh)
        top = max(top, *(e.id for e in fresh))
        new_floor = top - EVENT_LOOKBACK
        if new_floor > floor:
            self._floor = new_floor
            self._sent = {i for i in self._sent if i > new_floor}
        private = await self._private_for(e.org_slug for e in fresh)
        payload = [(e.org_slug, e.model_dump(mode="json")) for e in fresh]
        for sub in list(self._subs):
            if "activity" not in sub.topics:
                continue
            visible = [d for org, d in payload if sub.can_see(org, private)]
            if visible:
                sub.offer({"type": "activity", "events": visible})

    async def _poll_builders(self) -> None:
        assert self._fetch_builders is not None
        infos = await self._fetch_builders()
        self._builders_at = time.monotonic()
        self._builders_dirty = False
        old = self._builders
        new: dict[int, tuple[dict, str, str]] = {}
        for b in infos:
            d = b.model_dump(mode="json")
            new[b.id] = (d, json.dumps(d, sort_keys=True), b.org_slug or "")
        self._builders = new
        if old is None:
            return
        changed = [(org, d) for i, (d, j, org) in new.items() if i not in old or old[i][1] != j]
        removed = [(org, i) for i, (_, _, org) in old.items() if i not in new]
        if not changed and not removed:
            return
        private = await self._private_for([o for o, _ in changed] + [o for o, _ in removed])
        for sub in list(self._subs):
            if "builders" not in sub.topics:
                continue
            for org, d in changed:
                if sub.can_see(org, private):
                    sub.offer({"type": "builder", "builder": d})
            for org, i in removed:
                if sub.can_see(org, private):
                    sub.offer({"type": "builder_removed", "id": i})

    async def _poll_jobs(self) -> None:
        assert self._fetch_jobs_since is not None and self._jobs_cursor is not None
        jobs = await self._fetch_jobs_since(self._jobs_cursor - JOB_LOOKBACK)
        fresh = []
        for j in jobs:
            stamp = j.updated_at or self._jobs_cursor
            if (j.id, stamp) in self._jobs_sent:
                continue
            self._jobs_sent.add((j.id, stamp))
            fresh.append(j)
            if stamp > self._jobs_cursor:
                self._jobs_cursor = stamp
        horizon = self._jobs_cursor - JOB_LOOKBACK
        self._jobs_sent = {(i, t) for i, t in self._jobs_sent if t >= horizon}
        if not fresh:
            return
        private = await self._private_for(j.org_slug for j in fresh)
        payload = [(j.org_slug or "", j.model_dump(mode="json")) for j in fresh]
        for sub in list(self._subs):
            if "builds" not in sub.topics:
                continue
            visible = [d for org, d in payload if sub.can_see(org, private)]
            if visible:
                sub.offer({"type": "builds", "jobs": visible})
