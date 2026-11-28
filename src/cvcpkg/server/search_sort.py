# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""How ``/v1/search?sort=`` orders a whole result, before it is paged.

A sort is one key, with a leading ``-`` for descending: ``sort=name``,
``sort=-size``.  Empty means the order a search gives without one: best match
first with a query (:mod:`cvcpkg.server.search_rank`), by name without one.

What a key may be depends on what the search pages (``group``):

* ``build`` rows (:data:`BUILD_SORT_KEYS`): the row's own columns, ``size``
  being ``size_bytes`` and ``published`` being ``published_at``;
* ``package`` summaries (:data:`PACKAGE_SORT_KEYS`): the summary's fields,
  ``version`` being the newest matching version, ``builds`` the number of
  matching rows and ``size`` their total ``size_bytes``.

Text keys compare case-insensitively, then exactly.  Versions compare by
:func:`cvcpkg.semver.version_sort_key`, the order that picks a package's
"latest", so ``1.10`` sorts after ``1.9``.

Rows that tie on the key keep the order the search gives without a sort, and
then a unique id (the DB row id, a package's org/name, a file-index bundle's
place in the index), in both directions.  ``-`` reverses the key only, so
paging with limit/offset never repeats or skips a row.

The file-index backend sorts with the functions here.  The DB backend orders
in SQL by the same expressions (``DbPackageIndex``), except ``version``:
version order is not something SQL can compute, so that key fetches the
matching rows' versions and sorts them here.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple, TypeVar

from cvcpkg.semver import version_sort_key

BUILD_SORT_KEYS = (
    "name",
    "version",
    "platform",
    "arch",
    "build_type",
    "link",
    "size",
    "published",
    "org",
)
PACKAGE_SORT_KEYS = ("name", "org", "version", "builds", "size")

_ALL_KEYS = tuple(dict.fromkeys(BUILD_SORT_KEYS + PACKAGE_SORT_KEYS))

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

T = TypeVar("T")


class SearchSort(NamedTuple):
    """One parsed ``sort``: the key, and whether it runs descending."""

    key: str
    descending: bool = False

    def __str__(self) -> str:
        return f"-{self.key}" if self.descending else self.key


def sort_keys_for(group: str) -> tuple[str, ...]:
    """The keys a ``group=<group>`` search sorts by."""
    return PACKAGE_SORT_KEYS if group == "package" else BUILD_SORT_KEYS


def _allowed(group: str) -> str:
    return f"group={group} sorts by {', '.join(sort_keys_for(group))}"


def parse_sort(raw: str) -> SearchSort | None:
    """*raw* as a :class:`SearchSort`, or ``None`` for the default order.

    Raises ``ValueError``, naming the keys, for a key neither group has.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    descending = raw.startswith("-")
    key = raw[1:] if descending else raw
    if key not in _ALL_KEYS:
        raise ValueError(
            f"unknown sort key {raw!r}: {_allowed('build')}; {_allowed('package')}; "
            "a leading '-' sorts descending"
        )
    return SearchSort(key, descending)


def validate_sort_param(raw: str) -> str:
    """The ``sort`` query parameter's validator: *raw* if :func:`parse_sort` takes it."""
    parse_sort(raw)
    return raw


def check_sort_group(sort: SearchSort | None, group: str) -> None:
    """Raise ``ValueError``, naming the keys, when *group* cannot sort by *sort*."""
    if sort is not None and sort.key not in sort_keys_for(group):
        raise ValueError(
            f"group={group} cannot sort by {sort.key!r}: {_allowed(group)}; "
            "a leading '-' sorts descending"
        )


def text_sort_key(value: Any) -> tuple[str, str]:
    """A text column's order: case-insensitively, then exactly (SQL: ``lower(c), c``)."""
    s = "" if value is None else str(value)
    return (s.lower(), s)


def published_sort_key(value: Any) -> datetime.datetime:
    """``published_at`` as an aware UTC datetime, whatever form the index holds it in.

    The file index holds an ISO string, or a datetime where YAML parsed one;
    a naive time is UTC, and a missing or unreadable one sorts as the epoch.
    """
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.datetime.fromisoformat(text)
        except ValueError:
            return _EPOCH
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)
    return _EPOCH


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# What each key sorts a file-index bundle (a ``state.index`` dict) by.
_BUNDLE_KEYS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "name": lambda b: text_sort_key(b.get("name")),
    "version": lambda b: version_sort_key(str(b.get("version") or "")),
    "platform": lambda b: text_sort_key(b.get("platform")),
    "arch": lambda b: text_sort_key(b.get("arch")),
    "build_type": lambda b: text_sort_key(b.get("build_type")),
    "link": lambda b: text_sort_key(b.get("link")),
    "size": lambda b: _int(b.get("size_bytes")),
    "published": lambda b: published_sort_key(b.get("published_at")),
    "org": lambda b: text_sort_key(b.get("org")),
}

# What each key sorts a package summary (:func:`summarize_group`'s dict) by.
_GROUP_KEYS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "name": lambda g: text_sort_key(g.get("name")),
    "org": lambda g: text_sort_key(g.get("org")),
    "version": lambda g: version_sort_key(str(g.get("version") or "")),
    "builds": lambda g: _int(g.get("builds")),
    "size": lambda g: _int(g.get("size_bytes")),
}


def _stable_sort(items: Iterable[T], key: Callable[[T], Any], descending: bool) -> list[T]:
    # sorted() is stable in both directions (reverse=True keeps equal items in
    # their input order), so ties keep the order *items* came in.
    return sorted(items, key=key, reverse=descending)


def sort_bundles(bundles: Iterable[Mapping[str, Any]], sort: SearchSort) -> list:
    """File-index *bundles* by *sort*; ties keep their input order."""
    return _stable_sort(bundles, _BUNDLE_KEYS[sort.key], sort.descending)


def sort_groups(
    keys: Iterable[T], summary: Callable[[T], Mapping[str, Any]], sort: SearchSort
) -> list[T]:
    """Package *keys* by *sort* of their ``summary(key)``; ties keep their input order."""
    by = _GROUP_KEYS[sort.key]
    return _stable_sort(keys, lambda k: by(summary(k)), sort.descending)


def sort_by_version(rows: Iterable[T], version: Callable[[T], str], sort: SearchSort) -> list[T]:
    """*rows* by ``version(row)`` (:func:`version_sort_key`); ties keep their input order.

    The DB backend's ``version`` sort, which SQL cannot order.
    """
    return _stable_sort(rows, lambda r: version_sort_key(version(r) or ""), sort.descending)
