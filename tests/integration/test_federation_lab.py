"""End-to-end federation lab as a gated integration test.

Runs ``lab/federation_lab.py``, which spins up three real cvcpkg-server
processes (edge-a/b/c), seeds a cross-domain private dependency chain, and
asserts federated resolution + per-registry auth + private-package invisibility
+ allowlist enforcement.  Skipped where the server extras aren't installed.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("uvicorn", reason="uvicorn required to run real servers")
pytest.importorskip("aiosqlite", reason="aiosqlite required")
pytest.importorskip("httpx", reason="httpx required")

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_federation_lab_e2e():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(REPO / "lab" / "federation_lab.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, f"federation lab failed:\n{proc.stdout}\n{proc.stderr}"
    assert "ALL ASSERTIONS PASSED" in proc.stdout
