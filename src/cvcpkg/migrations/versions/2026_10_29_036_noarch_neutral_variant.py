# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""noarch bundles become config/link-neutral (build_type/link "none")

Revision ID: 036
Revises: 035
Create Date: 2026-10-29

A platform-independent bundle (platform ``any``, arch ``noarch``) does not
differ between release and debug or between shared and static, but it was
published under whatever config/link the build matrix said -- up to four rows
per version (release/shared, release/static, debug/shared, debug/static), each
listed with a meaningless "release"/"static" label.  It is now published once,
as ``none``/``none`` (cvcpkg.platform.NEUTRAL_CONFIG / NEUTRAL_LINK).

Every other bundle of a recipe with an ``any`` entry is neutral too: a
pure-Python column's ``windows`` bundle exists only for the Windows layout
(``Lib/site-packages``).  The server cannot see recipes, so it takes a
concrete-platform row whose (name, version, org) also has an any/noarch row to
be one of those; today every recipe with an ``any`` entry and another entry is
such a pure-Python column.

This relabels what is already published.  For each such version (name,
version, platform, arch, org) ONE row becomes none/none -- a live row before a
yanked one, then release/shared, release/static, debug/shared, debug/static,
then the oldest -- and the others are YANKED rather than deleted:

* a yanked row leaves the catalog, but its archive stays downloadable for a
  lockfile that pinned it, and the row still decides that archive's org
  visibility (a file with no row is served to anyone, which would expose a
  private org's archive);
* the yank-retention GC (CVCPKG_YANK_RETENTION_DAYS) later removes the row and
  its archive through the normal path, leaving a tombstone.

A group is left alone when another org already holds its none/none slot and the
database's unique variant index does not include org_slug (databases created
by migration 001 have it without): relabelling would violate the index.  Such
a group keeps its old labels, which current clients still resolve.

Downgrade relabels each none/none row to the first of release/shared,
release/static, debug/shared, debug/static its version does not already have.
The rows this upgrade yanked stay yanked.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NONE = "none"

# Survivor preference among one noarch version's variants, best first.
_VARIANT_RANK = {
    (_NONE, _NONE): 0,
    ("release", "shared"): 1,
    ("release", "static"): 2,
    ("debug", "shared"): 3,
    ("debug", "static"): 4,
}

_packages = sa.table(
    "packages",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("version", sa.String),
    sa.column("platform", sa.String),
    sa.column("arch", sa.String),
    sa.column("build_type", sa.String),
    sa.column("link", sa.String),
    sa.column("org_slug", sa.String),
    sa.column("yanked", sa.Boolean),
    sa.column("yanked_at", sa.DateTime(timezone=True)),
)


def _unique_index_has_org(insp: sa.Inspector) -> bool:
    for ix in insp.get_indexes("packages"):
        if ix["name"] == "ix_packages_unique_variant":
            return "org_slug" in (ix.get("column_names") or [])
    return True  # no such index: nothing to violate


def _version_rows(conn, marks: sa.ColumnElement[bool], *extra: sa.Column) -> list:
    """Every row of each (name, version, org) that has a row matching *marks*."""
    p = _packages
    names = sa.select(p.c.name).where(marks).distinct().scalar_subquery()
    rows = conn.execute(
        sa.select(
            p.c.id,
            p.c.name,
            p.c.version,
            p.c.platform,
            p.c.arch,
            p.c.build_type,
            p.c.link,
            p.c.org_slug,
            marks.label("marked"),
            *extra,
        )
        .where(p.c.name.in_(names))
        .order_by(p.c.id)
    ).all()
    marked = {(r.name, r.version, r.org_slug or "") for r in rows if r.marked}
    return [r for r in rows if (r.name, r.version, r.org_slug or "") in marked]


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    p = _packages
    has_yanked_at = "yanked_at" in {c["name"] for c in insp.get_columns("packages")}
    index_has_org = _unique_index_has_org(insp)

    # The any/noarch rows and their versions' concrete-platform siblings.
    rows = _version_rows(conn, p.c.platform == "any", p.c.yanked)

    # One group per published bundle: the any/noarch one and, say, windows/x86_64.
    groups: dict[tuple[str, str, str, str, str], list] = {}
    for r in rows:
        groups.setdefault((r.name, r.version, r.platform, r.arch, r.org_slug or ""), []).append(r)

    # none/none slots already taken (or taken below), by (name, version,
    # platform, arch) when the unique index ignores org, else with the org.
    def _slot(key: tuple[str, str, str, str, str]) -> tuple:
        return key if index_has_org else key[:4]

    taken = {
        _slot(k): r.id
        for k, rs in groups.items()
        for r in rs
        if (r.build_type, r.link) == (_NONE, _NONE)
    }

    now = datetime.datetime.now(datetime.timezone.utc)
    for key, rs in groups.items():
        survivor = min(
            rs,
            key=lambda r: (bool(r.yanked), _VARIANT_RANK.get((r.build_type, r.link), 9), r.id),
        )
        holder = taken.get(_slot(key))
        if holder is not None and holder != survivor.id:
            # Another row (another org's, under an org-less index) has the slot.
            continue
        if (survivor.build_type, survivor.link) != (_NONE, _NONE):
            conn.execute(
                p.update().where(p.c.id == survivor.id).values(build_type=_NONE, link=_NONE)
            )
            taken[_slot(key)] = survivor.id
        extra = [r.id for r in rs if r.id != survivor.id and not r.yanked]
        if extra:
            values: dict = {"yanked": True}
            if has_yanked_at:
                values["yanked_at"] = now
            conn.execute(p.update().where(p.c.id.in_(extra)).values(**values))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    p = _packages
    index_has_org = _unique_index_has_org(insp)
    rows = _version_rows(conn, p.c.build_type == _NONE)

    def _slot(r) -> tuple:
        base = (r.name, r.version, r.platform, r.arch)
        return base + ((r.org_slug or ""),) if index_has_org else base

    used = {(_slot(r), r.build_type, r.link) for r in rows}
    for r in rows:
        if (r.build_type, r.link) != (_NONE, _NONE):
            continue
        # The first variant this version does not already have (the rows the
        # upgrade yanked still hold theirs); with none free, keep none/none.
        for cfg, lnk in sorted(_VARIANT_RANK, key=_VARIANT_RANK.get)[1:]:
            if (_slot(r), cfg, lnk) not in used:
                conn.execute(p.update().where(p.c.id == r.id).values(build_type=cfg, link=lnk))
                used.add((_slot(r), cfg, lnk))
                break
