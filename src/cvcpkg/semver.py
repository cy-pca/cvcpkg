"""Lightweight SemVer-range parser and matcher.

Supports:  ==, >=, <=, >, <, ^, ~>, and comma-separated conjunctions.
Version strings may carry ``+cvc.<rev>`` build metadata (ignored for
range comparisons, used as a tiebreaker by the resolver).

This is intentionally minimal — no PEP-440, no npm-style ||.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

# Numeric components accept leading zeros so date-tagged upstreams
# (e.g. vcglib "2025.07") parse without repackaging. Converted to int
# below, so "07" collapses to 7 for comparison.
_VER_RE = re.compile(
    r"^(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def _pre_key(pre: str) -> tuple[tuple[int, int, str], ...]:
    """Identifier-wise pre-release precedence, per SemVer §11.4.1-11.4.4.

    Compared field by field: a numeric identifier compares numerically and
    ranks BELOW an alphanumeric one, and a larger set of fields outranks a
    smaller one that is otherwise an equal prefix.  This is why ``rc.10`` must
    outrank ``rc.9`` -- comparing the raw ``pre`` strings sorts them lexically
    and gets it backwards.
    """
    if not pre:
        return ()
    out: list[tuple[int, int, str]] = []
    for ident in pre.split("."):
        if ident.isdigit():
            out.append((0, int(ident), ""))
        else:
            out.append((1, 0, ident))
    return tuple(out)


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: str
    build: str  # ignored in comparisons

    @classmethod
    def parse(cls, s: str) -> Version:
        m = _VER_RE.match(s.strip())
        if not m:
            raise ValueError(f"invalid version: {s!r}")
        return cls(
            major=int(m.group("major")),
            minor=int(m.group("minor") or 0),
            patch=int(m.group("patch") or 0),
            pre=m.group("pre") or "",
            build=m.group("build") or "",
        )

    @property
    def _cmp_tuple(self) -> tuple:
        # Pre-release versions sort before the release (SemVer rule); build
        # metadata is deliberately absent -- it never affects precedence.
        return (
            self.major,
            self.minor,
            self.patch,
            not bool(self.pre),
            _pre_key(self.pre),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_tuple == other._cmp_tuple

    def __hash__(self) -> int:
        # __eq__ ignores `build`, so hashing must too, or equal Versions land
        # in different buckets.  The frozen dataclass's default __hash__ would
        # include `build`; pin it to the comparison identity instead.
        return hash(self._cmp_tuple)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_tuple < other._cmp_tuple

    @property
    def cvc_revision(self) -> int:
        """Extract the +cvc.<N> build-metadata integer, or 0."""
        if self.build.startswith("cvc."):
            try:
                return int(self.build[4:])
            except ValueError:
                pass
        return 0

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            s += f"-{self.pre}"
        if self.build:
            s += f"+{self.build}"
        return s


# ── Range constraints ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Constraint:
    """Single comparison constraint."""

    op: str  # ==, >=, <=, >, <, ^, ~>
    ver: Version

    def satisfied_by(self, v: Version) -> bool:
        if self.op == "==":
            # Version.__eq__ is SemVer-faithful and ignores +cvc.N, but a pin is
            # a *selection*, not a range test: after the readline incident an
            # operator pins "==8.3+cvc.2" precisely to avoid the broken cvc.1,
            # so an exact pin that names a revision must hold the revision too.
            # Ranges (>=, ^, ~>) still ignore build metadata -- that is the spec.
            if v != self.ver:
                return False
            want = self.ver.cvc_revision
            return want == 0 or v.cvc_revision == want
        if self.op == ">=":
            return v >= self.ver
        if self.op == "<=":
            return v <= self.ver
        if self.op == ">":
            return v > self.ver
        if self.op == "<":
            return v < self.ver
        if self.op == "^":
            # ^1.2.3 → >=1.2.3, <2.0.0 (caret)
            if self.ver.major > 0:
                upper = Version(self.ver.major + 1, 0, 0, "", "")
            elif self.ver.minor > 0:
                upper = Version(0, self.ver.minor + 1, 0, "", "")
            else:
                upper = Version(0, 0, self.ver.patch + 1, "", "")
            return v >= self.ver and v < upper
        if self.op == "~>":
            # ~>1.2.3 → >=1.2.3, <1.3.0 (pessimistic / tilde)
            upper = Version(self.ver.major, self.ver.minor + 1, 0, "", "")
            return v >= self.ver and v < upper
        raise ValueError(f"unknown op: {self.op!r}")


_OP_RE = re.compile(r"^(==|>=|<=|~>|>|<|\^)")


def parse_range(spec: str) -> list[_Constraint]:
    """Parse a comma-separated constraint string into a list of constraints.

    Examples::

        ">=1.2.3,<2.0.0"
        "^3.0"
        "==1.86.0"
        ">=20240722,<20260000"
    """
    constraints: list[_Constraint] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = _OP_RE.match(part)
        if m:
            op = m.group(1)
            ver_str = part[m.end() :].strip()
        else:
            # Bare version string → treat as ==
            op = "=="
            ver_str = part
        constraints.append(_Constraint(op=op, ver=Version.parse(ver_str)))
    return constraints


def satisfies(version: str | Version, spec: str) -> bool:
    """Return True if *version* satisfies every constraint in *spec*."""
    v = Version.parse(version) if isinstance(version, str) else version
    return all(c.satisfied_by(v) for c in parse_range(spec))


# ── Canonical ordering key ──────────────────────────────────────
#
# THE one place allowed to call Version.parse inside a sort key.  Every "which
# version wins" decision -- resolver, installer, publish, the server catalog --
# must route through this, so they cannot disagree.  Four hand-rolled orderings
# had already diverged, and each got the +cvc.N tie or an unparseable version
# wrong in its own way (the readline 8.3+cvc.1-over-cvc.2 incident, cvcpkg
# upgrade proposing openssh downgrades, cvcpkg info crashing on a raw version).

_CVC_RE = re.compile(r"\+cvc\.(\d+)$")
_NAT_RE = re.compile(r"(\d+)")


def _natural_key(s: str) -> tuple[tuple[int, int, str], ...]:
    """Digit runs compare numerically, text runs lexically, so '10' > '9'.

    Used to order versions that are not valid SemVer (openssh "10.4p1", x264
    "0.164.stable", jam "r1beta5") among themselves without a raw lexical sort.
    """
    out: list[tuple[int, int, str]] = []
    for tok in _NAT_RE.split(s):
        if not tok:
            continue
        out.append((0, int(tok), "") if tok.isdigit() else (1, 0, tok))
    return tuple(out)


def version_sort_key(version: str, cvc_revision: int | None = None) -> tuple:
    """Total, never-raising ASCENDING ordering key for a cvcpkg version string.

    For newest-first use ``max(xs, key=version_sort_key)`` or
    ``sorted(xs, key=version_sort_key, reverse=True)[0]``.

    Guarantees, each load-bearing:

    * **Never raises.**  ``cvcpkg info openssh`` used to traceback because the
      selection sort called a bare ``Version.parse`` on ``10.4p1+cvc.1``.
    * **Unparseable versions sort BELOW every parseable one** (rank 0 vs 1) but
      stay ordered among themselves by ``(natural key of upstream, +cvc.N)`` --
      preserving the resolver's intent that they remain installable yet never
      beat a well-formed version.  They are never collapsed to a sentinel (which
      would re-tie openssh/x264/llvm-cbe and let input order decide) and never
      sorted lexically (which flips ``10`` below ``9``).
    * **The +cvc.N revision is read from the STRING**, not the caller's
      argument, so two call sites that pass different arguments cannot produce
      different orders.  ``cvc_revision`` is a fallback used only when the
      string has no ``+cvc.N`` (the server catalog omits the suffix).
    * **Reuses ``Version._cmp_tuple``** for the parseable branch, so sort order
      and ``satisfies()`` range-filtering can never diverge.
    * **Total.**  The raw string is the final tiebreak, so two *distinct*
      version strings for one package can never tie and fall back to the
      caller's input order -- which is the entire bug class this replaces.
    """
    raw = (version or "").strip()
    m = _CVC_RE.search(raw)
    if m:
        rev = int(m.group(1))
        head = raw[: m.start()]
    else:
        rev = cvc_revision if cvc_revision is not None else 0
        head = raw
    try:
        v = Version.parse(raw)
    except ValueError:
        # rank 0: below every parseable version.  slot 0 differs from the
        # parseable branch, so the shape-incompatible slots are never compared
        # across ranks and sorted() over a mixed corpus still succeeds.
        return (0, _natural_key(head), rev, raw)
    return (1, v._cmp_tuple, rev, raw)
