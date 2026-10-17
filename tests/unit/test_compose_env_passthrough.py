# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""docker-compose.production.yml must pass through every env var the code reads.

The dead-knob-in-production failure class: a var read by the app, documented in
``.env.production.example``, set by an operator — and silently dropped because
``backend.environment`` never lists it, so compose does not inject it into the
container.  This is cheaper than the subprocess launch tests and catches the
compose omission directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Env vars the CLI-login broker + session layer consume at runtime.  Each MUST
# be passed through the production compose file or it is dead in production.
_REQUIRED = [
    "CVCPKG_PUBLIC_URL",
    "CVCPKG_SESSION_TTL_SECONDS",
    "CVCPKG_REFRESH_TTL_SECONDS",
    "CVCPKG_SESSION_MAX_LIFETIME_SECONDS",
    "CVCPKG_CLI_MAX_ROLE",
    "CVCPKG_CLI_PAIRING_TTL_SECONDS",
    "CVCPKG_CLI_POLL_INTERVAL",
    "CVCPKG_AUTH_GC_INTERVAL",
    # /v1/catalog cache lifetime.
    "CVCPKG_CATALOG_CACHE_TTL",
    # Storage caps and yank retention.  On 2026-10-17 cvcpkg.org hit the
    # 100 GiB global cap, and setting the var in .env.production did nothing.
    "CVCPKG_GLOBAL_CACHE_STORAGE_LIMIT_BYTES",
    "CVCPKG_ORG_STORAGE_LIMIT_BYTES",
    "CVCPKG_YANK_RETENTION_DAYS",
    "CVCPKG_YANK_GC_INTERVAL",
    # Multi-issuer SSO: the display label for the bare provider and the JSON
    # array of additional providers.
    "CVCPKG_OIDC_DISPLAY_NAME",
    "CVCPKG_OIDC_EXTRA_PROVIDERS",
]


@pytest.mark.parametrize("var", _REQUIRED)
def test_compose_passes_var(var):
    compose = _ROOT / "docker-compose.production.yml"
    if not compose.is_file():
        pytest.skip("docker-compose.production.yml not present")
    text = compose.read_text()
    # A `${VAR:-}` passthrough (or any interpolation of it) under backend env.
    assert f"${{{var}" in text, f"{var} is not passed through docker-compose.production.yml"


@pytest.mark.parametrize("var", _REQUIRED)
def test_example_documents_var(var):
    example = _ROOT / ".env.production.example"
    if not example.is_file():
        pytest.skip(".env.production.example not present")
    assert f"{var}=" in example.read_text(), f"{var} is undocumented in .env.production.example"


def test_empty_passthrough_keeps_server_defaults():
    """``${VAR:-}`` injects an empty string; the server must still import.

    int("") raised at import for these before, so passing them through compose
    would have stopped the backend booting on any deployment that left them unset.
    """
    import os
    import subprocess
    import sys

    pytest.importorskip("fastapi", reason="server extras not installed")
    env = dict(os.environ)
    for var in (
        "CVCPKG_GLOBAL_CACHE_STORAGE_LIMIT_BYTES",
        "CVCPKG_ORG_STORAGE_LIMIT_BYTES",
        "CVCPKG_YANK_RETENTION_DAYS",
        "CVCPKG_YANK_GC_INTERVAL",
    ):
        env[var] = ""
    code = (
        "import cvcpkg.server.app as a;"
        "print(a.GLOBAL_CACHE_STORAGE_LIMIT_BYTES, a.ORG_STORAGE_LIMIT_BYTES,"
        " a._YANK_RETENTION_DAYS, a._YANK_GC_INTERVAL)"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [str(100 * 1024**3), str(10 * 1024**3), "0", "21600"]

    env["CVCPKG_GLOBAL_CACHE_STORAGE_LIMIT_BYTES"] = "250GB"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[0] == str(250 * 1024**3)
