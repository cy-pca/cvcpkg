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

_VER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: str
    build: str  # ignored in comparisons

    @classmethod
    def parse(cls, s: str) -> "Version":
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
    def _cmp_tuple(self) -> tuple[int, int, int, bool, str]:
        # Pre-release versions sort before the release (SemVer rule).
        return (self.major, self.minor, self.patch, not bool(self.pre), self.pre)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_tuple == other._cmp_tuple

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
            return v == self.ver
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
