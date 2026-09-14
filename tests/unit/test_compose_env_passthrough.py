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
