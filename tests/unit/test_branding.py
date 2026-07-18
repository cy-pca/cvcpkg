"""Tests for the CLI brand splash and the configurable site logo."""

from __future__ import annotations

import importlib
import io

import pytest

from cvcpkg import branding


class _Tty(io.StringIO):
    """A StringIO that claims to be a terminal."""

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


# ── color-mode detection ────────────────────────────────────────


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"COLORTERM": "truecolor", "TERM": "xterm-256color"}, "truecolor"),
        ({"COLORTERM": "24bit"}, "truecolor"),
        ({"TERM": "xterm-256color"}, "256"),
        ({"TERM": "xterm"}, "16"),
        ({"TERM": "dumb"}, "none"),
        ({"TERM": "xterm-256color", "NO_COLOR": "1"}, "none"),
        ({"TERM": "xterm", "NO_COLOR": ""}, "none"),  # NO_COLOR honored when empty
    ],
)
def test_color_mode_detection(monkeypatch, env, expected):
    for var in ("COLORTERM", "TERM", "NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(var, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    assert branding.color_mode(_Tty()) == expected


def test_color_mode_non_tty_is_none(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert branding.color_mode(io.StringIO()) == "none"


def test_force_color_overrides_non_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert branding.color_mode(io.StringIO()) == "truecolor"


# ── splash gating ───────────────────────────────────────────────


def test_splash_suppressed_for_non_tty(monkeypatch):
    """Piped/redirected output must stay machine-clean."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert branding.splash(io.StringIO()) == ""


def test_splash_shown_for_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert "cvcpkg" in branding.splash(_Tty()).lower() or branding.splash(_Tty())


# ── rendering ───────────────────────────────────────────────────


def test_render_is_plain_ascii_without_color():
    out = branding.render_splash("9.9.9", mode="none", width=100)
    assert all(ord(ch) < 128 for ch in out), "art must be 7-bit ASCII"
    assert "\033[" not in out, "no escapes in mode='none'"
    assert "9.9.9" in out
    assert "CyberPC Angel, LLC" in out


def test_render_emits_ansi_when_colored():
    out = branding.render_splash("1.0.0", mode="truecolor", width=100)
    r, g, b = branding._RGB["blue"]
    assert f"\033[38;2;{r};{g};{b}m" in out
    assert branding._RESET in out


def test_render_256_and_16_modes_differ():
    c256 = branding.render_splash("1.0.0", mode="256", width=100)
    c16 = branding.render_splash("1.0.0", mode="16", width=100)
    assert "\033[38;5;" in c256
    assert "\033[38;5;" not in c16
    assert "\033[9" in c16 or "\033[3" in c16


def test_narrow_terminal_uses_compact_form():
    wide = branding.render_splash("1.0.0", mode="none", width=100)
    narrow = branding.render_splash("1.0.0", mode="none", width=40)
    assert len(narrow.splitlines()) < len(wide.splitlines())
    assert "cvcpkg 1.0.0" in narrow
    assert "CyberPC Angel, LLC" in narrow


def test_art_fits_conventional_terminal():
    assert max(len(line) for line in branding._ART) <= 80


# ── configurable site logo ──────────────────────────────────────


def _reload_landing(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CVCPKG_SITE_LOGO", raising=False)
    else:
        monkeypatch.setenv("CVCPKG_SITE_LOGO", value)
    import cvcpkg.server.landing as landing

    return importlib.reload(landing)


def test_site_logo_defaults_to_bundled_gears(monkeypatch):
    landing = _reload_landing(monkeypatch, None)
    data, media = landing.brand_logo_asset()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert media == "image/png"
    assert landing.brand_logo_href() == "/assets/cyberpc-angel-gears.png"


def test_site_logo_local_file_override(monkeypatch, tmp_path):
    custom = tmp_path / "brand.svg"
    custom.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    landing = _reload_landing(monkeypatch, str(custom))
    data, media = landing.brand_logo_asset()
    assert data == custom.read_bytes()
    assert media == "image/svg+xml"
    # served through our own route, so the href stays local
    assert landing.brand_logo_href() == "/assets/cyberpc-angel-gears.png"


def test_site_logo_remote_url_is_linked_directly(monkeypatch):
    url = "https://cdn.example.com/logo.png"
    landing = _reload_landing(monkeypatch, url)
    assert landing.brand_logo_href() == url
    assert url in landing._head_html("t")


def test_site_logo_missing_file_falls_back(monkeypatch, tmp_path):
    landing = _reload_landing(monkeypatch, str(tmp_path / "nope.png"))
    data, media = landing.brand_logo_asset()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "falls back to bundled logo"
    assert media == "image/png"


def test_head_includes_icon_and_social_tags(monkeypatch):
    landing = _reload_landing(monkeypatch, None)
    head = landing._head_html("cvcpkg")
    assert '<link rel="icon"' in head
    assert "apple-touch-icon" in head
    assert 'property="og:image"' in head
    assert 'name="twitter:image"' in head


def test_cleanup_reload(monkeypatch):
    """Leave the module in its default state for other tests."""
    _reload_landing(monkeypatch, None)
