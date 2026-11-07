#!/usr/bin/env python3
"""Retire the rows whose archive file was overwritten with another row's bytes.

On 2026-10-31 dev's migration 036 relabelled noarch rows ``release/shared`` ->
``none/none`` and kept their archive names (``...-release-shared.tar.zst``).
Populate then imported cvcpkg.org's ``release/shared`` rows under those names,
and /v1/publish republished six of them, and both replaced the file (fixed for
populate in cy-pca/cvcpkg#554).  So 1,449 live dev rows serve another build's
bytes, and ``cvcpkg install`` fails their sha256 check.

A row is a target when its archive file is shared with another row and holds
*that* row's bytes: proof of an overwrite, not mere corruption.  Each target is
yanked and nuked with its full scope, so nothing else at its name and version
is touched.  The file itself stays: the other row still stores it, and a nuke
never deletes an archive another row references.  Populate then imports
upstream's copy of the coordinate under its own name, verified by sha256.

A shared file holding bytes no row claims is reported, never touched.

Dry run by default.  ``--apply`` needs ``--expect N``, the dry run's count, so a
target set that changed since is refused, and an admin token in
``CVCPKG_TOKEN``.  Batches of ``--batch`` (default 200, populate's per-sync
import cap) are timed to land shortly before a populate sync, so a coordinate
is missing for minutes, not for the hours 1,449 imports take at 200 a sync.

Usage:
    python scripts/repair_overwritten_archives.py --server URL
        [--upstream https://cvcpkg.org] [--apply --expect N] [--batch 200]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

#: One row per neutral (none/none) bundle, not one copy per concrete variant.
NEUTRAL_HEADER = {"X-Cvcpkg-Neutral-Variants": "1"}

Coord = tuple[str, str, str, str, str, str]


@dataclass(frozen=True)
class Target:
    row: dict  # the overwritten row: retired
    holder: dict  # the row whose bytes the file holds: kept, file and all
    file: str


def coord(row: dict) -> Coord:
    return (
        row["name"],
        row["version"],
        row["platform"],
        row["arch"],
        row["build_type"],
        row["link"],
    )


def tag(row: dict) -> str:
    n, v, p, a, b, link = coord(row)
    return f"{n}=={v} {p}/{a}/{b}/{link}"


def archive_name(row: dict) -> str:
    return (row.get("archive_url") or "").rsplit("/", 1)[-1]


def shared_files(rows: list[dict]) -> dict[str, list[dict]]:
    """Archive file name -> its rows, for each file more than one row stores."""
    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if archive_name(r):
            by_file[archive_name(r)].append(r)
    return {f: rs for f, rs in by_file.items() if len(rs) > 1}


def select_targets(
    shared: dict[str, list[dict]], file_sha: dict[str, str]
) -> tuple[list[Target], list[str]]:
    """The overwritten rows, and the shared files whose bytes no row claims.

    A file not in *file_sha* (it could not be hashed) yields nothing.
    """
    targets: list[Target] = []
    unclaimed: list[str] = []
    for f, rows in sorted(shared.items()):
        sha = file_sha.get(f)
        if not sha:
            continue
        holders = [r for r in rows if r["sha256"] == sha]
        if not holders:
            unclaimed.append(f)
            continue
        targets += [Target(r, holders[0], f) for r in rows if r["sha256"] != sha]
    return targets, unclaimed


# ── HTTP ─────────────────────────────────────────────────────────


def _request(url: str, token: str, *, method="GET", headers=None, timeout=300):
    h = {"User-Agent": "cvcpkg-repair-overwritten-archives/1", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h, method=method), timeout=timeout
    )


def get_json(url: str, token: str, headers=None):
    with _request(url, token, headers=headers) as r:
        return json.load(r)


def fetch_catalog(server: str, token: str) -> list[dict]:
    url = f"{server}/v1/catalog?include_yanked=true"
    return get_json(url, token, NEUTRAL_HEADER).get("bundles", [])


def hash_archive(server: str, token: str, fname: str) -> str:
    """sha256 of the bytes *server* serves for *fname*; "" if it serves none."""
    url = f"{server}/v1/download/{urllib.parse.quote(fname, safe='')}"
    for attempt in range(3):
        try:
            h = hashlib.sha256()
            with _request(url, token) as r:
                while chunk := r.read(1 << 20):
                    h.update(chunk)
            return h.hexdigest()
        except urllib.error.HTTPError as exc:
            print(f"  {fname}: HTTP {exc.code}, not checked", flush=True)
            return ""
        except OSError as exc:
            if attempt == 2:
                print(f"  {fname}: {exc}, not checked", flush=True)
    return ""


def last_sync(server: str) -> str:
    stats = get_json(f"{server}/healthz", "").get("populate_stats") or {}
    return stats.get("last_sync") or ""


def wait_for_sync(server: str, after: str, timeout: float) -> str:
    """Block until a populate sync newer than *after* has finished."""
    deadline = time.monotonic() + timeout
    while (seen := last_sync(server)) == after:
        if time.monotonic() > deadline:
            sys.exit(f"no populate sync finished within {timeout:.0f}s of {after}; stopping")
        time.sleep(30)
    return seen


def caller_role(server: str, token: str) -> str:
    """The role *token* has on *server* ("admin", "publisher", ...), or why it has none."""
    try:
        return get_json(f"{server}/v1/auth/whoami", token).get("role", "")
    except urllib.error.HTTPError as exc:
        return f"none (HTTP {exc.code})"


def post_scoped(server: str, token: str, row: dict, action: str) -> int:
    """POST a yank or nuke scoped to exactly *row*'s variant; the count it changed."""
    n, v, p, a, b, link = coord(row)
    path = f"/v1/packages/{urllib.parse.quote(n, safe='')}/{urllib.parse.quote(v, safe='')}"
    query = urllib.parse.urlencode({"platform": p, "arch": a, "build_type": b, "link": link})
    with _request(f"{server}{path}/{action}?{query}", token, method="POST") as r:
        return int(json.load(r).get("count", -1))


def retire(server: str, token: str, row: dict) -> None:
    want = 0 if row["yanked"] else 1
    if (got := post_scoped(server, token, row, "yank")) != want:
        sys.exit(f"yank of {tag(row)} changed {got} row(s), expected {want}; stopping")
    if (got := post_scoped(server, token, row, "nuke")) != 1:
        sys.exit(f"nuke of {tag(row)} removed {got} row(s), expected 1; stopping")


# ── main ─────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", required=True, help="the server to repair")
    ap.add_argument("--upstream", default="", help="its populate upstream, to report re-imports")
    ap.add_argument("--apply", action="store_true", help="yank and nuke the targets")
    ap.add_argument("--expect", type=int, default=-1, help="with --apply: the dry run's count")
    ap.add_argument("--batch", type=int, default=200, help="targets per populate sync")
    ap.add_argument("--interval", type=int, default=900, help="the server's populate interval (s)")
    ap.add_argument("--lead", type=int, default=180, help="land a batch this long before a sync")
    args = ap.parse_args(argv)
    server = args.server.rstrip("/")
    token = os.environ.get("CVCPKG_TOKEN", "")
    if args.apply and (not token or args.expect < 0):
        ap.error("--apply needs CVCPKG_TOKEN (admin) and --expect N")
    # Before the hashing and the wait for a sync: a token another server issued
    # (prod's, say) is refused only at the first yank, many minutes in.
    if args.apply and (role := caller_role(server, token)) != "admin":
        sys.exit(f"CVCPKG_TOKEN's role on {server} is {role or 'unknown'}, not admin; stopping")

    rows = fetch_catalog(server, token)
    shared = shared_files(rows)
    print(f"{server}: {len(rows)} rows; {len(shared)} archive files shared by 2+ rows", flush=True)
    with ThreadPoolExecutor(6) as ex:
        hashes = ex.map(lambda f: hash_archive(server, token, f), shared)
        file_sha = dict(zip(shared, hashes, strict=True))
    unchecked = sum(1 for s in file_sha.values() if not s)
    targets, unclaimed = select_targets(shared, file_sha)

    per_coord: dict[Coord, int] = defaultdict(int)
    for r in rows:
        per_coord[coord(r)] += 1
    # The yank/nuke scope has no org: a twin in another org would go too.
    ambiguous = [t for t in targets if t.row.get("org") or per_coord[coord(t.row)] != 1]
    targets = [t for t in targets if t not in ambiguous]

    upstream: dict[Coord, dict] = {}
    if args.upstream:
        for r in fetch_catalog(args.upstream.rstrip("/"), ""):
            if not r.get("org") and not r.get("yanked") and r.get("archive_url"):
                upstream[coord(r)] = r

    def fate(t: Target) -> str:
        if not args.upstream:
            return ""
        up = upstream.get(coord(t.row))
        if up is None:
            return "  [no upstream copy]"
        return "" if up["sha256"] == t.holder["sha256"] else "  [upstream: other bytes]"

    print(f"\n{len(targets)} overwritten row(s) to retire:")
    for t in targets:
        print(f"  {tag(t.row)}  file holds {tag(t.holder)}{fate(t)}")
    for t in ambiguous:
        print(f"  SKIPPED {tag(t.row)}: org row or org twin; its scope is not unique")
    for f in unclaimed:
        print(f"  UNTOUCHED {f}: bytes match none of its rows")
    if args.upstream:
        served = sum(1 for t in targets if coord(t.row) in upstream)
        print(f"\nupstream {args.upstream} serves {served} of them; populate re-imports those")
    print(f"unchecked files: {unchecked}; skipped: {len(ambiguous)}; unclaimed: {len(unclaimed)}")

    if not args.apply:
        print(
            f"\nDRY RUN: {len(targets)} row(s) would be retired (--apply --expect {len(targets)})"
        )
        return 0
    if len(targets) != args.expect:
        sys.exit(f"{len(targets)} target(s) now, not the expected {args.expect}; refusing")

    if not (synced := last_sync(server)):
        sys.exit(f"{server} reports no populate sync; nothing would re-import the targets")
    for i in range(0, len(targets), args.batch):
        batch = targets[i : i + args.batch]
        if i:
            synced = wait_for_sync(server, synced, args.interval * 2)
        start = dt.datetime.fromisoformat(synced).timestamp() + args.interval - args.lead
        if (pause := start - time.time()) > 0:
            print(f"waiting {pause:.0f}s to land the next batch just before a sync", flush=True)
            time.sleep(pause)
        for t in batch:
            retire(server, token, t.row)
        print(f"retired {i + len(batch)}/{len(targets)}", flush=True)

    # Check the re-imports, allowing a few syncs for stragglers.
    want = {coord(t.row) for t in targets if coord(t.row) in upstream}
    for _ in range(3):
        synced = wait_for_sync(server, synced, args.interval * 2)
        have = {coord(r) for r in fetch_catalog(server, token) if not r.get("yanked")}
        if not (missing := want - have):
            break
        print(f"{len(missing)} coordinate(s) not re-imported yet", flush=True)
    for c in sorted(missing):
        print(f"  NOT RE-IMPORTED {c}")
    print(f"\nDONE: retired {len(targets)}; {len(want) - len(missing)}/{len(want)} re-imported")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
