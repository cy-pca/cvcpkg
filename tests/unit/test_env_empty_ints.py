# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Importing the server must survive present-but-empty env vars.

docker-compose's ``${VAR:-}`` passthrough injects an unset variable as an EMPTY
string, not absence — so ``os.environ.get(name, default)`` returns ``""`` and a
naive ``int("")`` crashes the server on import.  This regression pins that every
int-valued env var the production compose passes through tolerates ``""``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")
pytest.importorskip("fastapi", reason="server extras not installed")

_ROOT = Path(__file__).resolve().parents[2]

# Every int-valued key under backend.environment in docker-compose.production.yml.
_INT_PASSTHROUGH = [
    "CVCPKG_AUTH_GC_INTERVAL",
    "CVCPKG_SESSION_TTL_SECONDS",
    "CVCPKG_REFRESH_TTL_SECONDS",
    "CVCPKG_SESSION_MAX_LIFETIME_SECONDS",
    "CVCPKG_CLI_PAIRING_TTL_SECONDS",
    "CVCPKG_CLI_POLL_INTERVAL",
]


def test_app_imports_with_empty_passthrough_ints():
    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    for key in _INT_PASSTHROUGH:
        env[key] = ""  # what compose injects when the operator leaves it unset
    env.pop("CVCPKG_MIRROR_MODE", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import cvcpkg.server.app"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
