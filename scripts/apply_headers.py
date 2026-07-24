#!/usr/bin/env python3
"""Ensure first-party cvcpkg source files carry an SPDX + copyright header.

Sweeps every ``*.py`` file under ``src/cvcpkg/`` (excluding ``__pycache__``
and any vendored ``recipes/``/``third_party/`` trees, which are upstream
content rather than project source) and checks for a two-line header at the
very top of the file, before any docstring or code:

    # SPDX-License-Identifier: MIT
    # Copyright (c) 2026 CyberPC Angel, LLC

Per-recipe ``maintainer``/``maintainer_email`` fields in
``recipes/*/recipe.yaml`` are a separate, legitimate form of upstream-package
attribution and are intentionally out of scope for this script.

Usage::

    python scripts/apply_headers.py            # report files missing a header
    python scripts/apply_headers.py --check    # same, but exit 1 if any are missing (CI)
    python scripts/apply_headers.py --apply    # write the header into every file missing one
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SPDX_LINE = "# SPDX-License-Identifier: MIT"
COPYRIGHT_LINE = "# Copyright (c) 2026 CyberPC Angel, LLC"
HEADER = f"{SPDX_LINE}\n{COPYRIGHT_LINE}\n\n"

EXCLUDED_DIR_NAMES = {"__pycache__", "recipes", "third_party"}


def _iter_source_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts[:-1]
        if any(part in EXCLUDED_DIR_NAMES for part in parts):
            continue
        files.append(path)
    return files


def _has_header(text: str) -> bool:
    lines = text.splitlines()
    return len(lines) >= 2 and lines[0] == SPDX_LINE and lines[1] == COPYRIGHT_LINE


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any file is missing the header (CI mode). No changes made.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the header into every file missing one.",
    )
    args = p.parse_args()
    if args.check and args.apply:
        print("--check and --apply are mutually exclusive", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    src_root = repo_root / "src" / "cvcpkg"
    if not src_root.is_dir():
        print(f"source root not found: {src_root}", file=sys.stderr)
        return 2

    missing = [
        p for p in _iter_source_files(src_root) if not _has_header(p.read_text(encoding="utf-8"))
    ]

    if not missing:
        print("all first-party source files carry the SPDX/copyright header", flush=True)
        return 0

    for path in missing:
        print(f"missing header: {path.relative_to(repo_root)}", flush=True)
    print(f"total missing: {len(missing)}", flush=True)

    if args.apply:
        for path in missing:
            text = path.read_text(encoding="utf-8")
            path.write_text(HEADER + text, encoding="utf-8")
        print(f"wrote header into {len(missing)} file(s)", flush=True)
        return 0

    if args.check:
        return 1

    print("dry-run: no changes made. rerun with --apply to fix.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
