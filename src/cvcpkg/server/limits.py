# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Server size limits and the size-string parser they share.

Deliberately dependency-free and side-effect-free: ``cvcpkg server run``
imports this to validate ``--max-upload-bytes`` *before* it exports the
setting into the environment.  If the parser lived in ``server.app`` that
import would execute the app module, freezing ``MAX_UPLOAD_BYTES`` at the
old value — uvicorn is handed ``cvcpkg.server.app:create_app`` as a string
and reuses whatever is already in ``sys.modules``.
"""

from __future__ import annotations

import re

# Maximum size of a published bundle, when nothing overrides it.
#
# 4 GiB is sized to admit the largest bundles we actually publish — the CUDA
# runtime wheels (nvidia-cudnn unpacks to ~2 GiB) and full toolchains — rather
# than the 1 GiB that predated them.
DEFAULT_MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024

_SIZE_RE = re.compile(r"(?i)\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)(?:i?b)?\s*")
_UNITS = "bkmgtp"


def parse_size(value: str, *, default: int | None = None) -> int:
    """Parse a size setting: a plain byte count or a human suffix.

    Accepts ``4294967296``, ``4GB`` / ``4G`` / ``4GiB``, ``512MB``, ``2TB``
    (case-insensitive, optional space).  Units are binary throughout — ``GB``
    and ``GiB`` both mean 1024³ — because these caps are compared against real
    on-disk sizes, and a "4GB" limit that silently meant 3.7 GiB would be its
    own bug report.

    With *default* set, an empty or unparseable value returns it instead of
    raising, so a typo in a deployment env var cannot stop an already-running
    deployment from booting.  The CLI passes ``default=None`` to surface the
    typo as a startup error where an operator will see it.
    """
    raw = (value or "").strip()
    if not raw:
        if default is None:
            raise ValueError("empty size")
        return default
    m = _SIZE_RE.fullmatch(raw)
    if not m:
        if default is None:
            raise ValueError(f"invalid size: {value!r}")
        return default
    number, unit = float(m.group(1)), m.group(2).lower()
    return int(number * (1024 ** _UNITS.index(unit or "b")))


def format_size(n: int) -> str:
    """Render a byte count for logs/banners, e.g. ``4 GiB``, ``512 MiB``."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.3g} {unit}"
        size /= 1024
    return f"{size:.3g} TiB"
