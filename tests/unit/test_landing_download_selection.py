"""Guards for the SPA's copy-paste download command (landing.py).

The manual-install script used to emit ``curl`` for ``allBuilds[0]`` -- the
first bundle the server returned.  Before version-ordering that could be a stale
version; it can still be the wrong PLATFORM (a Linux visitor handed a Windows
archive).  The JS itself is browser-rendered, so these are structural guards
that the platform-aware selection cannot silently regress to ``allBuilds[0]``.
The selection behaviour is exercised in a browser by the Playwright suite.
"""

from __future__ import annotations

from pathlib import Path

_LANDING = (
    Path(__file__).resolve().parents[2] / "src" / "cvcpkg" / "server" / "landing.py"
).read_text()


def test_visitor_platform_detection_exists():
    # The helper that maps the browser's OS to a cvcpkg platform string.
    assert "function detectVisitorPlatform" in _LANDING
    for token in ("'windows'", "'macos'", "'linux'"):
        assert token in _LANDING


def test_download_command_selects_by_visitor_platform_not_index_zero():
    # The build chosen for the curl command must be filtered by the visitor's
    # platform, not a bare allBuilds[0].
    assert "detectVisitorPlatform()" in _LANDING
    assert "b.platform === wantPlat" in _LANDING


def test_version_ordering_is_not_reimplemented_in_js():
    # The audit's rule: do NOT port version_sort_key to JS (that is how the
    # server/CLI orderings diverged).  Version order comes from the server;
    # the JS only steers platform.  A crude tripwire: no cvc-revision parsing.
    assert "cvc." not in _LANDING or "version_sort_key" not in _LANDING
    # The comment recording the intent must survive.
    assert "newest-version-first from the server" in _LANDING
