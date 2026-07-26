# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The combined single binary is multi-call: cvcpkg_launcher._want_server()
dispatches to the client CLI or the server by the invoked program name (argv[0])
or the CVCPKG_ENTRY override.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_LAUNCHER = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "cvcpkg_launcher.py"
_spec = importlib.util.spec_from_file_location("cvcpkg_launcher", _LAUNCHER)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)  # top-level is only defs — no side effects


@pytest.mark.parametrize(
    "argv0,want",
    [
        ("/usr/local/bin/cvcpkg", False),
        ("/usr/local/bin/cvcpkg-server", True),
        ("cvcpkg", False),
        ("cvcpkg-server", True),
        (r"C:\Program Files\cvcpkg\cvcpkg.exe", False),
        (r"C:\Program Files\cvcpkg\cvcpkg-server.exe", True),
    ],
)
def test_dispatch_by_argv0(monkeypatch, argv0, want):
    monkeypatch.delenv("CVCPKG_ENTRY", raising=False)
    monkeypatch.setattr(sys, "argv", [argv0, "--help"])
    assert launcher._want_server() is want


def test_env_override_wins_over_name(monkeypatch):
    # CVCPKG_ENTRY is for environments where symlinks aren't available.
    monkeypatch.setattr(sys, "argv", ["cvcpkg"])  # name says client
    monkeypatch.setenv("CVCPKG_ENTRY", "server")
    assert launcher._want_server() is True
    monkeypatch.setattr(sys, "argv", ["cvcpkg-server"])  # name says server
    monkeypatch.setenv("CVCPKG_ENTRY", "client")
    assert launcher._want_server() is False
