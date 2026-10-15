"""Shared fixtures for the unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_host_gpu_probe(monkeypatch):
    """Keep the real host's GPU out of capability results.

    ``host_capabilities("install")`` also asks the NVIDIA driver whether a GPU
    is present (``platform._INSTALL_PROBES``), so on a GPU workstation every
    exact-set assertion about capabilities would gain ``cuda``.  Tests of that
    probe patch it back in explicitly.
    """
    import cvcpkg.platform as plat

    monkeypatch.setattr(plat, "_INSTALL_PROBES", {})
