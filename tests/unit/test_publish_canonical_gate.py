"""Canonical platform/arch gate on the publish surfaces.

``/v1/publish`` and ``/v1/upload/init`` accepted free-form ``platform`` /
``arch`` strings, so a misconfigured client (raw ``uname -m`` sending
``aarch64``, BSD ``amd64``) could mint an orphan catalog keyspace that no
canonical consumer ever queries. ``_reject_noncanonical_platform_arch``
closes that; this covers it directly.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi required")
from fastapi import HTTPException  # noqa: E402

from cvcpkg.server.app import _reject_noncanonical_platform_arch  # noqa: E402


@pytest.mark.parametrize(
    ("platform", "arch"),
    [
        ("linux", "x86_64"),
        ("macos", "arm64"),
        ("windows", "x86_64"),
        ("windows-gnu", "x86_64"),  # Phase-8 cross-toolchain target
        ("dragonflybsd", "x86_64"),  # detect_platform already emits it
        ("wasm", "wasm32"),
        ("any", "any"),  # platform-independent bundles
        ("", ""),  # back-compat: empty stays allowed
        ("freebsd", ""),
    ],
)
def test_canonical_values_pass(platform, arch):
    _reject_noncanonical_platform_arch(platform, arch)  # must not raise


@pytest.mark.parametrize(
    ("platform", "arch", "needle"),
    [
        ("linux", "aarch64", "did you mean 'arm64'"),
        ("linux", "amd64", "did you mean 'x86_64'"),
        ("linux", "x64", "did you mean 'x86_64'"),
        ("linux", "sparc9000", "non-canonical arch"),
    ],
)
def test_noncanonical_arch_rejected_with_hint(platform, arch, needle):
    with pytest.raises(HTTPException) as exc:
        _reject_noncanonical_platform_arch(platform, arch)
    assert exc.value.status_code == 422
    assert needle in exc.value.detail


@pytest.mark.parametrize("platform", ["ubuntu", "darwin", "win32", "linux-gnu"])
def test_noncanonical_platform_rejected(platform):
    with pytest.raises(HTTPException) as exc:
        _reject_noncanonical_platform_arch(platform, "x86_64")
    assert exc.value.status_code == 422
    assert "non-canonical platform" in exc.value.detail
