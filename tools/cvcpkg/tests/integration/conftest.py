"""Integration-test configuration.

Skip server-dependent tests when the test server is not reachable.
This allows ``pytest tests/`` from a plain checkout (no Docker)
to pass cleanly — only the unit tests will execute.
"""

from __future__ import annotations

import os

import pytest


def _server_reachable() -> bool:
    """Return True if the integration test server responds to /healthz."""
    url = os.environ.get("CVCPKG_TEST_SERVER_URL", "http://127.0.0.1:8421")
    try:
        import httpx

        resp = httpx.get(f"{url}/healthz", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


_REACHABLE = _server_reachable()


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests that need a running server."""
    if _REACHABLE:
        return
    skip_marker = pytest.mark.skip(reason="integration server not reachable")
    for item in items:
        # Only skip tests in files that hit the live server
        module = item.module.__name__ if item.module else ""
        if (
            "docker_integration" in module
            or "e2e_lifecycle" in module
            or "test_browser" in module
            or "test_browser_build_ui" in module
        ):
            item.add_marker(skip_marker)
