# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The encoded ``GET /v1/catalog`` body, kept until the catalog changes.

Building the catalog reads every ``packages`` row, sorts the versions and
encodes several MB of JSON.  On cvcpkg.org that took 1.6-2.6 s of the one
uvicorn worker's event loop, so every other request waited behind each of
the ~6 catalog fetches a minute that CI makes, and nearly all of them got
the same bytes back.

Every store method that writes ``packages`` or ``organizations`` is wrapped
in :func:`changes_catalog`, which advances a process-wide generation once its
writes are committed.  A cached body is served only while its generation is
current, so the first fetch after a publish, yank or org change rebuilds it.
The TTL only bounds how long a write this process did not make (another
``--workers`` process, SQL run by hand) can go unseen.

Bodies are cached per scope, not per caller.  A scope is the set of private
organizations whose packages the caller may see (``None`` for an admin, who
sees everything), plus ``include_yanked``.  Joining or leaving an org moves a
caller to another scope without changing what any scope contains, so
membership changes need no invalidation.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass

_generation = 0


def generation() -> int:
    """The catalog's generation: it advances each time the catalog changes."""
    return _generation


def _advance() -> None:
    global _generation
    _generation += 1


def catalog_changed() -> None:
    """Advance the generation once the current writes are committed.

    Inside an ``atomic_session`` (an audited endpoint) that is when the
    endpoint's transaction commits.  Advancing before then would let a fetch
    in between rebuild from the old rows and cache them as current.
    """
    from cvcpkg.server.db import run_after_commit

    run_after_commit(_advance)


def changes_catalog(fn):
    """Mark a store method that writes rows the catalog is built from.

    The generation advances even when the method raises: its writes may
    still commit (the caller can catch the error inside its transaction),
    and a needless rebuild is cheap next to serving a stale catalog.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        finally:
            catalog_changed()

    wrapper.changes_catalog = True
    return wrapper


# A server-relative archive_url, as it appears in the compact JSON below.  The
# match is exact: inside a JSON string every '"' is escaped, so this can only
# be the archive_url key and the opening quote of its value.
_RELATIVE_ARCHIVE_URL = b'"archive_url":"/'


@dataclass(frozen=True)
class CatalogBody:
    """One scope's catalog as JSON, with this server's archive URLs relative."""

    body: bytes
    digest: str
    bundle_count: int

    @classmethod
    def encode(cls, revision: int, bundles: list[dict]) -> CatalogBody:
        # Byte-for-byte what FastAPI's JSONResponse renders for the same dict.
        body = json.dumps(
            {"schema_version": 1, "revision": revision, "bundles": bundles},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(body=body, digest=hashlib.sha256(body).hexdigest(), bundle_count=len(bundles))

    def render(self, base_url: str) -> bytes:
        """The body with each relative archive_url made absolute under *base_url*.

        Done per request, because the base depends on how the client reached
        the server (Host, proxy headers).  One ``bytes.replace`` costs a few
        ms against the 6 MB body; re-encoding it cost over a second.
        """
        base = json.dumps(base_url.rstrip("/"), ensure_ascii=False)[1:-1].encode("utf-8")
        if not base:
            return self.body
        return self.body.replace(_RELATIVE_ARCHIVE_URL, b'"archive_url":"' + base + b"/")

    def etag(self, base_url: str) -> str:
        """A strong ETag for :meth:`render` of *base_url*."""
        tag = hashlib.sha256(f"{self.digest}\0{base_url.rstrip('/')}".encode()).hexdigest()
        return f'"{tag[:32]}"'


def etag_matches(if_none_match: str, etag: str) -> bool:
    """Whether an ``If-None-Match`` header names *etag* (weak comparison)."""
    value = if_none_match.strip()
    if not value:
        return False
    if value == "*":
        return True
    return any(
        (tag.strip()[2:] if tag.strip().startswith("W/") else tag.strip()) == etag
        for tag in value.split(",")
    )


@dataclass(frozen=True)
class _Entry:
    body: CatalogBody
    generation: int
    built_at: float


class CatalogCache:
    """Encoded catalogs by scope key, valid while the generation holds.

    *ttl_seconds* of 0 or less disables caching: every fetch builds afresh.
    *max_entries* bounds memory (each body is several MB); the least recently
    used scope goes first.
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 8) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _fresh(self, key: Hashable) -> CatalogBody | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (
            entry.generation != generation()
            or time.monotonic() - entry.built_at >= self.ttl_seconds
        ):
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry.body

    def _build_lock(self) -> asyncio.Lock:
        # One lock per event loop: tests run several apps, each on its own loop.
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def get(self, key: Hashable, build: Callable[[], Awaitable[CatalogBody]]) -> CatalogBody:
        """The cached body for *key*, or a fresh one from *build*.

        Builds are serialized, so the fetches that arrive together after a
        publish wait for one build instead of each running their own.
        """
        if self.ttl_seconds <= 0:
            return await build()
        body = self._fresh(key)
        if body is not None:
            self.hits += 1
            return body
        async with self._build_lock():
            body = self._fresh(key)
            if body is not None:
                self.hits += 1
                return body
            self.misses += 1
            # Read before building: a write that lands mid-build advances the
            # generation past this, so the next fetch rebuilds.
            gen = generation()
            body = await build()
            self._entries[key] = _Entry(body=body, generation=gen, built_at=time.monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return body
