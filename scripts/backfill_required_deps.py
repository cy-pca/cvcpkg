#!/usr/bin/env python3
"""Fill in the runtime deps that catalog rows published before #152 left out.

Until #152, `pack-all --server-cache-push` published each bundle with name and
tuple only, so its catalog row reached the server with ``required_deps: []``
(and ``provides: []``).  The client resolver walks ``required_deps`` on install,
so such a package installs WITHOUT its runtime dependencies -- e.g.
``cvcpkg install aiomysql-cp311`` resolved aiomysql alone, no python311, no
pymysql-cp311.  #152 fixed new publishes; this script repairs the old rows.

For every catalog row whose required_deps (or provides) is empty, it finds the
recipe that built THAT version -- ``recipes/<name>/recipe.yaml`` at the commit
whose ``upstream_version`` and ``cvc_revision`` match the row, not today's
recipe, whose deps may have moved on -- and derives the edges with
``cvcpkg.builder.runtime_dependencies_from_raw``, the same function every
publish path uses.  Rows whose recipe declares nothing to fill are left alone;
rows whose version no recipe in history matches are reported, not guessed.

Dry run by default: prints what it would fill.  ``--apply`` posts each fill to
the server's admin endpoint ``POST /v1/admin/packages/{name}/{version}/
backfill-deps``, which only ever writes a column that is still ``[]``.

Usage:
    python scripts/backfill_required_deps.py [--server URL] [--only NAME ...]
        [--platform P] [--csv FILE] [--apply --token ADMIN_TOKEN]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cvcpkg.builder import runtime_dependencies_from_raw  # noqa: E402
from cvcpkg.revisions import base_version, revision_of  # noqa: E402

DEFAULT_SERVER = "https://cvcpkg.org"


class RecipeHistory:
    """``recipes/<name>/recipe.yaml`` at every version git history holds.

    ``raw(name, version)`` returns the parsed recipe whose ``upstream_version``
    and ``cvc_revision`` match *version* (``X+cvc.N``), preferring the working
    tree, then the newest commit that had it; None when no recipe in history
    ever built that version.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self._by_name: dict[str, dict[tuple[str, int], tuple[str, dict]]] = {}

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        ).stdout

    @staticmethod
    def _key(raw: dict) -> tuple[str, int] | None:
        rec = (raw or {}).get("recipe") or {}
        if "upstream_version" not in rec:
            return None
        return str(rec["upstream_version"]), int(rec.get("cvc_revision", 1))

    def _load(self, name: str) -> dict[tuple[str, int], tuple[str, dict]]:
        if name in self._by_name:
            return self._by_name[name]
        rel = f"recipes/{name}/recipe.yaml"
        found: dict[tuple[str, int], tuple[str, dict]] = {}
        current = self.repo / rel
        if current.is_file():
            raw = yaml.safe_load(current.read_text())
            if (k := self._key(raw)) is not None:
                found[k] = ("working-tree", raw)
        try:
            commits = self._git("log", "--format=%H", "--", rel).split()
        except subprocess.CalledProcessError:
            commits = []
        for sha in commits:  # newest first; the first commit seen for a key wins
            try:
                text = self._git("show", f"{sha}:{rel}")
            except subprocess.CalledProcessError:
                continue  # the commit that deleted it
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            k = self._key(raw)
            if k is not None and k not in found:
                found[k] = (sha[:12], raw)
        self._by_name[name] = found
        return found

    def raw(self, name: str, version: str) -> tuple[str, dict] | None:
        return self._load(name).get((base_version(version), revision_of(version)))


@dataclass
class Fill:
    """One catalog row and what the backfill would write into it."""

    row: dict
    source: str  # commit the recipe came from, or "working-tree"; "" when not found
    required_deps: list[dict[str, str]] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.source:
            return "no-recipe-at-version"
        if self.required_deps or self.provides:
            return "fill"
        return "nothing-to-fill"

    def query(self) -> dict[str, str]:
        r = self.row
        return {
            "platform": r["platform"],
            "arch": r["arch"],
            "build_type": r.get("build_type") or "release",
            "link": r.get("link") or "shared",
            "org": r.get("org") or "",
        }


def plan(
    rows: Iterable[dict],
    history: RecipeHistory,
    *,
    only: set[str] | None = None,
    platform: str | None = None,
) -> list[Fill]:
    """A :class:`Fill` for every non-yanked row with an empty edge column."""
    fills: list[Fill] = []
    for row in rows:
        if row.get("yanked") or (only and row["name"] not in only):
            continue
        if platform and row["platform"] != platform:
            continue
        need_deps = not row.get("required_deps")
        need_provides = not row.get("provides")
        if not (need_deps or need_provides):
            continue
        found = history.raw(row["name"], row["version"])
        if found is None:
            fills.append(Fill(row, ""))
            continue
        source, raw = found
        fills.append(
            Fill(
                row,
                source,
                runtime_dependencies_from_raw(raw, row["platform"]) if need_deps else [],
                [str(p) for p in (raw.get("provides") or [])] if need_provides else [],
            )
        )
    return fills


Post = Callable[[str, dict[str, str], dict], dict]


def apply(fills: Iterable[Fill], post: Post) -> tuple[int, list[str]]:
    """POST every ``fill``-status entry; returns (rows changed, errors)."""
    changed, errors = 0, []
    for f in fills:
        if f.status != "fill":
            continue
        path = (
            f"/v1/admin/packages/{urllib.parse.quote(f.row['name'], safe='')}/"
            f"{urllib.parse.quote(f.row['version'], safe='')}/backfill-deps"
        )
        try:
            resp = post(path, f.query(), {"required_deps": f.required_deps, "provides": f.provides})
        except Exception as exc:  # noqa: BLE001 -- report and carry on
            errors.append(f"{f.row['name']} {f.row['version']} {f.query()}: {exc}")
            continue
        if any((resp.get("count") or {}).values()):
            changed += 1
    return changed, errors


def _http_post(server: str, token: str) -> Post:
    def post(path: str, query: dict[str, str], body: dict) -> dict:
        url = f"{server.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 -- admin URL
            return json.loads(resp.read())

    return post


def _fetch_catalog(server: str, token: str) -> list[dict]:
    req = urllib.request.Request(f"{server.rstrip('/')}/v1/catalog")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
        return json.loads(resp.read())["bundles"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--token", default=os.environ.get("CVCPKG_TOKEN", ""), help="admin token")
    ap.add_argument("--repo", type=Path, default=REPO, help="cvcpkg checkout (recipe history)")
    ap.add_argument("--only", action="append", default=[], metavar="NAME")
    ap.add_argument("--platform")
    ap.add_argument("--csv", type=Path, help="write the full plan here")
    ap.add_argument("--apply", action="store_true", help="post the fills (default: dry run)")
    args = ap.parse_args(argv)
    if args.apply and not args.token:
        ap.error("--apply needs an admin token (--token or CVCPKG_TOKEN)")

    rows = _fetch_catalog(args.server, args.token)
    fills = plan(
        rows, RecipeHistory(args.repo), only=set(args.only) or None, platform=args.platform
    )
    by_status: dict[str, int] = {}
    for f in fills:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    print(f"{len(rows)} catalog rows; {len(fills)} with an empty edge column: {by_status}")
    for f in fills:
        if f.status == "fill":
            deps = ",".join(d["name"] for d in f.required_deps) or "-"
            print(
                f"  fill {f.row['name']} {f.row['version']} {f.row['platform']}/{f.row['arch']}"
                f" {f.row.get('build_type')}/{f.row.get('link')}: deps={deps}"
                f" provides={','.join(f.provides) or '-'}  [recipe@{f.source}]"
            )
    if args.csv:
        with args.csv.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "status",
                    "name",
                    "version",
                    "platform",
                    "arch",
                    "build_type",
                    "link",
                    "org",
                    "required_deps",
                    "provides",
                    "recipe_source",
                ]
            )
            for f in fills:
                q = f.query()
                w.writerow(
                    [
                        f.status,
                        f.row["name"],
                        f.row["version"],
                        q["platform"],
                        q["arch"],
                        q["build_type"],
                        q["link"],
                        q["org"],
                        " ".join(d["name"] for d in f.required_deps),
                        " ".join(f.provides),
                        f.source,
                    ]
                )
        print(f"wrote {args.csv}")
    if not args.apply:
        print("dry run: nothing posted (pass --apply to fill)")
        return 0
    changed, errors = apply(fills, _http_post(args.server, args.token))
    print(f"filled {changed} row(s); {len(errors)} error(s)")
    for e in errors:
        print(f"  ERROR {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
