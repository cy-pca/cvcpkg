# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Landing page HTML for cvcpkg-server.

Serves a self-contained single-page index at ``/`` that displays
published packages grouped by name, supports sorting and filtering,
and links to per-package detail pages.  Uses Bulma CSS for styling.
"""

from __future__ import annotations

import functools
import html as _html
import json as _json
import logging
import os
import re
from dataclasses import dataclass

from cvcpkg import __version__
from cvcpkg.server.quickstart import QuickStartProfile
from cvcpkg.server.quickstart import profile as quickstart_profile

_log = logging.getLogger(__name__)

_GITHUB_REPO = os.environ.get("CVCPKG_GITHUB_REPO", "cy-pca/cvcpkg")
_GITHUB_URL = f"https://github.com/{_GITHUB_REPO}"

# The cvcpkg brand mark — the neon flaming-package icon, served self-hosted
# at /favicon.ico and /assets/cvcpkg-icon.png (see app.py).  The gears asset
# still ships for backward-compatible links.
_BRAND_LOGO_PATH = "/assets/cvcpkg-icon.png"
_BRAND_BANNER_PATH = "/assets/cvcpkg-banner.png"
_BRAND_HERO_PATH = "/assets/cvcpkg-hero.png"

# Bundled brand images that /assets/<name> may serve, mapped to their bytes on
# demand.  A fixed allow-list, so the route can never read arbitrary package
# files.
_BRAND_ASSETS = {
    "cvcpkg-icon.png",
    "cvcpkg-icon-180.png",
    "cvcpkg-icon-512.png",
    "cvcpkg-banner.png",
    "cvcpkg-hero.png",
    "cyberpc-angel-gears.png",
}

#: Operator override for the site icon.  Either an absolute ``http(s)://``
#: URL (linked directly, not proxied) or a path to a local image file
#: (served from the asset route).  Unset uses the bundled gears logo.
_SITE_LOGO = os.environ.get("CVCPKG_SITE_LOGO", "").strip()

_LOGO_MEDIA_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _logo_is_remote() -> bool:
    """True when the configured logo is an external URL rather than a file."""
    return _SITE_LOGO.startswith(("http://", "https://"))


def brand_logo_href() -> str:
    """URL used in ``<link rel="icon">`` and the small brand mark."""
    return _SITE_LOGO if _logo_is_remote() else _BRAND_LOGO_PATH


def brand_banner_href() -> str:
    """The horizontal logo (icon + wordmark), used in the navbar and og:image."""
    return _BRAND_BANNER_PATH


def brand_hero_href() -> str:
    """The large hero graphic on the landing page."""
    return _BRAND_HERO_PATH


@functools.lru_cache(maxsize=len(_BRAND_ASSETS) + 1)
def brand_asset(name: str) -> tuple[bytes, str]:
    """Return ``(bytes, media_type)`` for a bundled brand image, or ``(b"", ...)``.

    Only names in the ``_BRAND_ASSETS`` allow-list are served; anything else
    returns empty so the asset route answers 404 rather than reading an
    arbitrary package file.
    """
    if name not in _BRAND_ASSETS:
        return b"", "image/png"
    try:
        from importlib.resources import files as _res_files

        data = _res_files("cvcpkg.server").joinpath("assets", name).read_bytes()
        return data, "image/png"
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return b"", "image/png"


@functools.lru_cache(maxsize=1)
def brand_logo_asset() -> tuple[bytes, str]:
    """Return ``(image_bytes, media_type)`` for the served site icon.

    Honors ``CVCPKG_SITE_LOGO`` when it names a local file; otherwise falls
    back to the bundled CyberPC Angel gears PNG (package data, so it ships
    in the wheel).  Returns empty bytes rather than raising so the site
    still renders if an asset is missing or unreadable — a misconfigured
    override degrades to the bundled default rather than breaking the page.
    """
    if _SITE_LOGO and not _logo_is_remote():
        import pathlib

        custom = pathlib.Path(_SITE_LOGO).expanduser()
        try:
            data = custom.read_bytes()
            media = _LOGO_MEDIA_TYPES.get(custom.suffix.lower(), "image/png")
            return data, media
        except OSError:
            pass  # fall through to the bundled default

    try:
        from importlib.resources import files as _res_files

        data = _res_files("cvcpkg.server").joinpath("assets", "cvcpkg-icon.png").read_bytes()
        return data, "image/png"
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return b"", "image/png"


# The default-registry and fallback-repo assignments in each install script,
# exactly as written there.  The files carry the official deployment's values
# so they also run as-is from a checkout; install_script_asset swaps in the
# serving deployment's own.
_INSTALLER_DEFAULT_LINES = {
    "install.sh": ('DEFAULT_SERVER_URL="{}"', 'DEFAULT_GITHUB_REPO="{}"'),
    "install.ps1": ('$DefaultServerUrl = "{}"', '$DefaultGitHubRepo = "{}"'),
}
_OFFICIAL_SITE_URL = "https://cvcpkg.org"
_OFFICIAL_GITHUB_REPO = "cy-pca/cvcpkg"
# Both values land inside a double-quoted sh / PowerShell string, so nothing
# that could end or expand it (quotes, $, backtick, backslash) may get through.
_INSTALLER_URL_RE = re.compile(r"^https?://[A-Za-z0-9.:/_~%\[\]-]+$")
_INSTALLER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@functools.lru_cache(maxsize=2)
def install_script_asset(name: str) -> str:
    """Return the text of a bundled install script (``install.sh`` / ``install.ps1``).

    Read via ``importlib.resources`` (package data, like ``brand_logo_asset``)
    so it ships in the wheel and is served identically whether cvcpkg-server
    runs from a repo checkout or a ``pip install``.

    The scripts install the ``cvcpkg`` package from a registry, falling back
    to a GitHub repo's releases.  The copy served here defaults to THIS
    deployment's ``CVCPKG_SITE_URL`` and ``CVCPKG_GITHUB_REPO``, so a fork's
    ``/install.sh`` installs the fork's own builds rather than the official
    ones.  A value that is not safe to paste into the script keeps the
    official default (and is logged).
    """
    from importlib.resources import files as _res_files

    text = _res_files("cvcpkg.server").joinpath("assets", name).read_text(encoding="utf-8")
    url_line, repo_line = _INSTALLER_DEFAULT_LINES[name]
    for line, official, value, pattern in (
        (url_line, _OFFICIAL_SITE_URL, _SITE_URL, _INSTALLER_URL_RE),
        (repo_line, _OFFICIAL_GITHUB_REPO, _GITHUB_REPO, _INSTALLER_REPO_RE),
    ):
        if not pattern.match(value):
            _log.warning("%s: not baking in unsafe value %r; keeping %r", name, value, official)
            continue
        text = text.replace(line.format(official), line.format(value), 1)
    return text


def brand_logo_bytes() -> bytes:
    """Backwards-compatible accessor for just the icon bytes."""
    return brand_logo_asset()[0]


# Absolute site origin, used to build absolute URLs for social-preview
# (Open Graph / Twitter) image tags, which require a full URL.
_SITE_URL = os.environ.get("CVCPKG_SITE_URL", "https://cvcpkg.org").rstrip("/")

_SITE_TITLE = os.environ.get("CVCPKG_SITE_TITLE", "cvcpkg")
_SITE_TAGLINE = os.environ.get(
    "CVCPKG_SITE_TAGLINE", "A hermetic package manager for the scientific stack"
)
_SITE_HERO = os.environ.get(
    "CVCPKG_SITE_HERO",
    "We ship the tools we depend on, in public. A hermetic package manager and"
    " registry for the scientific stack \u2014 including WebAssembly builds of"
    " libraries nobody else ships \u2014 with curated LTS releases for"
    " reproducible downstream builds across Linux, macOS, Windows, the BSDs,"
    " Haiku, and the browser.",
)

# ── Shared CSS ───────────────────────────────────────────────────

_CSS = r"""
/* ── CyberPC Angel brand layer ──────────────────────────────────
   Neon magenta + cyan on dark navy, translucent panels with a soft
   glow, Gentium Book Basic body / VT323 terminal accents, pill
   buttons.  Matches cyberpcangel.com so the registry, the CLI splash,
   and the consultancy read as one product. */
:root {
  --neon-cyan: #00ffff;
  --neon-magenta: #ff00ff;
  --neon-pink: #ff2d95;
  --phosphor: rgba(200, 200, 255, 0.92);
  --bg: #0a0a12;
  --bg-alt: #0d0d1a;
  --navy-panel: rgba(0, 0, 64, 0.5);
  --panel-glow: 0 0 10px 2px rgba(100, 100, 255, 0.3);
}
html { background-color: var(--bg); }
body {
  font-family: "Gentium Book Basic", Georgia, serif;
  color: var(--phosphor);
  background-color: var(--bg);
}
h1, h2, h3, h4, .title, .subtitle { font-family: "Gentium Book Basic", Georgia, serif; }
code, pre, .is-family-monospace { font-family: "Consolas", "Monaco", monospace; }

.neon-cyan { color: var(--neon-cyan); text-shadow: 0 0 8px rgba(0,255,255,0.6); }
.neon-magenta { color: var(--neon-magenta); text-shadow: 0 0 8px rgba(255,0,255,0.6); }
.brand-wordmark {
  font-family: "VT323", monospace;
  letter-spacing: 0.02em;
  text-transform: lowercase;
}

/* Translucent navy panel with the house glow. */
.brand-panel {
  background: var(--navy-panel);
  border: 1px solid rgba(120, 120, 255, 0.18);
  box-shadow: var(--panel-glow);
  border-radius: 10px;
}
.brand-panel:hover { box-shadow: 0 0 14px 3px rgba(120,120,255,0.4); }

/* Pill buttons in the brand palette. */
.button.is-brand {
  border-radius: 9999px;
  border: 1px solid rgba(255,0,255,0.5);
  background: linear-gradient(90deg, rgba(255,0,255,0.18), rgba(0,255,255,0.18));
  color: #fff;
  text-shadow: 0 0 6px rgba(255,255,255,0.35);
  transition: box-shadow 0.15s, transform 0.1s;
}
.button.is-brand:hover {
  box-shadow: 0 0 14px 2px rgba(255,0,255,0.35), 0 0 18px 4px rgba(0,255,255,0.25);
  transform: translateY(-1px);
  color: #fff;
}
.button.is-rounded, .button.is-brand-outline { border-radius: 9999px; }
.button.is-brand-outline {
  background: transparent; border: 1px solid rgba(0,255,255,0.45);
  color: var(--neon-cyan);
}
.button.is-brand-outline:hover {
  box-shadow: 0 0 12px 1px rgba(0,255,255,0.35);
  border-color: var(--neon-cyan); color: var(--neon-cyan);
}

a, a.pkg-link { color: var(--neon-cyan); }
a:hover { color: #7fffff; }

/* Full-bleed neon hero for the landing page. */
.brand-hero {
  position: relative;
  background:
    radial-gradient(1200px 500px at 50% -10%, rgba(255,0,255,0.12), transparent 60%),
    radial-gradient(1000px 500px at 80% 120%, rgba(0,255,255,0.10), transparent 60%),
    var(--bg);
  border-bottom: 1px solid rgba(120,120,255,0.12);
  overflow: hidden;
}
.brand-hero .hero-art {
  max-width: 340px; width: 60%;
  filter: drop-shadow(0 0 24px rgba(255,0,255,0.35))
          drop-shadow(0 0 30px rgba(0,255,255,0.25));
}
.brand-hero-title {
  font-family: "VT323", monospace;
  font-size: clamp(3rem, 9vw, 6rem);
  line-height: 1;
  color: #fff;
  text-shadow: 0 0 10px rgba(0,255,255,0.7), 0 0 22px rgba(0,255,255,0.4);
}
.brand-hero-tagline {
  color: #7fffff;
  text-shadow: 0 0 6px rgba(0,255,255,0.4);
  font-size: 1.15rem;
}

/* Feature cards on the landing page. */
.feature-card { height: 100%; padding: 1.5rem; }
.feature-card .fa { text-shadow: 0 0 10px currentColor; }

/* Copyable install command. */
.install-cmd {
  font-family: "Consolas","Monaco",monospace;
  background: #05050c;
  border: 1px solid rgba(0,255,255,0.3);
  border-radius: 9999px;
  padding: 0.6rem 1.2rem;
  color: var(--neon-cyan);
  display: inline-flex; align-items: center; gap: 0.75rem;
}
.install-cmd .copy-btn { cursor: pointer; color: var(--phosphor); }
.install-cmd .copy-btn:hover { color: var(--neon-magenta); }

/* Empty search state. */
.search-empty { padding: 4rem 1rem; text-align: center; color: rgba(200,200,255,0.6); }
.search-empty .fa { text-shadow: 0 0 18px var(--neon-cyan); }

.navbar-logo { height: 2.3rem; width: auto;
  filter: drop-shadow(0 0 6px rgba(255,0,255,0.45)) drop-shadow(0 0 8px rgba(0,255,255,0.35)); }
.navbar-wordmark {
  font-size: 1.7rem; line-height: 1; margin-left: 0.5rem; color: #fff;
  text-shadow: 0 0 8px rgba(0,255,255,0.6), 0 0 14px rgba(255,0,255,0.35);
}

.hero-gradient {
  background:
    radial-gradient(900px 400px at 50% -20%, rgba(255,0,255,0.10), transparent 60%),
    linear-gradient(135deg, #14071f 0%, #0b1030 50%, #06121f 100%);
}

.logo-icon {
  width: 40px; height: 40px;
  border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  vertical-align: middle; margin-right: 10px;
}

.stat-box {
  text-align: center;
  padding: 1.25rem 1rem;
}
.stat-box .title { margin-bottom: 0.25rem !important; }

th.is-sortable { cursor: pointer; user-select: none; white-space: nowrap; }
th.is-sortable:hover { color: var(--neon-cyan); }
th.is-sorted { color: var(--neon-cyan); }
.sort-arrow { font-size: 0.65em; margin-left: 4px; }

.platform-tag.linux { background-color: rgba(72, 199, 116, 0.15); color: #48c774; }
.platform-tag.darwin { background-color: rgba(255, 221, 87, 0.15); color: #ffdd57; }
.platform-tag.windows { background-color: rgba(50, 115, 220, 0.15); color: #3273dc; }
.platform-tag.freebsd { background-color: rgba(255, 56, 56, 0.15); color: #ff3838; }
.platform-tag.netbsd { background-color: rgba(255, 145, 51, 0.15); color: #ff9133; }
.platform-tag.openbsd { background-color: rgba(241, 196, 15, 0.15); color: #f1c40f; }
.platform-tag.haiku { background-color: rgba(0, 169, 157, 0.15); color: #00a99d; }
.platform-tag.wasm { background-color: rgba(155, 89, 182, 0.15); color: #9b59b6; }
.platform-tag.wasi { background-color: rgba(230, 126, 34, 0.15); color: #e67e22; }

.release-tag { font-size: 0.75em; }
.release-tag.is-release { background-color: rgba(72, 199, 116, 0.15); color: #48c774; }
.release-tag.is-live { background-color: rgba(255, 221, 87, 0.15); color: #ffdd57; }

.empty-hero { padding: 4rem 1rem; }
.footer { padding: 2rem 1.5rem; }
.pkg-card { transition: transform 0.1s; }
.pkg-card:hover { transform: translateY(-2px); }
a.pkg-link { color: #3273dc; text-decoration: none; }
a.pkg-link:hover { text-decoration: underline; }

.badge-mainline {
  background: linear-gradient(135deg, #3273dc, #48c774);
  color: #fff; font-size: 0.7em; font-weight: 600;
  padding: 2px 8px; border-radius: 4px; vertical-align: middle;
  white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;
}
.badge-community {
  background-color: rgba(255, 221, 87, 0.15);
  color: #ffdd57; font-size: 0.7em; font-weight: 600;
  padding: 2px 8px; border-radius: 4px; vertical-align: middle;
  white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;
}
.badge-org {
  background: linear-gradient(135deg, #7957d5, #b86bff);
  color: #fff; font-size: 0.7em; font-weight: 600;
  padding: 2px 8px; border-radius: 4px; vertical-align: middle;
  white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;
}
@media screen and (max-width: 768px) {
  .badge-mainline, .badge-community, .badge-org {
    font-size: 0.6em; padding: 2px 6px;
  }
}
.note-card { border-left: 3px solid #3273dc; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }
.recipe-viewer { max-height: 640px; overflow-y: auto; }
.recipe-viewer pre { white-space: pre; word-break: normal; overflow-x: auto; }
.collapsible-header { cursor: pointer; user-select: none; }
.collapsible-header:hover { color: #3273dc; }
td.is-nowrap { white-space: nowrap; }
#builds-table tr.older-revision td { font-size: 0.9em; opacity: 0.85; }
.recipe-artifact { border-top: 1px solid #2a2a3e; }
.recipe-artifact:last-child { border-bottom: 1px solid #2a2a3e; }
.recipe-artifact-head {
  display: flex; align-items: center; gap: 0.25rem;
  padding: 0.45rem 0.25rem; cursor: pointer; user-select: none;
}
.recipe-artifact-head:hover { background-color: #1a1a2e; }
.recipe-artifact-body pre { margin: 0 0 0.5rem 0; max-height: 420px; overflow: auto; }

.navbar-dropdown {
  background-color: #1a1a2e !important;
  border-top: 2px solid #3273dc !important;
}
.navbar-dropdown .navbar-item {
  color: #f5f5f5 !important;
}
.navbar-dropdown .navbar-item:hover {
  background-color: #16213e !important;
  color: #3273dc !important;
}
.navbar-link::after { border-color: #f5f5f5 !important; }
.navbar-item.is-active:not(.has-dropdown),
.navbar-link.is-active { color: #3273dc !important; }

/* Live (WebSocket) pages: activity feed, builders */
.live-badge { display: inline-flex; align-items: center; gap: 0.4em; vertical-align: middle; }
.live-dot { width: 0.6em; height: 0.6em; border-radius: 50%; background: #7a7a7a; display: inline-block; }
.live-badge.is-live .live-dot { background: #48c774; animation: live-pulse 2s infinite; }
.live-badge.is-connecting .live-dot { background: #ffdd57; }
.live-badge.is-offline .live-dot { background: #f14668; }
@keyframes live-pulse {
  0% { box-shadow: 0 0 0 0 rgba(72, 199, 116, 0.6); }
  70% { box-shadow: 0 0 0 0.5em rgba(72, 199, 116, 0); }
  100% { box-shadow: 0 0 0 0 rgba(72, 199, 116, 0); }
}
@keyframes row-arrive { from { background-color: rgba(50, 115, 220, 0.35); } to { background-color: transparent; } }
tr.is-new td { animation: row-arrive 3s ease-out; }
.kind-btn.is-selected { border-color: #3273dc; background-color: rgba(50, 115, 220, 0.2); color: #fff; }
.builder-card { cursor: pointer; transition: border-color 0.2s, transform 0.1s; border: 1px solid transparent; }
.builder-card:hover { border-color: #3273dc; }
.builder-card:focus-visible { outline: 2px solid #3273dc; }
.meter { margin-bottom: 0.35rem; }
.meter-label { display: flex; justify-content: space-between; font-size: 0.75rem; color: #b5b5b5; }
.meter progress { margin-bottom: 0 !important; height: 0.45rem; }
.version-tag.is-current { background-color: rgba(72, 199, 116, 0.15); color: #48c774; }
.version-tag.is-other { background-color: rgba(255, 221, 87, 0.15); color: #ffdd57; }
.version-tag.is-unknown { background-color: rgba(122, 122, 122, 0.2); color: #b5b5b5; }
.detail-grid { display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem; font-size: 0.9rem; }
.detail-grid dt { color: #b5b5b5; }
.detail-grid dd { margin: 0; color: #f5f5f5; overflow-wrap: anywhere; }
"""

# ── Shared HTML fragments ────────────────────────────────────────


_NAVBAR_JS = r"""
(function() {
  // Burger toggle
  const burger = document.querySelector('.navbar-burger');
  if (burger) {
    burger.addEventListener('click', () => {
      burger.classList.toggle('is-active');
      document.getElementById(burger.dataset.target).classList.toggle('is-active');
      burger.setAttribute('aria-expanded', burger.classList.contains('is-active'));
    });
  }
  // Dropdown: click-to-toggle for touch devices
  document.querySelectorAll('.navbar-item.has-dropdown').forEach(dd => {
    const link = dd.querySelector('.navbar-link');
    if (link) {
      link.addEventListener('click', e => {
        e.preventDefault();
        // Close other open dropdowns
        document.querySelectorAll('.navbar-item.has-dropdown.is-active').forEach(other => {
          if (other !== dd) other.classList.remove('is-active');
        });
        dd.classList.toggle('is-active');
      });
    }
  });
  // Close menus on outside tap
  document.addEventListener('click', e => {
    if (!e.target.closest('.navbar-item.has-dropdown')) {
      document.querySelectorAll('.navbar-item.has-dropdown.is-active').forEach(
        dd => dd.classList.remove('is-active')
      );
    }
    if (!e.target.closest('.navbar-burger') && !e.target.closest('.navbar-menu')) {
      const b = document.querySelector('.navbar-burger');
      if (b && b.classList.contains('is-active')) {
        b.classList.remove('is-active');
        document.getElementById(b.dataset.target).classList.remove('is-active');
      }
    }
  });
  // Active navbar item
  const path = location.pathname;
  document.querySelectorAll('#navMenu .navbar-item[href]').forEach(a => {
    const href = a.getAttribute('href');
    if (href === path || (href !== '/' && path.startsWith(href))) {
      a.classList.add('is-active');
      // If inside a dropdown, also mark the parent
      const dd = a.closest('.navbar-item.has-dropdown');
      if (dd) dd.querySelector('.navbar-link').classList.add('is-active');
    }
  });
})()
"""


def _navbar_html() -> str:
    return f"""<nav class="navbar is-dark" role="navigation" aria-label="main navigation">
  <div class="container">
    <div class="navbar-brand">
      <a class="navbar-item" href="/">
        <img class="navbar-logo" src="{_BRAND_LOGO_PATH}" alt="cvcpkg" />
        <span class="navbar-wordmark brand-wordmark">cvcpkg</span>
        <span class="tag is-dark is-rounded ml-2">v{__version__}</span>
      </a>
      <a role="button" class="navbar-burger" aria-label="menu" aria-expanded="false" data-target="navMenu">
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
      </a>
    </div>
    <div id="navMenu" class="navbar-menu">
      <div class="navbar-end">
        <a class="navbar-item" href="/search">
          <span class="icon"><i class="fas fa-search"></i></span><span>Search</span>
        </a>
        <a class="navbar-item" href="/activity">
          <span class="icon"><i class="fas fa-bolt"></i></span><span>Activity</span>
        </a>
        <div class="navbar-item has-dropdown is-hoverable">
          <a class="navbar-link">
            <span class="icon"><i class="fas fa-book"></i></span><span>Docs</span>
          </a>
          <div class="navbar-dropdown is-right is-boxed">
            <a class="navbar-item" href="/guide">
              <span class="icon"><i class="fas fa-rocket"></i></span><span>Getting Started</span>
            </a>
            <a class="navbar-item" href="/docs">
              <span class="icon"><i class="fas fa-code"></i></span><span>API Reference</span>
            </a>
          </div>
        </div>
        <div class="navbar-item has-dropdown is-hoverable">
          <a class="navbar-link">
            <span class="icon"><i class="fas fa-hard-hat"></i></span><span>Build</span>
          </a>
          <div class="navbar-dropdown is-right is-boxed">
            <a class="navbar-item" href="/builders">
              <span class="icon"><i class="fas fa-server"></i></span><span>Builders</span>
            </a>
            <a class="navbar-item" href="/builds">
              <span class="icon"><i class="fas fa-hammer"></i></span><span>Build Jobs</span>
            </a>
            <a class="navbar-item" href="/recipes">
              <span class="icon"><i class="fas fa-scroll"></i></span><span>Recipes</span>
            </a>
          </div>
        </div>
        <a class="navbar-item" href="/orgs">
          <span class="icon"><i class="fas fa-building"></i></span><span>Organizations</span>
        </a>
        <a class="navbar-item" href="/tags">
          <span class="icon"><i class="fas fa-tags"></i></span><span>Tags</span>
        </a>
        <a class="navbar-item" href="/v1/catalog">
          <span class="icon"><i class="fas fa-list"></i></span><span>Catalog</span>
        </a>
        <a class="navbar-item" href="{_GITHUB_URL}">
          <span class="icon"><i class="fab fa-github"></i></span><span>GitHub</span>
        </a>
        <a class="navbar-item" href="/account">
          <span class="icon"><i class="fas fa-user"></i></span><span>Account</span>
        </a>
      </div>
    </div>
  </div>
</nav>"""


def _footer_html() -> str:
    return f"""<footer class="footer has-background-black-ter has-text-grey-light">
  <div class="content has-text-centered">
    <p>
      <a href="{_GITHUB_URL}" class="has-text-grey-light">
        <span class="icon"><i class="fab fa-github"></i></span> cvcpkg
      </a>
      &mdash; cross-platform binary package archive for scientific computing
    </p>
    <p class="is-size-7 has-text-grey">
      &copy; 2026 <a href="https://cyberpcangel.com" class="has-text-grey">CyberPC Angel, LLC</a>
      &mdash; released under the <a href="{_GITHUB_URL}/blob/master/LICENSE" class="has-text-grey">MIT License</a>
    </p>
  </div>
</footer>"""


def _head_html(title: str, canonical: str = "") -> str:
    href = brand_logo_href()
    # Social scrapers need an absolute URL; a configured remote logo already
    # is one, a served asset gets the site origin prepended.  The wide banner
    # makes a better social card than the square icon.
    banner = brand_banner_href()
    og_image = href if _logo_is_remote() else f"{_SITE_URL}{banner}"
    canonical_tags = (
        f'\n  <link rel="canonical" href="{_SITE_URL}{canonical}" />'
        f'\n  <meta property="og:url" content="{_SITE_URL}{canonical}" />'
        if canonical
        else ""
    )
    return f"""<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="icon" href="{href}" />
  <link rel="apple-touch-icon" href="/assets/cvcpkg-icon-180.png" />
  <meta name="theme-color" content="#0a0a12" />
  <meta property="og:title" content="{title}" />
  <meta property="og:type" content="website" />
  <meta property="og:image" content="{og_image}" />{canonical_tags}
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="{og_image}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Gentium+Book+Basic:ital,wght@0,400;0,700;1,400&family=VT323&display=swap" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css" />
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"
        integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA=="
        crossorigin="anonymous" referrerpolicy="no-referrer" />
  <style>{_CSS}</style>
</head>"""


# ── Shared JS helpers ────────────────────────────────────────────

_HELPERS_JS = r"""
function esc(s) {
  if (s == null) return '';
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(s)));
  return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function platformTag(platform) {
  if (!platform) return '<span class="has-text-grey">&mdash;</span>';
  let cls = '';
  const lp = platform.toLowerCase();
  if (lp.includes('linux')) cls = 'linux';
  else if (lp.includes('darwin') || lp.includes('macos')) cls = 'darwin';
  else if (lp.includes('win')) cls = 'windows';
  else if (lp === 'freebsd') cls = 'freebsd';
  else if (lp === 'netbsd') cls = 'netbsd';
  else if (lp === 'openbsd') cls = 'openbsd';
  else if (lp === 'haiku') cls = 'haiku';
  else if (lp === 'wasm') cls = 'wasm';
  else if (lp === 'wasi') cls = 'wasi';
  return '<span class="tag is-rounded platform-tag ' + cls + '">' + esc(platform) + '</span>';
}
function releaseTag(tag) {
  if (!tag) return '<span class="tag is-rounded release-tag is-live">live</span>';
  return '<span class="tag is-rounded release-tag is-release">' + esc(tag) + '</span>';
}
function fmtSize(bytes) {
  if (!bytes) return '&mdash;';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0, sz = bytes;
  while (sz >= 1024 && i < units.length - 1) { sz /= 1024; i++; }
  return sz.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}
function fmtSizeLarge(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}
// Best-effort guess of the visitor's OS, to steer the copy-paste download
// command toward an archive they can actually use.  Returns a cvcpkg platform
// string ('linux'/'macos'/'windows') or '' when undetectable.  Shared: the
// package page's manual-install script needs it as much as the landing page.
function detectVisitorPlatform() {
  const s = ((navigator.platform || '') + ' ' + (navigator.userAgent || ''));
  if (/Win/i.test(s)) return 'windows';
  if (/Mac/i.test(s)) return 'macos';
  if (/Linux|X11|Android/i.test(s)) return 'linux';
  return '';
}
function fmtDate(iso) {
  if (!iso) return '&mdash;';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}
"""

# ── Landing page JS ─────────────────────────────────────────────

_LANDING_JS = r"""
// Server-driven search. The full package list may reach 10s of thousands of
// entries, so we never load it into memory — every filter change fires a
// debounced request to /v1/search and re-renders from the response.

const PAGE_SIZE = 100;

let recipeNames = [];
let recipeMeta = {};
let currentSort = { key: 'name', dir: 'asc' };

// Filter/search state — kept in sync with URL for shareable links.
let state = {
  q: '',
  platform: '',
  release: '',
  tag: '',
  arch: '',
  link: '',
  build_type: '',
  offset: 0,
};

// Accumulated pages so "Load more" appends rather than replaces.
let loadedPackages = [];
let lastResponse = null;
let searchSeq = 0;      // race guard for out-of-order responses
let searchTimer = null; // debounce handle

function _debounce(fn, ms) {
  return function() {
    const args = arguments;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => fn.apply(null, args), ms);
  };
}

function _buildSearchQuery(overrides) {
  const s = Object.assign({}, state, overrides || {});
  const params = new URLSearchParams();
  if (s.q) params.set('q', s.q);
  if (s.platform) params.set('platform', s.platform);
  if (s.arch) params.set('arch', s.arch);
  if (s.link) params.set('link', s.link);
  if (s.build_type) params.set('build_type', s.build_type);
  if (s.release) params.set('release', s.release);
  if (s.tag) params.set('tag', s.tag);
  params.set('limit', String(PAGE_SIZE));
  params.set('offset', String(s.offset || 0));
  params.set('facets', 'true');
  return params.toString();
}

async function runSearch(opts) {
  opts = opts || {};
  const append = !!opts.append;
  if (!append) {
    state.offset = 0;
    loadedPackages = [];
  }
  const seq = ++searchSeq;
  const url = '/v1/search?' + _buildSearchQuery();
  const tbody = document.getElementById('pkg-body');
  if (!append) {
    tbody.innerHTML = `
      <tr><td colspan="6" class="has-text-centered py-6">
        <span class="icon is-large has-text-link">
          <i class="fas fa-spinner fa-spin fa-2x"></i>
        </span>
      </td></tr>`;
  }
  let data;
  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
  } catch (err) {
    if (seq !== searchSeq) return; // stale
    tbody.innerHTML =
      '<tr><td colspan="6" class="has-text-centered has-text-grey-light">'
      + 'Search failed: ' + esc(err.message || String(err)) + '</td></tr>';
    return;
  }
  if (seq !== searchSeq) return; // a newer request already fired
  lastResponse = data;
  const pkgs = data.packages || [];
  // Enrich with recipe metadata (license/description fallback).
  pkgs.forEach(p => {
    const m = recipeMeta[p.name];
    if (!m) return;
    if (!p.license && m.license) p.license = m.license;
    if (!p.description && m.description) p.description = m.description;
  });
  loadedPackages = loadedPackages.concat(pkgs);
  // Render the results FIRST so that a later step throwing can never leave the
  // table stuck on its loading spinner (see below).  updateStatsFromResponse
  // and updateFilterOptions are both no-ops on pages that lack their targets.
  renderResults(data);
  updateStatsFromResponse(data);
  updateFilterOptions(data.facets || {});
}

function updateStatsFromResponse(data) {
  const facets = data.facets || {};
  // The dedicated /search page has no stat-* hero (those live only on the
  // landing page), so every write here must tolerate a missing element --
  // otherwise the first getElementById(...).textContent throws a TypeError
  // that aborts runSearch before renderResults runs, and every query renders
  // zero results.  Guard like _syncSelect / showSearchEmptyState already do.
  const setText = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  setText('stat-packages', data.package_count || 0);
  setText('stat-builds', data.total || 0);
  setText('stat-platforms', (facets.platforms || []).length);
  setText('stat-size', fmtSizeLarge(data.total_size_bytes || 0));
}

function _syncSelect(id, buckets, keepValue) {
  const sel = document.getElementById(id);
  if (!sel) return;
  const current = keepValue != null ? keepValue : sel.value;
  // Preserve the first placeholder option (the "All ..." entry) and any
  // static options declared in HTML (identified by data-static="true").
  const preserved = [];
  Array.from(sel.options).forEach(o => {
    if (o.value === '' || o.dataset.static === 'true') preserved.push(o);
  });
  sel.innerHTML = '';
  preserved.forEach(o => sel.appendChild(o));
  const seen = new Set(preserved.map(o => o.value));
  (buckets || []).forEach(b => {
    if (seen.has(b.value)) return;
    const opt = document.createElement('option');
    opt.value = b.value;
    opt.textContent = b.count ? (b.value + ' (' + b.count + ')') : b.value;
    sel.appendChild(opt);
    seen.add(b.value);
  });
  // Restore previous selection when still present, otherwise fall back to
  // the empty "All ..." placeholder so we don't silently mutate state.
  if (seen.has(current)) {
    sel.value = current;
  } else {
    sel.value = '';
  }
}

function updateFilterOptions(facets) {
  _syncSelect('platform-filter', facets.platforms || [], state.platform);
  _syncSelect('arch-filter', facets.archs || [], state.arch);
  _syncSelect('release-filter', facets.releases || [], state.release);
  _syncSelect('tag-filter', facets.tags || [], state.tag);
}

function groupBundlesByName(pkgs) {
  const groups = {};
  pkgs.forEach(p => {
    const key = p.org ? p.org + '/' + p.name : p.name;
    if (!groups[key]) {
      groups[key] = {
        name: p.name,
        org: p.org || '',
        displayName: key,
        version: p.version,
        builds: [],
        platforms: new Set(),
        archs: new Set(),
        totalSize: 0,
      };
    }
    const g = groups[key];
    g.builds.push(p);
    if (p.platform) g.platforms.add(p.platform);
    if (p.arch) g.archs.add(p.arch);
    g.totalSize += p.size_bytes || 0;
  });
  return Object.values(groups);
}

function renderResults(data) {
  const grouped = groupBundlesByName(loadedPackages);
  grouped.sort((a, b) => {
    let va, vb;
    if (currentSort.key === 'builds') {
      va = a.builds.length; vb = b.builds.length;
      return currentSort.dir === 'asc' ? va - vb : vb - va;
    }
    if (currentSort.key === 'totalSize') {
      va = a.totalSize; vb = b.totalSize;
      return currentSort.dir === 'asc' ? va - vb : vb - va;
    }
    va = (a[currentSort.key] || '').toLowerCase();
    vb = (b[currentSort.key] || '').toLowerCase();
    if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
    if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
    return 0;
  });

  const totalBuilds = data.total || 0;
  const totalPackages = data.package_count || 0;
  const shownBuilds = loadedPackages.length;
  const shownPackages = grouped.length;
  const countEl = document.getElementById('pkg-count');
  if (shownPackages < totalPackages) {
    countEl.textContent =
      'Showing ' + shownPackages + ' of ' + totalPackages
      + (totalPackages === 1 ? ' package' : ' packages')
      + ' (' + shownBuilds + ' / ' + totalBuilds + ' builds)';
  } else {
    countEl.textContent =
      totalPackages + (totalPackages === 1 ? ' package' : ' packages')
      + ' (' + totalBuilds + (totalBuilds === 1 ? ' build' : ' builds') + ')';
  }

  const tbody = document.getElementById('pkg-body');
  if (grouped.length === 0) {
    const hasFilter = state.q || state.platform || state.release || state.tag
                      || state.arch || state.link || state.build_type;
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-hero has-text-centered">
          <span class="icon is-large has-text-grey-light"><i class="fas fa-box-open fa-3x"></i></span>
          <p class="title is-5 has-text-grey-light mt-4">
            ${hasFilter ? 'No packages match your filter' : 'No packages published yet'}
          </p>
          <p class="subtitle is-6 has-text-grey">
            ${hasFilter ? 'Try adjusting your search or platform filter.'
                        : 'Publish your first package to see it here.'}
          </p>
        </div>
      </td></tr>`;
  } else {
    tbody.innerHTML = grouped.map(g => {
      const isOrg = !!g.org;
      const pkgUrl = isOrg
        ? '/package/' + encodeURIComponent(g.org) + '/' + encodeURIComponent(g.name)
        : '/package/' + encodeURIComponent(g.name);
      return `
      <tr class="pkg-card">
        <td>
          <a class="pkg-link" href="${pkgUrl}">
            ${isOrg ? '<span class="has-text-grey-light">' + esc(g.org) + '/</span>' : ''}
            <strong>${esc(g.name)}</strong>
          </a>
        </td>
        <td><code>${esc(g.version)}</code></td>
        <td>${[...g.platforms].sort().map(p => platformTag(p)).join(' ')}</td>
        <td>${[...g.archs].sort().map(a => '<span class="tag is-small is-dark is-rounded ml-1">' + esc(a) + '</span>').join(' ')}</td>
        <td><span class="tag is-dark is-rounded">${g.builds.length}</span></td>
        <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(g.totalSize)}</span></td>
      </tr>
    `}).join('');
  }

  document.querySelectorAll('th.is-sortable').forEach(th => {
    const arrow = th.querySelector('.sort-arrow');
    if (th.dataset.key === currentSort.key) {
      th.classList.add('is-sorted');
      arrow.textContent = currentSort.dir === 'asc' ? ' \u25B2' : ' \u25BC';
    } else {
      th.classList.remove('is-sorted');
      arrow.textContent = '';
    }
  });

  // "Load more" button.
  const more = document.getElementById('load-more');
  if (more) {
    if (shownPackages < totalPackages) {
      more.style.display = '';
      more.disabled = false;
      more.textContent = 'Load more (' + (totalPackages - shownPackages) + ' packages remaining)';
    } else {
      more.style.display = 'none';
    }
  }
}

function sortBy(key) {
  if (currentSort.key === key) {
    currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    currentSort = { key, dir: 'asc' };
  }
  // Sort locally over already-loaded pages — no refetch.
  if (lastResponse) renderResults(lastResponse);
}

const debouncedSearch = _debounce(maybeSearch, 250);

let _recipeMetaLoaded = false;

// Recipe metadata (license/description fallback + the mainline/community
// badge) is large, so it is fetched lazily on the first actual search rather
// than on page load -- the search page must open instantly.
async function ensureRecipeMeta() {
  if (_recipeMetaLoaded) return;
  _recipeMetaLoaded = true;
  try {
    const dresp = await fetch('/v1/deps');
    const ddata = await dresp.json();
    recipeMeta = ddata.meta || {};
    recipeNames = ddata.recipe_names || [];
  } catch (_) {}
}

// Load recipe metadata (a license/description fallback) AFTER the search, never
// before or concurrently.  ensureRecipeMeta() fetches the whole recipe catalog
// from /v1/deps, which is slow (~10s for ~900 recipes) and the server serializes
// it against /v1/search: firing both at once made a `?q=` deep link's /v1/search
// wait ~15s behind /v1/deps, so the results table spun until it finished — the
// search only appeared to work after "clear the field and retype", because by
// then /v1/deps had completed and the retyped /v1/search ran alone (~1s).
// Callers therefore `await runSearch()` first and call this only afterwards; it
// fire-and-forgets and re-enriches on-screen results once the metadata arrives.
// (The metadata is not currently shown in the results columns, but the enrich
// hook is kept for when it is.)
function loadRecipeMetaInBackground() {
  if (_recipeMetaLoaded) return;  // already loaded; runSearch enriches inline
  ensureRecipeMeta().then(() => {
    if (!lastResponse || !loadedPackages.length) return;
    loadedPackages.forEach(p => {
      const m = recipeMeta[p.name];
      if (!m) return;
      if (!p.license && m.license) p.license = m.license;
      if (!p.description && m.description) p.description = m.description;
    });
    renderResults(lastResponse);
  });
}

function _hasCriteria() {
  return !!(state.q || state.platform || state.arch || state.tag || state.release
            || state.link || state.build_type);
}

function showSearchEmptyState() {
  const tbody = document.getElementById('pkg-body');
  if (tbody) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="search-empty">'
      + '<p class="mb-3"><span class="icon is-large"><i class="fas fa-magnifying-glass fa-2x"></i></span></p>'
      + '<p class="is-size-5">Search the cvcpkg registry</p>'
      + '<p class="is-size-7 mt-2">Type a package name, or pick a platform, architecture, or tag to begin.</p>'
      + '</td></tr>';
  }
  const cnt = document.getElementById('pkg-count');
  if (cnt) cnt.innerHTML = '&nbsp;';
  const more = document.getElementById('load-more');
  if (more) more.style.display = 'none';
}

// Run a search only when the user has entered criteria; otherwise rest in the
// empty state.  Fire the query FIRST, then load recipe metadata — never before
// or concurrently (see loadRecipeMetaInBackground for why).
async function maybeSearch() {
  if (!_hasCriteria()) { showSearchEmptyState(); return; }
  await runSearch();
  loadRecipeMetaInBackground();
}

async function init() {
  // Deep link: /search?q=... (or a filter param) opens directly on results.
  const params = new URLSearchParams(location.search);
  const q = params.get('q') || '';
  if (q) {
    const box = document.getElementById('search');
    if (box) box.value = q;
    state.q = q;
  }
  for (const k of ['platform', 'arch', 'tag', 'release']) {
    const v = params.get(k);
    if (v) state[k] = v;
  }
  if (_hasCriteria()) {
    await runSearch();
    loadRecipeMetaInBackground();
  } else {
    showSearchEmptyState();
    loadRecipeMetaInBackground();
  }
}

function loadMore() {
  const btn = document.getElementById('load-more');
  if (btn) btn.disabled = true;
  state.offset = loadedPackages.length;
  runSearch({ append: true });
}
"""


def landing_html() -> str:
    """The landing page: branded, fast, and free of any package listing.

    Deliberately makes **no** call to ``/v1/search`` or ``/v1/deps`` — those
    scan the whole registry (tens of thousands of variants) and made the front
    page slow to first paint.  Package discovery lives on ``/search`` now; this
    page loads instantly and does a single cheap ``/healthz`` fetch for the one
    live number it shows.
    """
    page_title = _html.escape(f"{_SITE_TITLE} — {_SITE_TAGLINE}", quote=False)
    hero = _html.escape(_SITE_HERO, quote=False)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(page_title)}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<!-- Hero -->
<section class="hero brand-hero is-medium">
  <div class="hero-body">
    <div class="container has-text-centered">
      <img class="hero-art mb-4" src="{_BRAND_HERO_PATH}" alt="cvcpkg" />
      <h1 class="brand-hero-title brand-wordmark mb-3">{_html.escape(_SITE_TITLE)}</h1>
      <p class="brand-hero-tagline mx-auto mb-5" style="max-width: 760px;">{hero}</p>

      <form action="/search" method="get" class="mb-4">
        <div class="field has-addons has-addons-centered">
          <div class="control has-icons-left is-expanded" style="max-width: 460px;">
            <input class="input is-medium is-rounded is-dark" type="text" name="q"
                   placeholder="Search 4,000+ packages…" autofocus />
            <span class="icon is-left"><i class="fas fa-search"></i></span>
          </div>
          <div class="control">
            <button class="button is-medium is-brand" type="submit">Search</button>
          </div>
        </div>
      </form>

      <div class="buttons is-centered">
        <a href="/search" class="button is-brand-outline">
          <span class="icon"><i class="fas fa-box-open"></i></span><span>Browse packages</span>
        </a>
        <a href="/guide" class="button is-brand-outline">
          <span class="icon"><i class="fas fa-rocket"></i></span><span>Get started</span>
        </a>
        <a href="{_GITHUB_URL}" class="button is-brand-outline">
          <span class="icon"><i class="fab fa-github"></i></span><span>Source</span>
        </a>
      </div>
    </div>
  </div>
</section>

<!-- Install -->
<section class="section pt-5 pb-5 has-background-black-ter">
  <div class="container has-text-centered">
    <p class="heading has-text-grey-light mb-3">Install in one line</p>
    <span class="install-cmd">
      <span id="install-cmd-text">curl -fsSL {_SITE_URL}/install.sh | sh</span>
      <span class="copy-btn" id="copy-install" title="Copy"><i class="fas fa-copy"></i></span>
    </span>
    <p class="is-size-7 has-text-grey mt-3">
      Custom location: <code class="is-family-monospace">curl -fsSL {_SITE_URL}/install.sh | sh -s -- --install-dir /opt/cvcpkg/bin</code>
    </p>
    <p class="is-size-7 has-text-grey mt-2">
      Windows: <code class="is-family-monospace">irm {_SITE_URL}/install.ps1 | iex</code>
    </p>
    <p class="is-size-7 has-text-grey mt-2">
      Windows (custom location): <code class="is-family-monospace">&amp; ([scriptblock]::Create((irm {_SITE_URL}/install.ps1))) -InstallDir C:\\tools\\cvcpkg</code>
    </p>
  </div>
</section>

<!-- Stats -->
<section class="section pt-4 pb-4">
  <div class="container">
    <div class="columns is-mobile is-multiline has-text-centered">
      <div class="column is-3-desktop is-6-mobile">
        <p class="title is-3 neon-cyan" id="stat-packages">&mdash;</p>
        <p class="heading has-text-grey-light">Packages</p>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <p class="title is-3 neon-magenta">10+</p>
        <p class="heading has-text-grey-light">Platforms</p>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <p class="title is-3 neon-cyan">Hermetic</p>
        <p class="heading has-text-grey-light">Self-contained builds</p>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <p class="title is-3 neon-magenta">LTS</p>
        <p class="heading has-text-grey-light">Reproducible releases</p>
      </div>
    </div>
  </div>
</section>

<!-- Features -->
<section class="section pt-4 pb-6">
  <div class="container">
    <div class="columns is-multiline">
      <div class="column is-3-desktop is-6-tablet">
        <div class="brand-panel feature-card">
          <p class="mb-3"><span class="icon is-large neon-cyan"><i class="fas fa-cube fa-2x"></i></span></p>
          <p class="title is-5 has-text-white">Hermetic &amp; reproducible</p>
          <p class="is-size-7">Pinned, self-contained binaries and curated LTS releases, so a
          downstream build resolves the same closure today and next year.</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-tablet">
        <div class="brand-panel feature-card">
          <p class="mb-3"><span class="icon is-large neon-magenta"><i class="fas fa-fire fa-2x"></i></span></p>
          <p class="title is-5 has-text-white">WebAssembly, shipped</p>
          <p class="is-size-7">Threaded WebAssembly builds of scientific libraries nobody else
          publishes &mdash; the same registry, the same tool, straight to the browser.</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-tablet">
        <div class="brand-panel feature-card">
          <p class="mb-3"><span class="icon is-large neon-cyan"><i class="fas fa-layer-group fa-2x"></i></span></p>
          <p class="title is-5 has-text-white">Every platform</p>
          <p class="is-size-7">Linux, macOS, Windows, FreeBSD, NetBSD, OpenBSD, Haiku, and WebAssembly
          &mdash; one manifest, prebuilt everywhere.</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-tablet">
        <div class="brand-panel feature-card">
          <p class="mb-3"><span class="icon is-large neon-magenta"><i class="fas fa-terminal fa-2x"></i></span></p>
          <p class="title is-5 has-text-white">One tool</p>
          <p class="is-size-7">Build, publish, and install from a single CLI backed by a public
          registry &mdash; with a remote builder fleet doing the cross-compiles.</p>
        </div>
      </div>
    </div>

    <div class="has-text-centered mt-5">
      <div class="buttons is-centered">
        <a href="/builders" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-server"></i></span><span>Builders</span></a>
        <a href="/builds" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-hammer"></i></span><span>Build Jobs</span></a>
        <a href="/recipes" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-scroll"></i></span><span>Recipes</span></a>
        <a href="/orgs" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-building"></i></span><span>Organizations</span></a>
        <a href="/tags" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-tags"></i></span><span>Tags</span></a>
        <a href="/docs" class="button is-dark is-rounded">
          <span class="icon"><i class="fas fa-code"></i></span><span>API</span></a>
      </div>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
document.addEventListener('DOMContentLoaded', () => {{
  // One cheap fetch for the single live number on the page.
  fetch('/healthz').then(r => r.json()).then(d => {{
    const el = document.getElementById('stat-packages');
    if (el && typeof d.packages_count === 'number') {{
      el.textContent = d.packages_count.toLocaleString();
    }}
  }}).catch(() => {{
    const el = document.getElementById('stat-packages');
    if (el) el.textContent = '—';
  }});

  const copyBtn = document.getElementById('copy-install');
  if (copyBtn) copyBtn.addEventListener('click', () => {{
    const txt = document.getElementById('install-cmd-text').textContent;
    navigator.clipboard.writeText(txt).then(() => {{
      copyBtn.innerHTML = '<i class="fas fa-check"></i>';
      setTimeout(() => {{ copyBtn.innerHTML = '<i class="fas fa-copy"></i>'; }}, 1500);
    }}).catch(() => {{}});
  }});
}});
</script>
</body>
</html>"""


def search_html() -> str:
    """The package search page: the registry's search UI, on its own route.

    Opens in an empty rest state and issues **no** ``/v1/search`` until the
    visitor enters a query or picks a filter (``init()`` in the search JS,
    which also honours a ``?q=`` deep link from the landing page).
    """
    page_title = _html.escape(f"Search — {_SITE_TITLE}", quote=False)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(page_title)}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section">
  <div class="container">
    <h1 class="title is-3 has-text-white">
      <span class="icon is-large mr-2 neon-cyan"><i class="fas fa-search"></i></span>
      Search packages
    </h1>
    <p class="subtitle is-6 has-text-grey-lighter mb-4">
      Prebuilt binaries across every platform. Start typing, or filter by
      platform, architecture, or tag.
    </p>

    <div class="columns is-multiline is-vcentered">
      <div class="column is-4">
        <div class="field">
          <div class="control has-icons-left">
            <input class="input is-dark" type="text" id="search"
                   placeholder="Search packages by name, platform, tags..." />
            <span class="icon is-left"><i class="fas fa-search"></i></span>
          </div>
        </div>
      </div>
      <div class="column is-2">
        <div class="field"><div class="control has-icons-left">
          <div class="select is-dark is-fullwidth">
            <select id="tag-filter"><option value="">All tags</option></select>
          </div>
          <span class="icon is-left"><i class="fas fa-tag"></i></span>
        </div></div>
      </div>
      <div class="column is-2">
        <div class="field"><div class="control">
          <div class="select is-dark is-fullwidth">
            <select id="platform-filter"><option value="">All platforms</option></select>
          </div>
        </div></div>
      </div>
      <div class="column is-2">
        <div class="field"><div class="control">
          <div class="select is-dark is-fullwidth">
            <select id="arch-filter"><option value="">All architectures</option></select>
          </div>
        </div></div>
      </div>
      <div class="column is-2">
        <div class="field"><div class="control">
          <div class="select is-dark is-fullwidth">
            <select id="release-filter">
              <option value="">All channels</option>
              <option value="live" data-static="true">Live only</option>
            </select>
          </div>
        </div></div>
      </div>
      <div class="column is-2 has-text-right">
        <span class="is-size-7 has-text-grey-light" id="pkg-count">&nbsp;</span>
      </div>
    </div>

    <div class="table-container">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th class="is-sortable" data-key="name">Package <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="version">Version <span class="sort-arrow"></span></th>
            <th>Platforms</th>
            <th>Architectures</th>
            <th class="is-sortable" data-key="builds">Builds <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="totalSize">Size <span class="sort-arrow"></span></th>
          </tr>
        </thead>
        <tbody id="pkg-body"></tbody>
      </table>
    </div>

    <div class="has-text-centered mt-4">
      <button id="load-more" class="button is-dark is-outlined" style="display:none;">
        Load more
      </button>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_LANDING_JS}

document.addEventListener('DOMContentLoaded', () => {{
  {_NAVBAR_JS}

  document.getElementById('search').addEventListener('input', e => {{
    state.q = e.target.value;
    debouncedSearch();
  }});
  document.getElementById('platform-filter').addEventListener('change', e => {{
    state.platform = e.target.value;
    maybeSearch();
  }});
  document.getElementById('arch-filter').addEventListener('change', e => {{
    state.arch = e.target.value;
    maybeSearch();
  }});
  document.getElementById('tag-filter').addEventListener('change', e => {{
    state.tag = e.target.value;
    maybeSearch();
  }});
  document.getElementById('release-filter').addEventListener('change', e => {{
    state.release = e.target.value;
    maybeSearch();
  }});
  document.querySelectorAll('th.is-sortable').forEach(th => {{
    th.addEventListener('click', () => sortBy(th.dataset.key));
  }});
  const moreBtn = document.getElementById('load-more');
  if (moreBtn) moreBtn.addEventListener('click', loadMore);

  init();
}});
</script>
</body>
</html>"""


# ── Package detail page ──────────────────────────────────────────

_DETAIL_JS = r"""
let pkgName = '';
let pkgOrg = '';
let allBuilds = [];
let currentSort = { key: 'platform', dir: 'asc' };
// Variants (platform/arch/config/link) whose older revisions are shown.
const expandedVariants = new Set();

async function init(name, org) {
  pkgName = name;
  pkgOrg = org || '';
  // The recipe needs nothing else on the page, so it loads at once and on its
  // own.  It used to load only after /v1/deps answered -- the whole catalog's
  // dependency graph -- and when that request was slow or failed, the recipe
  // section silently never appeared.
  loadRecipeArtifacts();
  try {
    const orgParam = pkgOrg ? '?org=' + encodeURIComponent(pkgOrg) : '';
    const resp = await fetch('/v1/packages/' + encodeURIComponent(name) + orgParam);
    const data = await resp.json();
    allBuilds = data.packages || [];
    renderInfo();
    renderBuilds();
  } catch (err) {
    document.getElementById('builds-body').innerHTML =
      '<tr><td colspan="6" class="has-text-centered has-text-grey-light">Failed to load package data.</td></tr>';
  }
  // Fetch dependency graph and recipe metadata
  try {
    const dresp = await fetch('/v1/deps');
    const ddata = await dresp.json();
    renderDeps(ddata.forward || {}, ddata.reverse || {}, ddata.meta || {},
               ddata.recipe_names || []);
  } catch (_) {}
}

function renderDeps(forward, reverse, meta, recipeNames) {
  const isMainline = recipeNames.includes(pkgName);

  // Show mainline/community badge
  const badgeEl = document.getElementById('pkg-source-badge');
  if (badgeEl) {
    if (isMainline) {
      badgeEl.innerHTML = '<span class="badge-mainline" title="Official cvcpkg recipe — maintained as part of the cvcpkg distribution"><i class="fas fa-check-circle"></i> cvcpkg</span>';
    } else {
      badgeEl.innerHTML = '<span class="badge-community" title="Community-uploaded package — not part of the mainline cvcpkg recipe set"><i class="fas fa-users"></i> community</span>';
    }
    badgeEl.style.display = 'inline';
  }

  // Fill in description/license/maintainer from recipe if not in package data
  const m = meta[pkgName];
  if (m) {
    const descEl = document.getElementById('pkg-description');
    if (descEl && !descEl.textContent && m.description) {
      descEl.textContent = m.description;
      descEl.style.display = '';
    }
    const licEl = document.getElementById('pkg-license');
    if (licEl && !licEl.textContent && m.license) {
      licEl.textContent = m.license;
      licEl.style.display = '';
    }
    if (m.maintainer_email) {
      const maintEl = document.getElementById('pkg-maintainer');
      if (maintEl && maintEl.textContent) {
        const link = document.createElement('a');
        link.href = 'mailto:' + m.maintainer_email;
        link.className = 'has-text-link';
        link.textContent = ' <' + m.maintainer_email + '>';
        maintEl.appendChild(link);
      }
    }
  }
  // A Python column's Quick Start links the same package's other
  // interpreter columns that exist: attrs-cp311, attrs-cp313t, ... for
  // attrs-cp313, and only other -cuda columns for torch-cp311-cuda.
  const colEl = document.getElementById('qs-columns');
  if (colEl && colEl.dataset.base) {
    const base = colEl.dataset.base;
    const variant = colEl.dataset.variant || '';
    const others = recipeNames.filter(n =>
      n !== pkgName && n.startsWith(base) && n.endsWith(variant) &&
      /^-cp3\d+t?$/.test(n.slice(base.length, n.length - variant.length))
    ).sort();
    if (others.length > 0) {
      colEl.innerHTML = ' Other interpreters: ' + others.map(n =>
        '<a class="has-text-link" href="/package/' + encodeURIComponent(n) + '">' + esc(n) + '</a>'
      ).join(', ') + '.';
    }
  }

  const deps = forward[pkgName] || [];
  const depEl = document.getElementById('pkg-deps');
  if (deps.length > 0) {
    depEl.innerHTML = deps.sort().map(d =>
      '<a class="tag is-link is-light is-rounded mr-1 mb-1" href="/package/' +
      encodeURIComponent(d) + '">' + esc(d) + '</a>'
    ).join('');
    depEl.parentElement.style.display = '';
  }
  const rdeps = reverse[pkgName] || [];
  const rdepEl = document.getElementById('pkg-rdeps');
  if (rdeps.length > 0) {
    rdepEl.innerHTML = rdeps.sort().map(d =>
      '<a class="tag is-success is-light is-rounded mr-1 mb-1" href="/package/' +
      encodeURIComponent(d) + '">' + esc(d) + '</a>'
    ).join('');
    rdepEl.parentElement.style.display = '';
  }

  // Populate manual install section with dependency-aware download script
  const manualEl = document.getElementById('manual-install');
  const scriptEl = document.getElementById('manual-script');
  if (manualEl && scriptEl && allBuilds.length > 0) {
    // Collect all transitive deps
    function transitiveDeps(name, seen) {
      if (seen.has(name)) return;
      seen.add(name);
      (forward[name] || []).forEach(d => transitiveDeps(d, seen));
    }
    const allDeps = new Set();
    transitiveDeps(pkgName, allDeps);
    allDeps.delete(pkgName);

    // Pick the build for the copy-paste command.  allBuilds arrives
    // newest-version-first from the server (/v1/packages/{name} orders by
    // version_sort_key), so we do NOT re-rank versions here -- a second
    // ordering impl in JS is exactly how the server/CLI orderings diverged.
    // We only steer PLATFORM: a Linux visitor must not be handed a Windows
    // archive.  The first build matching the visitor's OS is therefore already
    // its newest version; fall back to the newest overall if none matches.
    const wantPlat = detectVisitorPlatform();
    const firstBuild =
      allBuilds.find(b => b.platform === wantPlat && b.arch === 'x86_64') ||
      allBuilds.find(b => b.platform === wantPlat) ||
      allBuilds[0];

    const depNames = [...allDeps].sort();
    let lines = ['mkdir -p /opt/cvcpkg', ''];
    if (depNames.length > 0) {
      lines.push('# Download dependencies first:');
      depNames.forEach(d => {
        lines.push('# ' + d + ': https://cvcpkg.org/package/' + encodeURIComponent(d));
      });
      lines.push('');
    }
    lines.push('# Download and extract ' + pkgName +
               ' (' + firstBuild.version + ', ' +
               firstBuild.platform + '/' + firstBuild.arch + '):');
    const url = 'https://cvcpkg.org' + firstBuild.archive_url;
    const fname = firstBuild.archive_url.split('/').pop();
    lines.push('curl -LO ' + url);
    lines.push('tar --zstd -xf ' + fname + ' -C /opt/cvcpkg');
    // What to do with the extracted prefix depends on the kind of package
    // (CMake for a library, the interpreter for a Python package, PATH for
    // a tool); the server renders it into data-tail.
    const tail = scriptEl.dataset.tail || '';
    if (tail) {
      lines.push('');
      tail.split('\n').forEach(l => lines.push(l));
    }

    scriptEl.textContent = lines.join('\n');
    manualEl.style.display = '';
  }

  // Render notes from recipe
  if (m && m.notes && m.notes.length > 0) {
    const notesEl = document.getElementById('pkg-notes-list');
    const notesSection = document.getElementById('pkg-notes-section');
    if (notesEl && notesSection) {
      notesEl.innerHTML = m.notes.map(n => `
        <div class="note-card has-background-black-ter">
          <p class="has-text-weight-semibold has-text-white">
            <span class="icon is-small mr-1"><i class="fas fa-sticky-note"></i></span>
            ${esc(n.title || 'Note')}
            ${(n.platforms || []).map(p => '<span class="tag is-small is-dark is-rounded ml-1">' + esc(p) + '</span>').join('')}
          </p>
          <p class="has-text-grey-lighter is-size-7 mt-1">${esc(n.description || '')}</p>
        </div>
      `).join('');
      notesSection.style.display = '';
    }
  }

  // Render toolchain requirements
  if (m && m.toolchain && Object.keys(m.toolchain).length > 0) {
    const tcEl = document.getElementById('pkg-toolchain');
    if (tcEl) {
      let items = [];
      for (const [key, val] of Object.entries(m.toolchain)) {
        if (key === 'note') {
          items.push('<span class="is-size-7 has-text-grey-lighter">' + esc(String(val)) + '</span>');
        } else if (typeof val === 'object' && val !== null) {
          const desc = val.description || key;
          const ver = val.min_version ? ' >= ' + val.min_version : '';
          const rec = val.recommended ? ' (recommended: ' + val.recommended + ')' : '';
          items.push('<span class="tag is-dark is-rounded mr-1 mb-1" title="' + esc(desc) + '">' +
            esc(key) + esc(ver) + esc(rec) + '</span>');
        }
      }
      if (items.length > 0) {
        tcEl.innerHTML = items.join(' ');
        tcEl.parentElement.style.display = '';
      }
    }
  }

}

function copyRecipeHowto(btn) {
  const text = document.getElementById('recipe-howto-cmds').textContent;
  const done = () => {
    const label = btn.querySelector('span:last-child');
    const was = label.textContent;
    label.textContent = 'Copied';
    setTimeout(() => { label.textContent = was; }, 1500);
  };
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => {});
}

// The recipe section loads on its own, and is NOT gated on isMainline.
// isMainline means "this name is in the recipe list bundled with the running
// server", which is false for an org package and for any recipe pushed after
// the last release -- exactly the pages whose recipe was invisible before.
// The endpoint decides what the caller may see; the page just asks.
// Query string carrying the org scope (and any extra params) to the
// recipe endpoints.  Public packages send nothing, so anonymous visitors
// keep working unchanged.
function recipeQuery(extra) {
  const p = new URLSearchParams(extra || {});
  if (pkgOrg) p.set('org', pkgOrg);
  const s = p.toString();
  return s ? '?' + s : '';
}

async function loadRecipeArtifacts() {
  const section = document.getElementById('recipe-section');
  const list = document.getElementById('recipe-artifacts');
  const yaml = document.getElementById('recipe-yaml');
  if (!section || !list) return;

  // Reveal the section and explain the absence, for a package published
  // straight from prebuilt artifacts: the listing 404s or comes back empty
  // and there is no bundle behind the archive buttons.
  const showNoRecipe = () => {
    section.style.display = '';
    if (yaml) {
      yaml.textContent =
        'No recipe on file for this package — it was published from ' +
        'prebuilt artifacts. Run cvcpkg recipe push ' + pkgName +
        ' to attach one.';
    }
    ['recipe-archive-link', 'recipe-zip-link', 'recipe-howto'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
  };

  let files = [];
  try {
    const resp = await fetch(
      '/v1/recipe/' + encodeURIComponent(pkgName) + '/files' + recipeQuery());
    if (!resp.ok) { showNoRecipe(); return; }
    files = (await resp.json()).files || [];
  } catch (_) { return; }
  if (files.length === 0) { showNoRecipe(); return; }

  // recipe.yaml first, then the recipe's own files, then shared helpers.
  const rank = f => (f.kind === 'recipe' ? 0 : (f.shared ? 2 : 1));
  files.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));

  // recipe.yaml renders open above the rest instead of behind a click.
  const recipeFile = files.find(f => f.kind === 'recipe');
  const rest = files.filter(f => f.kind !== 'recipe');

  if (yaml) {
    yaml.replaceChildren();
    if (recipeFile) {
      try {
        const resp = await fetch(
          '/v1/recipe/' + encodeURIComponent(pkgName) + recipeQuery());
        const text = resp.ok ? await resp.text() : 'Could not load recipe.yaml.';
        // textContent, not innerHTML: recipe content is untrusted.
        const pre = document.createElement('pre');
        pre.className = 'has-background-dark has-text-light p-3';
        pre.style.borderRadius = '6px';
        pre.style.overflowX = 'auto';
        const code = document.createElement('code');
        code.textContent = text;
        pre.appendChild(code);
        yaml.appendChild(pre);
      } catch (_) {
        yaml.textContent = 'Could not load recipe.yaml.';
      }
    }
  }

  const iconFor = k =>
    k === 'recipe' ? 'fa-file-code' :
    k === 'image'  ? 'fa-image' :
    k === 'patch'  ? 'fa-file-medical' :
    k === 'script' ? 'fa-terminal' : 'fa-file-alt';

  list.innerHTML = rest.map((f, i) =>
    '<div class="recipe-artifact">' +
      '<a class="recipe-artifact-head" onclick="toggleArtifact(' + i + ')">' +
        '<span class="icon is-small"><i id="ra-icon-' + i + '" class="fas fa-chevron-right"></i></span>' +
        '<span class="icon is-small"><i class="fas ' + iconFor(f.kind) + '"></i></span>' +
        '<span class="ml-2 has-text-light">' + esc(f.name) + '</span>' +
        (f.shared
          ? '<span class="tag is-dark is-rounded is-small ml-2" title="Shared helper sourced by many recipes">shared</span>'
          : '') +
        '<span class="has-text-grey ml-2 is-size-7">' + fmtSize(f.size) + '</span>' +
      '</a>' +
      '<div id="ra-body-' + i + '" class="recipe-artifact-body" style="display:none"' +
        ' data-path="' + esc(f.path) + '" data-kind="' + esc(f.kind) + '"' +
        ' data-large="' + (f.too_large ? '1' : '0') + '" data-loaded="0"></div>' +
    '</div>'
  ).join('');
  section.style.display = '';
}

// Everything starts folded; a file's bytes are fetched on first expand so a
// recipe with a dozen scripts costs one request until the user asks.
async function toggleArtifact(i) {
  const body = document.getElementById('ra-body-' + i);
  const icon = document.getElementById('ra-icon-' + i);
  if (!body) return;

  if (body.style.display !== 'none') {
    body.style.display = 'none';
    if (icon) icon.className = 'fas fa-chevron-right';
    return;
  }
  body.style.display = '';
  if (icon) icon.className = 'fas fa-chevron-down';
  if (body.dataset.loaded === '1') return;
  body.dataset.loaded = '1';

  const path = body.dataset.path;
  const url = '/v1/recipe/' + encodeURIComponent(pkgName) + '/file' +
              recipeQuery({ path: path });

  if (body.dataset.large === '1') {
    body.innerHTML = '<p class="has-text-grey p-3 is-size-7">Too large to show inline &mdash; ' +
      '<a class="has-text-link" href="' + url + '">open it</a> or download the recipe archive.</p>';
    return;
  }
  if (body.dataset.kind === 'image') {
    body.innerHTML = '<div class="p-3"><img src="' + url + '" alt="' + esc(path) +
      '" style="max-width:100%;border-radius:6px"></div>';
    return;
  }
  if (body.dataset.kind === 'binary') {
    body.innerHTML = '<p class="has-text-grey p-3 is-size-7">Binary file &mdash; ' +
      '<a class="has-text-link" href="' + url + '">download it</a>.</p>';
    return;
  }

  try {
    const resp = await fetch(url);
    const text = resp.ok ? await resp.text() : 'Could not load this file.';
    // textContent, not innerHTML: recipe scripts are untrusted content.
    const pre = document.createElement('pre');
    pre.className = 'has-background-dark has-text-light p-3';
    pre.style.borderRadius = '6px';
    pre.style.overflowX = 'auto';
    const code = document.createElement('code');
    code.textContent = text;
    pre.appendChild(code);
    body.replaceChildren(pre);
  } catch (_) {
    body.innerHTML = '<p class="has-text-grey p-3 is-size-7">Could not load this file.</p>';
  }
}

function toggleRecipe() {
  const body = document.getElementById('recipe-body');
  const icon = document.getElementById('recipe-toggle-icon');
  if (body.style.display === 'none') {
    body.style.display = '';
    icon.className = 'fas fa-chevron-up';
  } else {
    body.style.display = 'none';
    icon.className = 'fas fa-chevron-down';
  }
}

function renderInfo() {
  if (allBuilds.length === 0) return;
  const p = allBuilds[0];
  document.getElementById('pkg-title').textContent = p.name;
  document.getElementById('pkg-version').textContent = p.version;

  // Warn when any build of this package shadows a different upstream package.
  if (allBuilds.some(b => b.diverges_upstream)) {
    const dv = document.getElementById('pkg-diverge-badge');
    if (dv) dv.style.display = '';
  }

  if (p.description) {
    document.getElementById('pkg-description').textContent = p.description;
    document.getElementById('pkg-description').style.display = '';
  }
  if (p.homepage) {
    const el = document.getElementById('pkg-homepage');
    if (new RegExp('^https?://', 'i').test(p.homepage || '')) el.href = p.homepage;
    el.textContent = p.homepage;
    el.parentElement.style.display = '';
  }
  if (p.license) {
    const el = document.getElementById('pkg-license');
    el.textContent = p.license;
    el.style.display = '';
  }
  if (p.published_by) {
    const pubEl = document.getElementById('pkg-publisher');
    const span = document.createElement('span');
    span.className = 'has-text-white';
    span.textContent = p.published_by;
    pubEl.appendChild(span);
    if (p.published_by_email) {
      const emailLink = document.createElement('a');
      emailLink.href = 'mailto:' + p.published_by_email;
      emailLink.className = 'has-text-link ml-1';
      emailLink.textContent = '<' + p.published_by_email + '>';
      pubEl.appendChild(document.createTextNode(' '));
      pubEl.appendChild(emailLink);
    }
    pubEl.parentElement.style.display = '';
  }
  if (p.maintainer) {
    document.getElementById('pkg-maintainer').textContent = p.maintainer;
    document.getElementById('pkg-maintainer').parentElement.style.display = '';
  }
  if (p.tags) {
    const container = document.getElementById('pkg-tags');
    p.tags.split(',').map(t => t.trim()).filter(Boolean).forEach(tag => {
      const span = document.createElement('span');
      span.className = 'tag is-info is-light is-rounded mr-1';
      span.textContent = tag;
      container.appendChild(span);
    });
    container.parentElement.style.display = '';
  }

  const platforms = new Set(allBuilds.map(b => b.platform).filter(Boolean));
  const totalSize = allBuilds.reduce((s, b) => s + (b.size_bytes || 0), 0);
  document.getElementById('pkg-stat-builds').textContent = allBuilds.length;
  document.getElementById('pkg-stat-platforms').textContent = platforms.size;
  document.getElementById('pkg-stat-size').textContent = fmtSizeLarge(totalSize);
}

function variantKey(b) {
  return [b.org, b.platform, b.arch, b.build_type, b.link].map(v => v || '').join('/');
}

// One group per variant.  /v1/packages/{name} lists versions newest first
// (semver.version_sort_key), so the first build of each variant is its latest
// revision and the rest are older ones.  Yanked builds are not in the list,
// and nuked ones no longer exist.
function groupByVariant(builds) {
  const groups = new Map();
  for (const b of builds) {
    const key = variantKey(b);
    if (groups.has(key)) groups.get(key).older.push(b);
    else groups.set(key, { key, latest: b, older: [] });
  }
  return [...groups.values()];
}

function compareBuilds(a, b) {
  let va = a[currentSort.key] || '';
  let vb = b[currentSort.key] || '';
  if (currentSort.key === 'size_bytes') {
    return currentSort.dir === 'asc' ? (va||0) - (vb||0) : (vb||0) - (va||0);
  }
  if (currentSort.key === 'published_at') {
    return currentSort.dir === 'asc'
      ? new Date(va) - new Date(vb) : new Date(vb) - new Date(va);
  }
  va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
  if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
  if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
  return 0;
}

function buildRow(b, group) {
  const older = !group;
  const expanded = group && expandedVariants.has(group.key);
  const n = group ? group.older.length : 0;
  const toggle = n ? `
        <button class="button is-small is-dark ml-1 revisions-toggle" type="button"
                data-variant="${esc(group.key)}" aria-expanded="${expanded}"
                title="${expanded ? 'Hide' : 'Show'} ${n} older revision${n === 1 ? '' : 's'}"
                onclick="toggleRevisions(this.dataset.variant)">
          <span class="icon is-small"><i class="fas fa-chevron-${expanded ? 'up' : 'down'}"></i></span>
          <span>${n}</span>
        </button>` : '';
  return `
    <tr${older ? ' class="older-revision"' : ''}>
      <td>${older ? '' : platformTag(b.platform)}</td>
      <td><span class="is-size-7">${older ? '' : esc(b.arch)}</span></td>
      <td>${older ? '<span class="has-text-grey mr-1">&#8627;</span>' : ''}<code class="is-size-7">${esc(b.version || '')}</code>${b.diverges_upstream ? ' <span class="icon is-small has-text-warning" title="Diverges from upstream: this local build shadows a different package published upstream at these coordinates. An admin can nuke this bundle to let the upstream copy re-populate."><i class="fas fa-exclamation-triangle"></i></span>' : ''}</td>
      <td><span class="is-size-7">${older ? '' : esc(b.build_type)}</span></td>
      <td><span class="is-size-7">${older ? '' : esc(b.link)}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(b.size_bytes)}</span></td>
      <td>${releaseTag(b.release_tag)}</td>
      <td><span class="is-size-7 has-text-grey-light">${fmtDate(b.published_at)}</span></td>
      <td class="is-nowrap">
        <a class="button is-small is-link is-outlined" href="${b.archive_url && b.archive_url.startsWith('/') ? esc(b.archive_url) : '#'}" title="Download ${esc(b.version || '')}">
          <span class="icon is-small"><i class="fas fa-download"></i></span>
        </a>${toggle}
      </td>
    </tr>`;
}

function toggleRevisions(key) {
  if (expandedVariants.has(key)) expandedVariants.delete(key);
  else expandedVariants.add(key);
  renderBuilds();
}

function renderBuilds() {
  if (allBuilds.length === 0) {
    document.getElementById('builds-body').innerHTML =
      '<tr><td colspan="9" class="has-text-centered has-text-grey-light py-4">No builds available.</td></tr>';
    return;
  }
  // Only each variant's latest revision is listed; the chevron next to its
  // download button lists the older ones under it.  Sorting orders the
  // variants by their latest build.
  const groups = groupByVariant(allBuilds);
  groups.sort((a, b) => compareBuilds(a.latest, b.latest));

  const tbody = document.getElementById('builds-body');
  tbody.innerHTML = groups.map(g =>
    buildRow(g.latest, g) +
    (expandedVariants.has(g.key) ? g.older.map(b => buildRow(b, null)).join('') : '')
  ).join('');

  document.querySelectorAll('#builds-table th.is-sortable').forEach(th => {
    const arrow = th.querySelector('.sort-arrow');
    if (th.dataset.key === currentSort.key) {
      th.classList.add('is-sorted');
      arrow.textContent = currentSort.dir === 'asc' ? ' \u25B2' : ' \u25BC';
    } else {
      th.classList.remove('is-sorted');
      arrow.textContent = '';
    }
  });
}

function sortBy(key) {
  if (currentSort.key === key) {
    currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    currentSort = { key, dir: 'asc' };
  }
  renderBuilds();
}

async function loadDownloadStats(name) {
  try {
    const resp = await fetch('/v1/downloads/stats?name=' + encodeURIComponent(name));
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.daily || data.daily.length === 0) return;

    const section = document.getElementById('download-stats-section');
    const totalEl = document.getElementById('download-total');
    if (!section) return;

    totalEl.textContent = data.total.toLocaleString() + ' total';
    section.style.display = '';

    const canvas = document.getElementById('download-chart');
    const cfg = data.config || {};
    const chartHeight = cfg.height || 200;
    const lineColor = cfg.color || '#3273dc';
    const fillColor = cfg.fill_color || 'rgba(50,115,220,0.15)';

    // Draw the chart on canvas
    const daily = data.daily;
    const maxCount = Math.max(1, ...daily.map(d => d.count));
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.parentElement.clientWidth;
    canvas.width = width * dpr;
    canvas.height = chartHeight * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = chartHeight + 'px';

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const pad = { top: 20, right: 20, bottom: 30, left: 50 };
    const cw = width - pad.left - pad.right;
    const ch = chartHeight - pad.top - pad.bottom;

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 1;
    const gridLines = 4;
    for (let i = 0; i <= gridLines; i++) {
      const y = pad.top + (ch / gridLines) * i;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + cw, y);
      ctx.stroke();
      // Y-axis labels
      const val = Math.round(maxCount * (1 - i / gridLines));
      ctx.fillStyle = 'rgba(255,255,255,0.4)';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(val.toString(), pad.left - 8, y + 4);
    }

    // X-axis labels (show ~6 date labels)
    const labelInterval = Math.max(1, Math.floor(daily.length / 6));
    ctx.fillStyle = 'rgba(255,255,255,0.4)';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    daily.forEach((d, i) => {
      if (i % labelInterval === 0 || i === daily.length - 1) {
        const x = pad.left + (i / (daily.length - 1 || 1)) * cw;
        const label = d.date.slice(5);  // MM-DD
        ctx.fillText(label, x, chartHeight - 6);
      }
    });

    // Build path
    const points = daily.map((d, i) => ({
      x: pad.left + (i / (daily.length - 1 || 1)) * cw,
      y: pad.top + ch - (d.count / maxCount) * ch,
    }));

    // Fill area
    ctx.beginPath();
    ctx.moveTo(points[0].x, pad.top + ch);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(points[points.length - 1].x, pad.top + ch);
    ctx.closePath();
    ctx.fillStyle = fillColor;
    ctx.fill();

    // Draw line
    ctx.beginPath();
    points.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // Dots on data points (only if few enough)
    if (daily.length <= 31) {
      points.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
        ctx.fillStyle = lineColor;
        ctx.fill();
      });
    }

    // Hover tooltip
    canvas.addEventListener('mousemove', function(e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const idx = Math.round(((mx - pad.left) / cw) * (daily.length - 1));
      if (idx >= 0 && idx < daily.length) {
        canvas.title = daily[idx].date + ': ' + daily[idx].count + ' downloads';
      }
    });
  } catch (_) {}
}

function fmtDuration(start, end) {
  if (!start || !end) return '\u2014';
  const s = (new Date(end) - new Date(start)) / 1000;
  if (s < 60) return s.toFixed(1) + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.round((s%3600)/60) + 'm';
}
function statusCls(status) {
  return {
    succeeded: 'is-success', failed: 'is-danger', running: 'is-info',
    pending: 'is-light', dispatched: 'is-warning', cancelled: 'is-dark',
    timed_out: 'is-danger'
  }[status] || 'is-light';
}
async function loadBuildJobs(name) {
  const section = document.getElementById('build-jobs-section');
  if (!section) return;
  try {
    // Public read: a token only adds the viewer's private orgs' jobs.
    const tok = localStorage.getItem('cvcpkg_token') || '';
    const resp = await fetch('/v1/builds?recipe_name=' + encodeURIComponent(name) + '&limit=20',
      { headers: tok ? { 'Authorization': 'Bearer ' + tok } : {} });
    if (resp.status === 401 || resp.status === 403) return;
    if (!resp.ok) return;
    const data = await resp.json();
    const jobs = data.jobs || [];
    if (jobs.length === 0) return;
    section.style.display = '';
    const tbody = document.getElementById('build-jobs-body');
    tbody.innerHTML = jobs.map(j => {
      const logLink = (j.status === 'running')
        ? '<a class="button is-small is-info is-outlined" href="/build/' + j.id + '" title="Live Log">' +
          '<span class="icon is-small"><i class="fas fa-stream"></i></span></a>'
        : (j.status === 'succeeded' || j.status === 'failed')
        ? '<a class="button is-small is-outlined" href="/build/' + j.id + '" title="View Log">' +
          '<span class="icon is-small"><i class="fas fa-file-alt"></i></span></a>'
        : '';
      const dagLink = j.dag_id
        ? '<a href="/builds?dag_id=' + encodeURIComponent(j.dag_id) + '" class="tag is-dark is-rounded is-small" title="DAG: ' + esc(j.dag_id) + '">' +
          '<span class="icon is-small"><i class="fas fa-project-diagram"></i></span>&nbsp;' + esc(j.dag_id) + '</a>'
        : '';
      const errTip = (j.status === 'failed' && j.error_message)
        ? ' title="' + esc(j.error_message.substring(0, 200)) + '"'
        : '';
      const builderName = j.builder_id ? 'b' + j.builder_id : '\u2014';
      return '<tr>' +
        '<td><a href="/build/' + j.id + '"><span class="tag ' + statusCls(j.status) + ' is-rounded"' + errTip + '>' + esc(j.status) + '</span></a></td>' +
        '<td><span class="is-size-7">' + platformTag(j.platform) + '</span></td>' +
        '<td><span class="is-size-7">' + esc(j.arch) + '</span></td>' +
        '<td><span class="is-size-7">' + esc(j.config) + '</span></td>' +
        '<td><span class="is-size-7">' + esc(j.link) + '</span></td>' +
        '<td><span class="is-size-7 has-text-grey-light">' + esc(builderName) + '</span></td>' +
        '<td><span class="is-size-7 has-text-grey-light">' + fmtDuration(j.started_at, j.finished_at) + '</span></td>' +
        '<td><span class="is-size-7">' + dagLink + '</span></td>' +
        '<td><span class="is-size-7 has-text-grey-light">' + fmtDate(j.submitted_at) + '</span></td>' +
        '<td>' + logLink + '</td>' +
        '</tr>';
    }).join('');
  } catch (_) {}
}
"""


def _js_string_literal(s: str) -> str:
    """Encode a Python string as a JS string literal that is safe inside HTML.

    json.dumps leaves <, >, & (hence </script>) intact, which
    would let a reflected value break out of the surrounding <script> block.
    Each is emitted as a JS unicode escape.
    """
    return _json.dumps(s).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@dataclass(frozen=True)
class _QsStep:
    """One Quick Start step: an intro (HTML), a copy-paste block (plain text,
    escaped on output) and small notes under it (HTML)."""

    intro: str
    code: str = ""
    notes: tuple[str, ...] = ()


def _qs_steps(steps: list[_QsStep]) -> str:
    out: list[str] = []
    for i, step in enumerate(steps):
        spacing = "" if i == 0 else " mt-4"
        out.append(f'<p class="has-text-grey-lighter{spacing}">{step.intro}</p>')
        if step.code:
            out.append(
                '<pre class="has-background-dark has-text-success p-3" '
                f'style="border-radius:6px;">{_html.escape(step.code, quote=False)}</pre>'
            )
        out.extend(f'<p class="has-text-grey is-size-7 mt-2">{n}</p>' for n in step.notes)
    return "\n        ".join(out)


_QS_VENV_INTRO = (
    "Work in a virtualenv on top of it. Whatever you <code>pip install</code> goes into "
    "<code>.venv</code>, and the prefix's packages stay importable:"
)


def _qs_windows_note(exe: str, *invocations: str) -> str:
    """Where the interpreter is on Windows, with *invocations* spelled out.

    A Windows CPython keeps ``python.exe`` at the prefix root, and the
    activation scripts put only ``bin/`` and ``Scripts/`` on PATH.
    """
    runs = " or ".join(
        f"<code>.\\deps\\{_html.escape(exe)} {_html.escape(i)}</code>" for i in invocations
    )
    return (
        "On Windows the interpreter sits at the prefix root, which activation does not put "
        f"on PATH, so call it directly: {runs}."
    )


def _qs_codes(items: tuple[str, ...], limit: int = 8) -> str:
    """``<code>a</code>, <code>b</code> and 3 more``."""
    shown = ", ".join(f"<code>{_html.escape(c)}</code>" for c in items[:limit])
    more = len(items) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def _quickstart(qs: QuickStartProfile, *, name: str, org: str = "") -> tuple[str, str, str]:
    """``(quick_start_html, manual_tail_intro, manual_tail_cmd)`` for one package.

    *name* and *org* arrive unescaped; commands install ``[org/]name``.  The
    manual-install tail (what to do after extracting the archives by hand) is
    as kind-specific as the Quick Start; its intro is HTML, its command plain
    text.
    """
    e = _html.escape
    title = f"{org}/{name}" if org else name
    install = f"cvcpkg install {title} --prefix ./deps"
    build = _QsStep(
        "Or build it from source using the recipe:", f"cvcpkg build {title} --prefix ./deps"
    )
    activate = "source ./deps/bin/activate"

    if qs.kind == "python":
        cmd, exe, abi = qs.python_cmd, qs.windows_exe, qs.abi
        venv = "-m venv --system-site-packages .venv"
        # Without the recipe the import name is unknown, and a pip-metadata
        # check would fail for columns that ship no dist-info (vtk-python).
        check = f'-c "import {qs.module}"' if qs.module else ""
        use_notes = [_qs_windows_note(exe, *(i for i in (check, venv) if i))]
        if qs.commands:
            use_notes.append(
                f"It also installs {_qs_codes(qs.commands)}, on PATH once the prefix is activated."
            )
        # Sibling columns are linked from the page's recipe list, which knows
        # no org, so an org page would link packages that org may not have.
        columns = (
            ""
            if org
            else f'<span id="qs-columns" data-base="{e(qs.base)}" '
            f'data-variant="{e(qs.variant)}"></span>'
        )
        steps = [
            _QsStep(
                f"<strong>{e(qs.base)}</strong> for CPython {e(cmd[len('python'):])} ({e(abi)}). "
                f"It installs into the prefix's own <code>{e(cmd)}</code>; it does not import "
                f"into a system Python.{columns}"
            ),
            _QsStep(
                f"Install it. This also pulls in <code>{e(qs.interpreter)}</code> and the "
                f"{e(abi)} build of every dependency:",
                install,
            ),
            _QsStep(
                "Use it from the prefix's interpreter:",
                f"{activate}\n{cmd} {check}".rstrip(),
                tuple(use_notes),
            ),
            _QsStep(_QS_VENV_INTRO, f"{cmd} {venv}"),
            _QsStep(
                "Pin it in your project: list it in a requirements file, then run "
                "<code>cvcpkg install --from cvc-requirements.yaml --prefix ./deps</code>:",
                f"# cvc-requirements.yaml\ncomponents:\n  - {title}",
            ),
            build,
        ]
        return (
            _qs_steps(steps),
            "Then run the prefix's interpreter:",
            f"/opt/cvcpkg/bin/{cmd} {check or '--version'}",
        )

    if qs.kind == "interpreter":
        cmd, exe, abi = qs.python_cmd, qs.windows_exe, qs.abi
        steps = [
            _QsStep(
                f"CPython {e(cmd[len('python'):])}. Python packages for it are separate recipes, "
                f"one per interpreter, named <code>&lt;package&gt;-{e(abi)}</code>."
            ),
            _QsStep("Install it:", install),
            _QsStep(
                "Run it:",
                f"{activate}\n{cmd} --version",
                (_qs_windows_note(exe, "--version"),),
            ),
            _QsStep("Add packages for it:", f"cvcpkg install <package>-{abi} --prefix ./deps"),
            _QsStep(_QS_VENV_INTRO, f"{cmd} -m venv --system-site-packages .venv"),
        ]
        if not abi.endswith("t"):
            # CMake's FindPython3 module, not a shipped config package.  Its
            # Python3_ROOT_DIR hint is searched before the Windows registry and
            # macOS frameworks, which CMAKE_PREFIX_PATH is not.  (FindPython3
            # selects a free-threaded ABI only on request, so 3.13t gets no
            # line here.)
            steps.append(
                _QsStep(
                    "Embed it in a C/C++ build: CMake's FindPython3 module "
                    "(<code>find_package(Python3 COMPONENTS Development)</code>) finds it when "
                    "pointed at the prefix:",
                    'cmake -S . -B build -DPython3_ROOT_DIR="$PWD/deps"',
                )
            )
        steps.append(build)
        return _qs_steps(steps), "Then run the interpreter:", f"/opt/cvcpkg/bin/{cmd} --version"

    if qs.kind == "tool":
        notes = (f"Commands: {_qs_codes(qs.commands)}.",) if qs.commands else ()
        steps = [
            _QsStep("Install it:", install),
            _QsStep(
                "Put its commands on PATH by activating the prefix:",
                f"{activate}            # bash / zsh\n. .\\deps\\Scripts\\Activate.ps1   # PowerShell",
                notes,
            ),
            build,
        ]
        return (
            _qs_steps(steps),
            "Then put the prefix's commands on PATH:",
            "export PATH=/opt/cvcpkg/bin:$PATH",
        )

    if qs.kind == "source":
        upstream = name[: -len("-src")] if name.endswith("-src") else name
        pin = f'\n      version: "=={qs.upstream_version}"' if qs.upstream_version else ""
        steps = [
            _QsStep(
                f"A source package: the unmodified upstream source of <strong>{e(upstream)}</strong>, "
                f"staged at <code>src/{e(name)}/</code>. Nothing is compiled. Recipes build from "
                "it, so the upstream host is contacted once and every other build installs this "
                "package from the catalog."
            ),
        ]
        # source.package must name a depends.build entry exactly, and the
        # recipe schema allows no org/ there, so an org's source package has
        # no snippet that builds.
        if not org:
            steps.append(
                _QsStep(
                    "Build a recipe from it (in that recipe's <code>recipe.yaml</code>):",
                    f"source:\n  type: package\n  package: {name}\n"
                    f"depends:\n  build:\n    - name: {name}{pin}",
                )
            )
        steps.append(
            _QsStep(
                "Or install the source tree itself:" if not org else "Install the source tree:",
                f"{install}    # -> ./deps/src/{name}/",
            )
        )
        return _qs_steps(steps), "The source tree is then at:", f"ls /opt/cvcpkg/src/{name}/"

    if qs.kind == "files":
        notes = (f"It installs {_qs_codes(qs.paths)} under the prefix.",) if qs.paths else ()
        return _qs_steps([_QsStep("Install it:", install, notes), build]), "", ""

    # library: the C/C++ flow.
    steps = [
        _QsStep(
            "Install the pre-built binary:", f"cvcpkg install {title} --prefix /path/to/prefix"
        ),
        _QsStep(
            "Or build from source using the recipe:",
            f"cvcpkg build {title} --prefix /path/to/prefix",
        ),
        _QsStep(
            "Use in a downstream CMake project:", "cmake -DCMAKE_PREFIX_PATH=/path/to/prefix .."
        ),
    ]
    return (
        _qs_steps(steps),
        "Then point CMake at the prefix:",
        "cmake -DCMAKE_PREFIX_PATH=/opt/cvcpkg ..",
    )


def package_detail_html(name: str, *, org: str = "", recipe: dict | None = None) -> str:
    """Return the HTML for a package detail page.

    *recipe* is the package's parsed recipe.yaml, when the caller may show what
    it says; it decides the Quick Start (a Python column, a tool, a source
    package, ...).  Without it the name alone does.
    """
    safe_name = _html.escape(name, quote=True)
    safe_org = _html.escape(org, quote=True) if org else ""
    display_title = f"{safe_org}/{safe_name}" if safe_org else safe_name
    # Escaped: _head_html writes it into href/content attributes as is.
    canonical_path = f"/package/{safe_org}/{safe_name}" if org else f"/package/{safe_name}"
    try:
        qs = quickstart_profile(name, recipe)
    except Exception:
        # profile() reads untrusted pushed recipes defensively; if one still
        # trips it, the page renders from the name rather than failing.
        _log.warning("quick start: could not read the recipe of %s", name, exc_info=True)
        qs = quickstart_profile(name)
    qs_html, tail_intro, tail_cmd = _quickstart(qs, name=name, org=org)
    manual_tail_html = (
        f"""{tail_intro}
        </p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">{_html.escape(tail_cmd, quote=False)}</pre>"""
        if tail_cmd
        else "</p>"
    )
    # The JS-built manual script ends with the same step as a comment and the
    # command ("Then point CMake at the prefix:" -> "# Point CMake at the
    # prefix:").  Carried in a data- attribute, escaped for it.
    comment = tail_intro.removeprefix("Then ")
    manual_tail_attr = _html.escape(
        f"# {comment[:1].upper()}{comment[1:]}\n{tail_cmd}" if tail_cmd else "", quote=True
    )

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"{display_title} &mdash; cvcpkg", canonical=canonical_path)}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<!-- Breadcrumb -->
<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Packages</a></li>
        {f'<li><a href="/org/{safe_org}" class="has-text-grey-light">{safe_org}</a></li>' if safe_org else ""}
        <li class="is-active"><a href="#" class="has-text-light">{safe_name}</a></li>
      </ul>
    </nav>
  </div>
</section>

<!-- Package info -->
<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="columns">
      <!-- Left: metadata -->
      <div class="column is-8">
        <h1 class="title is-2 has-text-white">
          <span class="icon mr-2"><i class="fas fa-cube"></i></span>
          {f'<a href="/org/{safe_org}" class="has-text-link">{safe_org}</a><span class="has-text-grey mx-1">/</span>' if safe_org else ""}
          <span id="pkg-title">{safe_name}</span>
          <span class="tag is-link is-rounded is-medium ml-3" id="pkg-version">&hellip;</span>
          <span class="tag is-warning is-rounded is-medium ml-2" id="pkg-license" style="display:none"></span>
          <span id="pkg-source-badge" class="ml-2" style="display:none"></span>
          <span id="pkg-diverge-badge" class="tag is-warning is-medium ml-2" style="display:none" title="One or more builds of this package diverge from upstream: a local build shadows a different package published upstream at the same coordinates. An admin can nuke the divergent build(s) to let the upstream copy re-populate."><span class="icon is-small"><i class="fas fa-exclamation-triangle"></i></span><span>diverges from upstream</span></span>
        </h1>
        <p class="subtitle is-5 has-text-grey-lighter" id="pkg-description" style="display:none"></p>

        <div class="content">
          <div style="display:none"><strong class="has-text-grey-light">Homepage:</strong>
            <a id="pkg-homepage" href="#" class="has-text-link" target="_blank" rel="noopener noreferrer"></a>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Published by:</strong>
            <span id="pkg-publisher"></span>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Maintainer:</strong>
            <span id="pkg-maintainer"></span>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Tags:</strong>
            <span id="pkg-tags"></span>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Dependencies:</strong>
            <span id="pkg-deps"></span>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Used By:</strong>
            <span id="pkg-rdeps"></span>
          </div>
          <div style="display:none"><strong class="has-text-grey-light">Toolchain:</strong>
            <span id="pkg-toolchain"></span>
          </div>
        </div>
      </div>

      <!-- Right: quick stats -->
      <div class="column is-4">
        <div class="box has-background-black-ter">
          <div class="columns is-mobile">
            <div class="column has-text-centered">
              <p class="title is-4 has-text-info" id="pkg-stat-builds">&mdash;</p>
              <p class="heading has-text-grey-light">Builds</p>
            </div>
            <div class="column has-text-centered">
              <p class="title is-4 has-text-success" id="pkg-stat-platforms">&mdash;</p>
              <p class="heading has-text-grey-light">Platforms</p>
            </div>
            <div class="column has-text-centered">
              <p class="title is-4 has-text-warning" id="pkg-stat-size">&mdash;</p>
              <p class="heading has-text-grey-light">Total Size</p>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</section>

<!-- Recipe + its artifacts -->
<!-- Above Downloads on purpose: the recipe is the source of truth for what
     this package is, so it reads before the build/download table. -->
<section class="section pt-0 has-background-black-bis" id="recipe-section" style="display:none">
  <div class="container">
    <div class="box has-background-black-ter">
      <h3 class="title is-5 has-text-white collapsible-header" onclick="toggleRecipe()">
        <span class="icon mr-1"><i class="fas fa-file-code"></i></span> Recipe
        <span class="icon is-small ml-2"><i id="recipe-toggle-icon" class="fas fa-chevron-up"></i></span>
        <span class="is-pulled-right" onclick="event.stopPropagation()">
          <a class="button is-small is-link is-outlined" id="recipe-archive-link"
             href="/v1/recipe/{safe_name}/archive{'?org=' + safe_org if safe_org else ''}"
             title="Download recipe.yaml and every script it ships with, as a tar.gz">
            <span class="icon is-small"><i class="fas fa-file-archive"></i></span>
            <span>tar.gz</span>
          </a>
          <a class="button is-small is-link is-outlined ml-1" id="recipe-zip-link"
             href="/v1/recipe/{safe_name}/archive?format=zip{'&amp;org=' + safe_org if safe_org else ''}"
             title="Same archive as a zip">
            <span class="icon is-small"><i class="fas fa-file-archive"></i></span>
            <span>zip</span>
          </a>
        </span>
      </h3>
      <div id="recipe-body" class="recipe-viewer">
        <div id="recipe-howto" class="mb-3">
          <p class="has-text-grey-lighter is-size-7 mb-2">
            The archive holds <code>{safe_name}/</code> (recipe.yaml, build scripts, patches) and
            the shared <code>_common/</code> helpers they source. Extract it into a recipes
            directory and point cvcpkg at it:
          </p>
          <pre class="has-background-dark has-text-light p-3 is-size-7" id="recipe-howto-cmds"
               style="border-radius:6px;overflow-x:auto"><code>mkdir -p recipes
curl -fsSL {_SITE_URL}/v1/recipe/{safe_name}/archive{'?org=' + safe_org if safe_org else ''} | tar -xz -C recipes
cvcpkg install-deps recipes/{safe_name} --prefix ./prefix    # its dependencies, prebuilt
cvcpkg build {safe_name} --recipes-dir recipes --no-deps --prefix ./prefix    # build and install
cvcpkg pack {safe_name} --recipes-dir recipes --prefix ./prefix --output-dir ./dist    # package it</code></pre>
          <button class="button is-small is-dark" type="button" onclick="copyRecipeHowto(this)">
            <span class="icon is-small"><i class="fas fa-copy"></i></span><span>Copy commands</span>
          </button>
          <span class="has-text-grey is-size-7 ml-2">Click any file below to view it.</span>
        </div>
        <div id="recipe-yaml"></div>
        <div id="recipe-artifacts"></div>
      </div>
    </div>
  </div>
</section>

<!-- Available builds -->
<!-- Above Quick Start on purpose: "which platforms is this actually built
     for?" is the first question a visitor has, and the install commands
     below are only useful once it is answered. -->
<section class="section pt-0 has-background-black-bis">
  <div class="container">
    <!-- Download Stats Graph -->
    <div class="box has-background-black-ter mb-5" id="download-stats-section" style="display:none">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-chart-area"></i></span> Downloads
        <span class="tag is-dark is-rounded ml-2" id="download-total">0</span>
      </h3>
      <div style="position:relative">
        <canvas id="download-chart"></canvas>
      </div>
    </div>

    <h2 class="title is-4 has-text-white mb-4">
      <span class="icon mr-1"><i class="fas fa-box"></i></span> Available Builds
    </h2>

    <div class="table-container" id="builds-table">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th class="is-sortable" data-key="platform">Platform <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="arch">Arch <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="version">Version <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="build_type">Config <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="link">Link <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="size_bytes">Size <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="release_tag">Release <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="published_at">Published <span class="sort-arrow"></span></th>
            <th>DL</th>
          </tr>
        </thead>
        <tbody id="builds-body">
          <tr>
            <td colspan="9" class="has-text-centered py-6">
              <span class="icon is-large has-text-link">
                <i class="fas fa-spinner fa-spin fa-2x"></i>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- Quick Start -->
<section class="section pt-0 has-background-black-bis">
  <div class="container">
    <!-- Integration guide -->
    <div class="box has-background-black-ter mt-4">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-terminal"></i></span> Quick Start
      </h3>
      <div class="content" id="quick-start">
        {qs_html}
      </div>
    </div>

    <!-- Manual install guide -->
    <div class="box has-background-black-ter mt-4" id="manual-install" style="display:none">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-download"></i></span> Manual Install
      </h3>
      <div class="content">
        <p class="has-text-grey-lighter">
          You can manually download and extract packages without using the cvcpkg
          CLI. Download <strong>{safe_name}</strong> and all its dependencies for your
          platform, then extract them into a single prefix directory:
        </p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;" id="manual-script" data-tail="{manual_tail_attr}">mkdir -p /opt/cvcpkg
# Download {safe_name} and its dependencies, then:
tar --zstd -xf &lt;package&gt;.tar.zst -C /opt/cvcpkg</pre>
        <p class="has-text-grey-lighter mt-3">
          Each package extracts into the same prefix layout (<code>lib/</code>, <code>include/</code>, etc.)
          so they compose correctly. {manual_tail_html}
      </div>
    </div>
  </div>
</section>

<!-- Build Notes -->
<section class="section pt-0 has-background-black-bis" id="pkg-notes-section" style="display:none">
  <div class="container">
    <h2 class="title is-5 has-text-white mb-3">
      <span class="icon mr-1"><i class="fas fa-sticky-note"></i></span> Build Notes
    </h2>
    <div id="pkg-notes-list"></div>
  </div>
</section>

<!-- Recent build jobs -->
<!-- Last on the page: this is fleet activity for maintainers, not something
     a consumer of the package needs in order to use it. -->
<section class="section pt-0 has-background-black-bis">
  <div class="container">
    <div class="box has-background-black-ter" id="build-jobs-section" style="display:none">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-hard-hat"></i></span> Recent Build Jobs
        <a href="/builds" class="is-size-7 has-text-link ml-3">View all &rarr;</a>
        <a href="/live" class="is-size-7 has-text-link ml-3">Live builds &rarr;</a>
      </h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead>
            <tr>
              <th>Status</th>
              <th>Platform</th>
              <th>Arch</th>
              <th>Config</th>
              <th>Link</th>
              <th>Builder</th>
              <th>Duration</th>
              <th>DAG</th>
              <th>Submitted</th>
              <th>Log</th>
            </tr>
          </thead>
          <tbody id="build-jobs-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_DETAIL_JS}

document.addEventListener('DOMContentLoaded', () => {{
  {_NAVBAR_JS}
  document.querySelectorAll('#builds-table th.is-sortable').forEach(th => {{
    th.addEventListener('click', () => sortBy(th.dataset.key));
  }});
  init({_js_string_literal(name)}, {_js_string_literal(org)});
  loadDownloadStats({_js_string_literal(name)});
  loadBuildJobs({_js_string_literal(name)});
}});
</script>
</body>
</html>"""


# ── Organizations listing page ───────────────────────────────────


def orgs_listing_html() -> str:
    """Return the HTML for the organizations listing page."""
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Organizations &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section has-background-black-bis">
  <div class="container">
    <h1 class="title is-3 has-text-white">
      <span class="icon mr-2"><i class="fas fa-building"></i></span> Organizations
    </h1>
    <p class="subtitle is-6 has-text-grey-lighter mb-5">
      Organizations can publish and manage their own packages on cvcpkg.
    </p>

    <div id="orgs-list">
      <div class="has-text-centered py-6">
        <span class="icon is-large has-text-link"><i class="fas fa-spinner fa-spin fa-2x"></i></span>
      </div>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_NAVBAR_JS}

async function init() {{
  try {{
    const resp = await fetch('/v1/orgs?limit=200');
    const data = await resp.json();
    const orgs = data.organizations || [];
    render(orgs);
  }} catch (err) {{
    document.getElementById('orgs-list').innerHTML =
      '<p class="has-text-grey-light">Failed to load organizations.</p>';
  }}
}}

function render(orgs) {{
  const container = document.getElementById('orgs-list');
  if (orgs.length === 0) {{
    container.innerHTML = `
      <div class="has-text-centered py-6">
        <span class="icon is-large has-text-grey-light"><i class="fas fa-building fa-3x"></i></span>
        <p class="title is-5 has-text-grey-light mt-4">No organizations yet</p>
        <p class="subtitle is-6 has-text-grey">
          Create one via the API: <code>POST /v1/orgs</code>
        </p>
      </div>`;
    return;
  }}

  container.innerHTML = '<div class="columns is-multiline">' +
    orgs.map(o => `
      <div class="column is-4">
        <div class="box has-background-black-ter" style="height:100%">
          <article class="media">
            <div class="media-left">
              ${{o.logo_url
                ? '<figure class="image is-64x64"><img src="' + esc(o.logo_url) + '" alt="' + esc(o.slug) + '" style="border-radius:8px"></figure>'
                : '<span class="icon is-large has-text-link"><i class="fas fa-building fa-2x"></i></span>'
              }}
            </div>
            <div class="media-content">
              <a href="/org/${{encodeURIComponent(o.slug)}}" class="title is-5 has-text-link">${{esc(o.display_name)}}</a>
              ${{o.is_private ? '<span class="tag is-warning is-light ml-2"><span class="icon is-small"><i class="fas fa-lock"></i></span><span>private</span></span>' : ''}}
              <p class="is-size-7 has-text-grey-light">${{esc(o.slug)}}</p>
              ${{o.description ? '<p class="is-size-7 has-text-grey-lighter mt-2">' + esc(o.description) + '</p>' : ''}}
              ${{o.storage_limit_bytes != null ? '<p class="is-size-7 has-text-grey mt-2">Storage: ' + fmtSize(o.storage_used_bytes) + ' / ' + fmtSize(o.storage_limit_bytes) + '</p>' : ''}}
            </div>
          </article>
        </div>
      </div>
    `).join('') +
    '</div>';
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


# ── Organization detail page ─────────────────────────────────────


def org_detail_html(slug: str) -> str:
    """Return the HTML for an organization detail page."""
    import html as _html

    safe_slug = _html.escape(slug, quote=True)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"{safe_slug} &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li><a href="/orgs" class="has-text-grey-light">Organizations</a></li>
        <li class="is-active"><a href="#" class="has-text-light">{safe_slug}</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="columns">
      <div class="column is-8">
        <div class="media mb-4">
          <div class="media-left" id="org-logo">
            <span class="icon is-large has-text-link"><i class="fas fa-building fa-2x"></i></span>
          </div>
          <div class="media-content">
            <h1 class="title is-2 has-text-white">
              <span id="org-name">{safe_slug}</span><span id="org-private-badge" class="tag is-warning is-light is-medium ml-2" style="display:none"><span class="icon is-small"><i class="fas fa-lock"></i></span><span>private</span></span>
            </h1>
            <p class="subtitle is-6 has-text-grey-lighter" id="org-desc"></p>
          </div>
        </div>
        <div id="org-homepage" style="display:none" class="mb-3">
          <span class="icon"><i class="fas fa-link"></i></span>
          <a id="org-homepage-link" href="#" class="has-text-link" target="_blank" rel="noopener noreferrer"></a>
        </div>
      </div>
      <div class="column is-4">
        <div class="box has-background-black-ter">
          <div class="columns is-mobile">
            <div class="column has-text-centered">
              <p class="title is-4 has-text-info" id="org-pkg-count">&mdash;</p>
              <p class="heading has-text-grey-light">Packages</p>
            </div>
            <div class="column has-text-centered" id="org-storage-col">
              <p class="title is-4 has-text-warning" id="org-storage">&mdash;</p>
              <p class="heading has-text-grey-light">Storage</p>
            </div>
          </div>
          <progress class="progress is-small is-link mt-2" id="org-storage-bar" value="0" max="100">0%</progress>
        </div>
      </div>
    </div>

    <h3 class="title is-5 has-text-white mt-5 mb-3">
      <span class="icon mr-1"><i class="fas fa-users"></i></span> Members
      <a href="/org/{safe_slug}/manage" class="button is-small is-dark ml-3">
        <span class="icon is-small"><i class="fas fa-users-gear"></i></span>
        <span>Manage</span>
      </a>
    </h3>
    <div id="org-members" class="mb-5">
      <span class="has-text-grey-light">Loading...</span>
    </div>

    <h3 class="title is-5 has-text-white mt-5 mb-3">
      <span class="icon mr-1"><i class="fas fa-box"></i></span> Packages
    </h3>
    <div id="org-packages">
      <span class="has-text-grey-light">Loading...</span>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_NAVBAR_JS}

async function init() {{
  try {{
    const resp = await fetch('/v1/orgs/' + encodeURIComponent({_js_string_literal(slug)}));
    const data = await resp.json();
    renderOrg(data);
  }} catch (err) {{
    document.getElementById('org-packages').innerHTML =
      '<p class="has-text-grey-light">Failed to load organization.</p>';
  }}
}}

function renderOrg(data) {{
  const o = data.org;
  document.getElementById('org-name').textContent = o.display_name;
  if (o.is_private) document.getElementById('org-private-badge').style.display = '';
  if (o.description) document.getElementById('org-desc').textContent = o.description;
  if (o.logo_url) {{
    document.getElementById('org-logo').innerHTML =
      '<figure class="image is-64x64"><img src="' + esc(o.logo_url) + '" alt="' + esc(o.display_name || o.slug) + '" style="border-radius:8px"></figure>';
  }}
  if (o.homepage) {{
    const el = document.getElementById('org-homepage');
    el.style.display = '';
    const link = document.getElementById('org-homepage-link');
    if (new RegExp('^https?://', 'i').test(o.homepage || '')) link.href = o.homepage;
    link.textContent = o.homepage;
  }}

  if (o.storage_limit_bytes != null) {{
    document.getElementById('org-storage').textContent = fmtSize(o.storage_used_bytes);
    const pct = o.storage_limit_bytes > 0 ? Math.round(o.storage_used_bytes / o.storage_limit_bytes * 100) : 0;
    document.getElementById('org-storage-bar').value = pct;
  }} else {{
    // Storage is member/super-admin-only — omit the storage UI entirely.
    const col = document.getElementById('org-storage-col');
    if (col) col.style.display = 'none';
    const bar = document.getElementById('org-storage-bar');
    if (bar) bar.style.display = 'none';
  }}

  // Members
  const members = data.members || [];
  const memEl = document.getElementById('org-members');
  if (members.length === 0) {{
    memEl.innerHTML = '<p class="has-text-grey">No members.</p>';
  }} else {{
    memEl.innerHTML = '<div class="tags">' + members.map(m =>
      '<span class="tag is-dark is-medium">' +
      '<span class="icon is-small mr-1"><i class="fas fa-user"></i></span>' +
      esc(m.token_name) +
      (m.role === 'owner' ? ' <span class="tag is-warning is-light is-small ml-1">owner</span>' : '') +
      '</span>'
    ).join(' ') + '</div>';
  }}

  // Packages
  const pkgs = data.packages || [];
  document.getElementById('org-pkg-count').textContent = pkgs.length;
  const pkgEl = document.getElementById('org-packages');
  if (pkgs.length === 0) {{
    pkgEl.innerHTML = '<p class="has-text-grey">No packages published yet.</p>';
  }} else {{
    const groups = {{}};
    pkgs.forEach(p => {{
      if (!groups[p.name]) groups[p.name] = [];
      groups[p.name].push(p);
    }});
    pkgEl.innerHTML = '<div class="table-container"><table class="table is-fullwidth is-hoverable is-dark is-striped"><thead><tr>' +
      '<th>Package</th><th>Version</th><th>Builds</th><th>Size</th>' +
      '</tr></thead><tbody>' +
      Object.entries(groups).sort((a,b) => a[0].localeCompare(b[0])).map(([name, builds]) => {{
        const totalSize = builds.reduce((s, b) => s + (b.size_bytes || 0), 0);
        const orgSlug = builds[0].org || '';
        const pkgUrl = orgSlug
          ? '/package/' + encodeURIComponent(orgSlug) + '/' + encodeURIComponent(name)
          : '/package/' + encodeURIComponent(name);
        const displayName = orgSlug
          ? '<span class="has-text-grey-light">' + esc(orgSlug) + '/</span><strong>' + esc(name) + '</strong>'
          : '<strong>' + esc(name) + '</strong>';
        return '<tr><td><a href="' + pkgUrl + '" class="has-text-link">' + displayName + '</a></td>' +
          '<td><code>' + esc(builds[0].version) + '</code></td>' +
          '<td><span class="tag is-dark is-rounded">' + builds.length + '</span></td>' +
          '<td class="is-family-monospace is-size-7 has-text-grey-light">' + fmtSize(totalSize) + '</td></tr>';
      }}).join('') +
      '</tbody></table></div>';
  }}
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


# ── Organization member management page (session-authed) ─────────


def org_manage_html(
    slug: str,
    *,
    org,
    members,
    principals,
    csrf_add: str,
    csrf_remove: str,
    field: str,
    flash=None,
) -> str:
    """Owner-facing member management for one org — server-rendered, POST forms.

    A member is a person (an SSO principal) or a machine token.  Add someone by
    their username (they must have signed in once) or by a machine-token name,
    and grant ``owner`` to make them an org admin.  Mirrors the ``/account``
    surface: session cookie + CSRF, no bearer token in the browser.
    """
    import html as _html

    esc = lambda s: _html.escape(str(s), quote=True)  # noqa: E731
    safe_slug = esc(slug)
    display = esc(getattr(org, "display_name", "") or slug)

    flash_html = ""
    if flash is not None:
        kind, body = flash
        flash_html = f'<div class="notification {esc(kind)}">{body}</div>'

    # A datalist of known principal names to make the add form autocompleting.
    options = "".join(f'<option value="{esc(p)}">' for p in principals)

    if members:
        rows = ""
        for m in members:
            name = esc(m.token_name)
            krole = esc(getattr(m.role, "value", m.role))
            kind = esc(getattr(m, "kind", "") or "?")
            kind_tag = {
                "user": '<span class="tag is-info is-light">person</span>',
                "token": '<span class="tag is-dark">token</span>',
                "orphan": '<span class="tag is-danger is-light">orphan</span>',
            }.get(getattr(m, "kind", ""), '<span class="tag is-dark">?</span>')
            owner_tag = (
                '<span class="tag is-warning is-light ml-1">owner</span>'
                if krole == "owner"
                else ""
            )
            rows += f"""
            <tr>
              <td class="is-family-monospace">{name}</td>
              <td>{kind_tag}</td>
              <td>{krole} {owner_tag}</td>
              <td class="has-text-right">
                <form method="post" action="/org/{safe_slug}/members/remove" style="display:inline"
                      onsubmit="return confirm('Remove this member from {safe_slug}?')">
                  <input type="hidden" name="{esc(field)}" value="{esc(csrf_remove)}">
                  <input type="hidden" name="token_name" value="{name}">
                  <button class="button is-small is-danger is-light" type="submit">Remove</button>
                </form>
              </td>
            </tr>"""
        members_table = f"""
        <div class="table-container">
          <table class="table is-fullwidth is-dark is-striped is-hoverable">
            <thead><tr><th>Member</th><th>Type</th><th>Role</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""
    else:
        members_table = '<p class="has-text-grey-light">No members yet.</p>'

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"Manage {display} &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li><a href="/orgs" class="has-text-grey-light">Organizations</a></li>
        <li><a href="/org/{safe_slug}" class="has-text-grey-light">{display}</a></li>
        <li class="is-active"><a href="#" class="has-text-light">Members</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <h1 class="title is-3 has-text-white">
      <span class="icon mr-2"><i class="fas fa-users-gear"></i></span> Manage members
      <span class="is-size-5 has-text-grey-light">&mdash; {display}</span>
    </h1>

    {flash_html}

    <div class="box has-background-black-ter">
      <h2 class="title is-5 has-text-white">Add a member</h2>
      <p class="subtitle is-6 has-text-grey-lighter">
        Add a person by their <strong>username</strong> (they must have signed in once),
        or a <strong>machine token</strong> by its name. Grant <strong>owner</strong> to make
        them an org admin who can manage members.
      </p>
      <form method="post" action="/org/{safe_slug}/members">
        <input type="hidden" name="{esc(field)}" value="{esc(csrf_add)}">
        <div class="field is-grouped is-grouped-multiline">
          <div class="control is-expanded">
            <input class="input" type="text" name="token_name" list="known-principals"
                   placeholder="username or machine-token name" autocomplete="off" required>
            <datalist id="known-principals">{options}</datalist>
          </div>
          <div class="control">
            <div class="select">
              <select name="principal_kind">
                <option value="user">person (SSO username)</option>
                <option value="token">machine token</option>
                <option value="auto">auto-detect</option>
              </select>
            </div>
          </div>
          <div class="control">
            <div class="select">
              <select name="role">
                <option value="member">member</option>
                <option value="owner">owner (admin)</option>
              </select>
            </div>
          </div>
          <div class="control">
            <button class="button is-link" type="submit">Add</button>
          </div>
        </div>
      </form>
    </div>

    <h2 class="title is-5 has-text-white mt-5">Current members</h2>
    {members_table}

    <a href="/org/{safe_slug}" class="button is-small is-dark mt-3">&larr; Back to organization</a>
  </div>
</section>

{_footer_html()}
</body>
</html>"""


# ── Guide / Getting Started page ─────────────────────────────────

_GUIDE_CSS = r"""
.guide-section { padding: 2rem 0; }
.guide-section + .guide-section { border-top: 1px solid #363636; }
.guide-code pre {
  background: #1a1a2e; border-radius: 6px; padding: 1rem 1.25rem;
  overflow-x: auto; font-size: 0.9rem;
}
.guide-code code { color: #48c774; }
.guide-step {
  counter-increment: guide-step;
  padding-left: 2.5rem; position: relative; margin-bottom: 1.5rem;
}
.guide-step::before {
  content: counter(guide-step);
  position: absolute; left: 0; top: 0;
  width: 1.75rem; height: 1.75rem; border-radius: 50%;
  background: linear-gradient(135deg, #3273dc, #48c774);
  color: #fff; font-weight: 700; font-size: 0.85rem;
  display: flex; align-items: center; justify-content: center;
}
.toc a { color: #3273dc; text-decoration: none; }
.toc a:hover { text-decoration: underline; }
.toc li { margin-bottom: 0.35rem; }
"""


# ── Tag listing page ─────────────────────────────────────────────


def tags_listing_html() -> str:
    """Return the HTML for the tag listing/browse page."""
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Tags &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section has-background-black-bis">
  <div class="container">
    <h1 class="title is-3 has-text-white">
      <span class="icon mr-2"><i class="fas fa-tags"></i></span> Browse Tags
    </h1>
    <p class="subtitle is-6 has-text-grey-lighter mb-5">
      Packages are organized by tags.  Click a tag to see its description
      and the packages it contains.
    </p>

    <div class="field mb-5">
      <div class="control has-icons-left">
        <input class="input is-dark" type="text" id="tag-search"
               placeholder="Filter tags..." />
        <span class="icon is-left"><i class="fas fa-search"></i></span>
      </div>
    </div>

    <div class="columns is-multiline" id="tags-grid">
      <div class="column is-12 has-text-centered py-6">
        <span class="icon is-large has-text-link">
          <i class="fas fa-spinner fa-spin fa-2x"></i>
        </span>
      </div>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_NAVBAR_JS}

let allTags = [];

async function init() {{
  try {{
    const resp = await fetch('/v1/tags/all');
    const data = await resp.json();
    allTags = data.tags || [];
    render(allTags);
  }} catch (err) {{
    document.getElementById('tags-grid').innerHTML =
      '<div class="column is-12"><p class="has-text-grey-light">Failed to load tags.</p></div>';
  }}
}}

function render(tags) {{
  const grid = document.getElementById('tags-grid');
  if (tags.length === 0) {{
    grid.innerHTML = `
      <div class="column is-12 has-text-centered py-6">
        <span class="icon is-large has-text-grey-light"><i class="fas fa-tags fa-3x"></i></span>
        <p class="title is-5 has-text-grey-light mt-4">No tags yet</p>
        <p class="subtitle is-6 has-text-grey">
          Tags are automatically discovered from published packages,
          or created by admins via <code>POST /v1/tags</code>.
        </p>
      </div>`;
    return;
  }}

  grid.innerHTML = tags.map(t => {{
    const href = '/tag/' + encodeURIComponent(t.name);
    return `
      <div class="column is-3">
        <a href="${{href}}" class="box has-background-black-ter has-text-centered" style="display:block; border:1px solid #363636;">
          <span class="icon is-medium has-text-info mb-2"><i class="fas fa-tag fa-lg"></i></span>
          <p class="has-text-white has-text-weight-bold">${{esc(t.display_name || t.name)}}</p>
          <p class="has-text-grey-light is-size-7 mt-1">${{t.package_count}} ${{t.package_count === 1 ? 'package' : 'packages'}}</p>
        </a>
      </div>`;
  }}).join('');
}}

document.addEventListener('DOMContentLoaded', () => {{
  document.getElementById('tag-search').addEventListener('input', e => {{
    const q = e.target.value.toLowerCase();
    if (!q) {{ render(allTags); return; }}
    render(allTags.filter(t =>
      t.name.toLowerCase().includes(q) ||
      (t.display_name || '').toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      (t.orgs || []).some(o => o.toLowerCase().includes(q))
    ));
  }});
  init();
}});
</script>
</body>
</html>"""


# ── Tag detail page ──────────────────────────────────────────────


def tag_detail_html(tag_name: str, org_slug: str = "") -> str:
    """Return the HTML for a tag detail page showing description + packages."""

    safe_name = _html.escape(tag_name, quote=True)
    safe_org = _html.escape(org_slug, quote=True)
    display = f"{safe_org}/{safe_name}" if safe_org else safe_name

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"{display} &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li><a href="/tags" class="has-text-grey-light">Tags</a></li>
        <li class="is-active"><a href="#" class="has-text-light">{display}</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="columns">
      <div class="column is-8">
        <div class="media mb-4">
          <div class="media-left" id="tag-logo">
            <span class="icon is-large has-text-info"><i class="fas fa-tag fa-2x"></i></span>
          </div>
          <div class="media-content">
            <h1 class="title is-2 has-text-white" id="tag-title">{display}</h1>
            <p class="subtitle is-6 has-text-grey-lighter" id="tag-desc"></p>
          </div>
        </div>
      </div>
      <div class="column is-4">
        <div class="box has-background-black-ter">
          <div class="has-text-centered">
            <p class="title is-3 has-text-info" id="tag-pkg-count">&mdash;</p>
            <p class="heading has-text-grey-light">Packages</p>
          </div>
        </div>
      </div>
    </div>

    <h3 class="title is-5 has-text-white mt-5 mb-3">
      <span class="icon mr-1"><i class="fas fa-box"></i></span> Packages
    </h3>
    <div class="table-container">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th>Package</th>
            <th>Version</th>
            <th>Platforms</th>
            <th>Builds</th>
            <th>Size</th>
          </tr>
        </thead>
        <tbody id="tag-packages">
          <tr>
            <td colspan="5" class="has-text-centered py-6">
              <span class="icon is-large has-text-link">
                <i class="fas fa-spinner fa-spin fa-2x"></i>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_NAVBAR_JS}

const TAG_NAME = {_js_string_literal(tag_name)};
const TAG_ORG = {_js_string_literal(org_slug)};

async function loadTagMeta() {{
  // Curated display name / description / logo, if any.
  try {{
    const resp = await fetch('/v1/tags/all');
    const data = await resp.json();
    const match = (data.tags || []).find(t => t.name === TAG_NAME.toLowerCase());
    if (match) {{
      if (match.display_name) {{
        document.getElementById('tag-title').textContent =
          (TAG_ORG ? TAG_ORG + '/' : '') + match.display_name;
      }}
      if (match.description) {{
        document.getElementById('tag-desc').textContent = match.description;
      }}
      if (match.logo_url) {{
        document.getElementById('tag-logo').innerHTML =
          '<figure class="image is-64x64"><img src="' + esc(match.logo_url) + '" alt="' + esc(match.display_name || match.name) + '" style="border-radius:8px"></figure>';
      }}
    }}
  }} catch (_) {{}}
}}

async function fetchCandidates() {{
  // /v1/packages caps a page at 1000 bundles; a popular tag spans several.
  const base = '/v1/packages?limit=1000&search=' + encodeURIComponent(TAG_NAME) +
    (TAG_ORG ? '&org=' + encodeURIComponent(TAG_ORG) : '');
  const out = [];
  for (let offset = 0; ; ) {{
    const resp = await fetch(base + '&offset=' + offset);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const page = data.packages || [];
    out.push(...page);
    offset += page.length;
    if (page.length === 0 || offset >= (data.total || 0)) return out;
  }}
}}

async function init() {{
  loadTagMeta();

  // Load packages with this tag
  try {{
    const pkgs = (await fetchCandidates()).filter(p => {{
      const tags = (p.tags || '').split(',').map(t => t.trim().toLowerCase());
      return tags.includes(TAG_NAME.toLowerCase());
    }});
    // If org-scoped, also filter by org
    const filtered = TAG_ORG
      ? pkgs.filter(p => (p.org || '') === TAG_ORG)
      : pkgs;
    renderPackages(filtered);
  }} catch (err) {{
    document.getElementById('tag-packages').innerHTML =
      '<tr><td colspan="5" class="has-text-centered has-text-grey-light">Failed to load packages.</td></tr>';
  }}
}}

function renderPackages(pkgs) {{
  // Group by name
  const groups = {{}};
  pkgs.forEach(p => {{
    if (!groups[p.name]) {{
      groups[p.name] = {{
        name: p.name, version: p.version,
        builds: [], platforms: new Set(), totalSize: 0,
      }};
    }}
    const g = groups[p.name];
    g.builds.push(p);
    if (p.platform) g.platforms.add(p.platform);
    g.totalSize += p.size_bytes || 0;
  }});

  const sorted = Object.values(groups).sort((a, b) =>
    a.name.toLowerCase().localeCompare(b.name.toLowerCase())
  );

  document.getElementById('tag-pkg-count').textContent = sorted.length;

  const tbody = document.getElementById('tag-packages');
  if (sorted.length === 0) {{
    tbody.innerHTML = `
      <tr><td colspan="5">
        <div class="has-text-centered py-4">
          <p class="has-text-grey-light">No packages have this tag yet.</p>
        </div>
      </td></tr>`;
    return;
  }}

  tbody.innerHTML = sorted.map(g => `
    <tr class="pkg-card">
      <td>
        <a class="pkg-link" href="/package/${{encodeURIComponent(g.name)}}">
          <strong>${{esc(g.name)}}</strong>
        </a>
      </td>
      <td><code>${{esc(g.version)}}</code></td>
      <td>${{[...g.platforms].sort().map(p => platformTag(p)).join(' ')}}</td>
      <td><span class="tag is-dark is-rounded">${{g.builds.length}}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${{fmtSize(g.totalSize)}}</span></td>
    </tr>
  `).join('');
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


# ── Builders / Builds / Build-detail / Recipes pages ─────────────

_BUILD_HELPERS_JS = r"""
function fmtDuration(start, end) {
  if (!start || !end) return '\u2014';
  const s = (new Date(end) - new Date(start)) / 1000;
  if (s < 60) return s.toFixed(1) + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.round((s%3600)/60) + 'm';
}
function fmtRelative(iso) {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return 'just now';
  if (s < 60) return Math.floor(s) + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}
function statusCls(status) {
  return {
    succeeded: 'is-success', failed: 'is-danger', running: 'is-info',
    pending: 'is-light', dispatched: 'is-warning', cancelled: 'is-dark',
    timed_out: 'is-danger'
  }[status] || 'is-light';
}
function authHeaders() {
  const t = localStorage.getItem('cvcpkg_token') || '';
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}
"""

# The browser side of /v1/live/ws (see cvcpkg.server.live).  Reconnects with
# backoff; while the socket is down the page's own onState('offline') handler
# falls back to polling the REST endpoint.
_LIVE_JS = r"""
function openLive(topics, handlers) {
  let ws = null, delay = 1000, stopped = false, anonymous = false;
  function connect() {
    handlers.onState('connecting');
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try { ws = new WebSocket(proto + '//' + location.host + '/v1/live/ws'); }
    catch (e) { retry(); return; }
    let sentToken = false;
    ws.onopen = () => {
      // handlers.hello() adds per-connection fields (a log to tail, from where
      // the page got to), rebuilt on every reconnect.
      const hello = Object.assign({ type: 'hello', topics: topics },
                                  handlers.hello ? handlers.hello() : {});
      const t = anonymous ? '' : (localStorage.getItem('cvcpkg_token') || '');
      if (t) { hello.token = t; sentToken = true; }
      ws.send(JSON.stringify(hello));
    };
    ws.onmessage = ev => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'ready') { delay = 1000; handlers.onState('live'); }
      handlers.onMessage(msg);
    };
    ws.onclose = ev => {
      ws = null;
      if (stopped) return;
      handlers.onState('offline');
      // 4004: this server has no live updates; polling is all there is.
      if (ev.code === 4004) { stopped = true; return; }
      // A stored token the server refuses: watch the public view instead,
      // as the REST endpoints do.  Refused with no token at all: this server
      // requires one for reads, and retrying cannot help.
      if (ev.code === 4001) {
        if (!sentToken) { stopped = true; return; }
        anonymous = true; delay = 0;
      }
      retry();
    };
  }
  function retry() {
    setTimeout(connect, delay);
    delay = Math.min(Math.max(delay, 1000) * 2, 30000);
  }
  connect();
  return { close() { stopped = true; if (ws) ws.close(); } };
}
function setLiveBadge(el, state) {
  if (!el) return;
  el.classList.remove('is-live', 'is-connecting', 'is-offline');
  el.classList.add('is-' + state);
  el.querySelector('.live-label').textContent =
    { live: 'Live', connecting: 'Connecting\u2026', offline: 'Offline \u2014 polling' }[state] || state;
}
"""


_BUILDERS_JS = r"""
const builders = new Map();   // id -> BuilderInfo
let selectedId = null;
let pollTimer = null;
let modalTimer = null;

function saveToken() {
  const t = document.getElementById('token-input').value.trim();
  if (t) { localStorage.setItem('cvcpkg_token', t); location.reload(); }
}

// ── Formatting ──────────────────────────────────────────────
function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i >= 3 ? 1 : 0) + ' ' + u[i];
}
function fmtUptime(s) {
  if (s == null) return '—';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? d + 'd ' + h + 'h' : h ? h + 'h ' + m + 'm' : m + 'm';
}
function versionTag(b) {
  if (!b.version) return '<span class="tag is-rounded version-tag is-unknown" title="This builder does not report its version">version ?</span>';
  const same = b.version.split('@')[0] === SERVER_VERSION;
  return '<span class="tag is-rounded version-tag ' + (same ? 'is-current' : 'is-other') + '" title="' +
    (same ? 'Same cvcpkg as this server' : 'This server runs cvcpkg ' + esc(SERVER_VERSION)) + '">' +
    '<i class="fas fa-code-branch mr-1"></i>' + esc(b.version) + '</span>';
}
function statusTag(b) {
  const cls = { online: 'is-success', busy: 'is-warning' }[b.status] || 'is-danger';
  return '<span class="tag ' + cls + ' is-rounded"><i class="fas fa-circle mr-1"></i>' + esc(b.status) + '</span>';
}
function pctClass(p) { return p >= 90 ? 'is-danger' : p >= 70 ? 'is-warning' : 'is-success'; }
function meter(label, pct, text) {
  if (pct == null || isNaN(pct)) return '';
  pct = Math.max(0, Math.min(100, pct));
  return '<div class="meter"><div class="meter-label"><span>' + label + '</span><span>' + text + '</span></div>' +
    '<progress class="progress is-small ' + pctClass(pct) + '" value="' + pct.toFixed(0) + '" max="100">' +
    pct.toFixed(0) + '%</progress></div>';
}
function systemMeters(sys) {
  if (!sys) return '';
  let out = '';
  if (sys.cpu_percent != null) out += meter('CPU', sys.cpu_percent, sys.cpu_percent.toFixed(0) + '%' +
    (sys.cpu_count ? ' of ' + sys.cpu_count + ' cores' : ''));
  else if (sys.load_avg && sys.cpu_count) out += meter('Load', 100 * sys.load_avg[0] / sys.cpu_count,
    sys.load_avg[0].toFixed(2) + ' / ' + sys.cpu_count + ' cores');
  if (sys.mem_total_bytes && sys.mem_available_bytes != null) {
    const used = sys.mem_total_bytes - sys.mem_available_bytes;
    out += meter('Memory', 100 * used / sys.mem_total_bytes, fmtBytes(used) + ' / ' + fmtBytes(sys.mem_total_bytes));
  }
  if (sys.disk_total_bytes && sys.disk_free_bytes != null) {
    const used = sys.disk_total_bytes - sys.disk_free_bytes;
    out += meter('Disk', 100 * used / sys.disk_total_bytes, fmtBytes(sys.disk_free_bytes) + ' free of ' + fmtBytes(sys.disk_total_bytes));
  }
  return out;
}

// ── Cards ───────────────────────────────────────────────────
function cardHtml(b) {
  const hb = b.last_heartbeat ? fmtRelative(b.last_heartbeat) : 'never';
  const labels = (b.labels || []).filter(l => !l.startsWith('cvcpkg='));
  const meters = systemMeters(b.system);
  return '<div class="box has-background-black-ter builder-card" tabindex="0" role="button" ' +
      'aria-label="Show details for builder ' + esc(b.name) + '" data-id="' + b.id + '">' +
    '<div class="level is-mobile mb-2"><div class="level-left"><div class="level-item">' +
    '<h3 class="title is-5 has-text-white mb-0">' +
    '<span class="icon mr-1"><i class="fas fa-microchip"></i></span>' + esc(b.name) + '</h3>' +
    '</div></div><div class="level-right"><div class="level-item">' + statusTag(b) + '</div></div></div>' +
    '<div class="content is-small mb-2">' +
    '<p class="mb-2">' + platformTag(b.platform) + ' <span class="has-text-grey-light">' + esc(b.arch) + '</span> ' +
      versionTag(b) + '</p>' +
    '<p class="mb-1"><strong class="has-text-grey-lighter">Jobs:</strong> ' +
    '<span class="has-text-white">' + b.current_jobs + '</span> / ' + b.max_jobs +
    ' &middot; <strong class="has-text-grey-lighter">Heartbeat:</strong> ' +
    '<span class="has-text-grey-light">' + esc(hb) + '</span></p>' +
    (b.org_slug ? '<p class="mb-1"><strong class="has-text-grey-lighter">Org:</strong> ' + esc(b.org_slug) + '</p>' : '') +
    (labels.length ? '<p class="mb-1">' + labels.map(l => '<span class="tag is-dark is-rounded mr-1">' + esc(l) + '</span>').join('') + '</p>' : '') +
    '</div>' + meters + '</div>';
}
function renderSummary(list) {
  const counts = { online: 0, busy: 0, offline: 0 };
  for (const b of list) counts[b.status in counts ? b.status : 'offline']++;
  document.getElementById('builder-summary').textContent = list.length
    ? list.length + ' builder' + (list.length === 1 ? '' : 's') + ' · ' + counts.online + ' online · ' +
      counts.busy + ' busy · ' + counts.offline + ' offline'
    : 'No builders registered.';
}
// One builder changed: redraw its card in place, so focus and scroll stay put.
function renderOne(id) {
  const card = document.querySelector('.builder-card[data-id="' + id + '"]');
  if (!card || !builders.has(id)) { render(); return; }
  const hadFocus = document.activeElement === card;
  card.parentElement.innerHTML = cardHtml(builders.get(id));
  if (hadFocus) document.querySelector('.builder-card[data-id="' + id + '"]').focus();
  renderSummary([...builders.values()]);
  if (selectedId === id) renderModal(false);
}
function render() {
  const list = [...builders.values()].sort((a, b) => a.id - b.id);
  const grid = document.getElementById('builders-grid');
  renderSummary(list);
  if (!list.length) {
    grid.innerHTML = '<div class="column"><div class="notification is-dark">No builders registered.</div></div>';
    return;
  }
  grid.innerHTML = list.map(b => '<div class="column is-6-tablet is-4-desktop">' + cardHtml(b) + '</div>').join('');
  if (selectedId !== null) renderModal(false);
}
function onGridClick(ev) {
  const card = ev.target.closest('.builder-card');
  if (card) openModal(parseInt(card.dataset.id, 10));
}
function onGridKey(ev) {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const card = ev.target.closest('.builder-card');
  if (card) { ev.preventDefault(); openModal(parseInt(card.dataset.id, 10)); }
}

// ── Detail modal ────────────────────────────────────────────
function openModal(id) {
  if (!builders.has(id)) return;
  selectedId = id;
  if (location.hash !== '#builder-' + id) history.replaceState(null, '', '#builder-' + id);
  document.getElementById('builder-modal').classList.add('is-active');
  document.documentElement.classList.add('is-clipped');
  renderModal(true);
  clearInterval(modalTimer);
  // The live socket refreshes the job lists as jobs change; this is the fallback.
  modalTimer = setInterval(() => {
    if (!document.getElementById('live-badge').classList.contains('is-live')) loadJobs(selectedId);
  }, 15000);
}
function closeModal() {
  selectedId = null;
  clearInterval(modalTimer);
  document.getElementById('builder-modal').classList.remove('is-active');
  document.documentElement.classList.remove('is-clipped');
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
}
function dl(rows) {
  return '<dl class="detail-grid">' + rows.filter(r => r[1] !== null && r[1] !== undefined && r[1] !== '')
    .map(r => '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>').join('') + '</dl>';
}
function renderModal(withJobs) {
  const b = builders.get(selectedId);
  if (!b) { document.getElementById('modal-body').innerHTML = '<p class="has-text-grey">This builder was unregistered.</p>'; return; }
  document.getElementById('modal-title').innerHTML =
    '<span class="icon mr-1"><i class="fas fa-microchip"></i></span>' + esc(b.name) + ' ' + statusTag(b);
  const sys = b.system || {};
  const caps = Object.entries(b.capabilities || {}).map(([k, v]) =>
    '<span class="tag is-dark is-rounded mr-1 mb-1">' + esc(v === true ? k : k + ': ' +
      (Array.isArray(v) ? v.map(x => typeof x === 'object' ? (x.platform || '') + '/' + (x.arch || '') : x).join(', ') : JSON.stringify(v))) +
    '</span>').join('');
  const served = (b.served_namespaces || []).map(n => '<span class="tag is-dark is-rounded mr-1">' +
    (n ? esc(n) : 'public') + '</span>').join('');
  const labels = (b.labels || []).map(l => '<span class="tag is-dark is-rounded mr-1 mb-1">' + esc(l) + '</span>').join('');
  const hasSys = b.system && Object.keys(b.system).length;
  document.getElementById('modal-info').innerHTML =
    '<div class="columns">' +
    '<div class="column">' +
    '<h4 class="title is-6 has-text-grey-lighter mb-2">Builder</h4>' +
    dl([
      ['ID', '#' + b.id],
      ['Version', versionTag(b)],
      ['Platform', platformTag(b.platform) + ' ' + esc(b.arch)],
      ['Jobs running', '<span class="has-text-white has-text-weight-semibold">' + b.current_jobs + '</span> of ' + b.max_jobs + ' slots'],
      ['Heartbeat', b.last_heartbeat ? esc(fmtRelative(b.last_heartbeat)) + ' <span class="has-text-grey is-size-7">(' + fmtDate(b.last_heartbeat) + ')</span>' : 'never'],
      ['Org', b.org_slug ? esc(b.org_slug) : 'public'],
      ['Serves', served],
      ['Affinity', b.prefer_affinity ? 'prefers recipes it built before' : null],
      ['Registered', fmtDate(b.created_at) + (b.registered_by ? ' by ' + esc(b.registered_by) : '')],
    ]) +
    '</div>' +
    '<div class="column">' +
    '<h4 class="title is-6 has-text-grey-lighter mb-2">Host</h4>' +
    (hasSys
      ? systemMeters(b.system) + dl([
          ['Load', sys.load_avg ? sys.load_avg.map(x => x.toFixed(2)).join(' ') : null],
          ['Cores', sys.cpu_count],
          ['Memory free', sys.mem_available_bytes != null ? fmtBytes(sys.mem_available_bytes) : null],
          ['Work disk', sys.disk_total_bytes ? fmtBytes(sys.disk_free_bytes) + ' free of ' + fmtBytes(sys.disk_total_bytes) : null],
          ['OS', sys.os ? esc(sys.os) : null],
          ['Python', sys.python ? esc(sys.python) : null],
          ['Host up', sys.uptime_seconds != null ? fmtUptime(sys.uptime_seconds) : null],
          ['Agent up', sys.agent_uptime_seconds != null ? fmtUptime(sys.agent_uptime_seconds) : null],
        ])
      : '<p class="has-text-grey is-size-7">This builder does not report host figures; ' +
        (b.free_disk_gb != null ? 'free disk at its last heartbeat: ' + b.free_disk_gb + ' GiB. ' : '') +
        'Upgrade it to cvcpkg ' + esc(SERVER_VERSION) + ' or newer to see CPU, memory and disk here.</p>') +
    '</div></div>' +
    (labels ? '<h4 class="title is-6 has-text-grey-lighter mb-2 mt-2">Labels</h4><p>' + labels + '</p>' : '') +
    (caps ? '<h4 class="title is-6 has-text-grey-lighter mb-2 mt-3">Capabilities</h4><p>' + caps + '</p>' : '');
  document.getElementById('modal-builds-link').href = '/builds?builder_id=' + b.id;
  if (withJobs) loadJobs(b.id);
}
function jobRow(j) {
  const dur = j.started_at ? fmtDuration(j.started_at, j.finished_at || new Date().toISOString()) : '—';
  return '<tr><td><a href="/build/' + j.id + '" class="has-text-link">#' + j.id + '</a></td>' +
    '<td><span class="tag ' + statusCls(j.status) + ' is-rounded">' + esc(j.status) + '</span></td>' +
    '<td class="has-text-white">' + esc(j.recipe_name) + ' <span class="has-text-grey is-size-7">' + esc(j.recipe_version || '') + '</span></td>' +
    '<td class="is-size-7">' + esc(dur) + '</td>' +
    '<td class="is-size-7 has-text-grey-light">' + esc(fmtRelative(j.submitted_at)) + '</td></tr>';
}
async function fetchJobs(id, extra) {
  const resp = await fetch('/v1/builds?builder_id=' + id + extra, { headers: authHeaders() });
  if (resp.status === 401 || resp.status === 403) return null;
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return (await resp.json()).jobs || [];
}
const NO_JOBS_ACCESS = '<p class="has-text-grey is-size-7">This server requires a token to ' +
  'read; save one on the <a href="/builds">Build Jobs</a> page to see this builder’s jobs.</p>';
let _modalJobsTimer = null;
function scheduleModalJobs() {
  clearTimeout(_modalJobsTimer);
  _modalJobsTimer = setTimeout(() => loadJobs(selectedId), 1000);
}
async function loadJobs(id) {
  const el = document.getElementById('modal-jobs');
  if (id === null || id === undefined) return;
  try {
    const [running, dispatched, recent] = await Promise.all([
      fetchJobs(id, '&status=running&limit=50'),
      fetchJobs(id, '&status=dispatched&limit=50'),
      fetchJobs(id, '&limit=10'),
    ]);
    if (id !== selectedId) return;
    if (running === null) { el.innerHTML = NO_JOBS_ACCESS; return; }
    const active = dispatched.concat(running).sort((a, b) => b.id - a.id);
    const table = rows => '<div class="table-container"><table class="table is-fullwidth is-narrow is-dark is-size-7">' +
      '<thead><tr><th>#</th><th>Status</th><th>Recipe</th><th>Duration</th><th>Submitted</th></tr></thead><tbody>' +
      rows.map(jobRow).join('') + '</tbody></table></div>';
    el.innerHTML =
      '<h4 class="title is-6 has-text-grey-lighter mb-2">Active jobs (' + active.length + ')</h4>' +
      (active.length ? table(active) : '<p class="has-text-grey is-size-7 mb-3">Idle.</p>') +
      '<h4 class="title is-6 has-text-grey-lighter mb-2 mt-3">Recent jobs</h4>' +
      (recent.length ? table(recent) : '<p class="has-text-grey is-size-7">No jobs yet.</p>');
  } catch (e) {
    if (id === selectedId) el.innerHTML = '<p class="has-text-danger is-size-7">Could not load jobs: ' + esc(e.message) + '</p>';
  }
}

// ── Data: REST load + live socket ───────────────────────────
async function loadBuilders() {
  try {
    const resp = await fetch('/v1/builders', { headers: authHeaders() });
    if (resp.status === 401 || resp.status === 403) {
      document.getElementById('auth-prompt').style.display = '';
      document.getElementById('builder-summary').textContent = 'Authentication required';
      return;
    }
    if (!resp.ok) { document.getElementById('builder-summary').textContent = 'Error loading builders'; return; }
    const data = await resp.json();
    builders.clear();
    for (const b of data.builders || []) builders.set(b.id, b);
    render();
  } catch (e) {
    document.getElementById('builder-summary').textContent = 'Error: ' + e.message;
  }
}
function onLive(msg) {
  if (msg.type === 'builders') {
    builders.clear();
    for (const b of msg.builders) builders.set(b.id, b);
    render();
  } else if (msg.type === 'builder') {
    builders.set(msg.builder.id, msg.builder);
    renderOne(msg.builder.id);
  } else if (msg.type === 'builder_removed') {
    builders.delete(msg.id);
    render();
  } else if (msg.type === 'builds') {
    // A job on the builder whose details are open changed: refresh its lists.
    if (selectedId !== null && msg.jobs.some(j => j.builder_id === selectedId)) scheduleModalJobs();
  } else if (msg.type === 'resync') {
    loadBuilders();
  }
}
function onLiveState(state) {
  setLiveBadge(document.getElementById('live-badge'), state);
  clearInterval(pollTimer);
  pollTimer = null;
  if (state === 'offline') pollTimer = setInterval(loadBuilders, 30000);
}

async function init() {
  const grid = document.getElementById('builders-grid');
  grid.addEventListener('click', onGridClick);
  grid.addEventListener('keydown', onGridKey);
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && selectedId !== null) closeModal(); });
  await loadBuilders();
  const m = /^#builder-(\d+)$/.exec(location.hash);
  if (m) openModal(parseInt(m[1], 10));
  openLive(['builders', 'builds'], { onMessage: onLive, onState: onLiveState });
  // Relative heartbeat times age between pushes.
  setInterval(render, 30000);
}
document.addEventListener('DOMContentLoaded', init);
"""


def builders_html() -> str:
    """Return HTML for the Builders Dashboard page."""
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Builders &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li class="is-active"><a href="#" class="has-text-light">Builders</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="level mb-2">
      <div class="level-left">
        <h1 class="title has-text-white mb-0">
          <span class="icon mr-2"><i class="fas fa-server"></i></span> Builders
        </h1>
      </div>
      <div class="level-right">
        <span id="live-badge" class="live-badge is-connecting is-size-7 has-text-grey-light">
          <span class="live-dot"></span><span class="live-label">Connecting&hellip;</span>
        </span>
      </div>
    </div>
    <p class="subtitle is-6 has-text-grey-light mt-1" id="builder-summary">Loading&hellip;</p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>This server requires a token to read. Enter your API token.</p>
      <div class="field has-addons mt-2">
        <div class="control is-expanded">
          <input class="input is-dark" id="token-input" type="password" placeholder="cvctok_&hellip;">
        </div>
        <div class="control">
          <button class="button is-link" onclick="saveToken()">Save</button>
        </div>
      </div>
    </div>

    <div id="builders-grid" class="columns is-multiline"></div>
  </div>
</section>

<div class="modal" id="builder-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <div class="modal-background" onclick="closeModal()"></div>
  <div class="modal-card" style="width:min(960px, calc(100vw - 32px))">
    <header class="modal-card-head has-background-black-ter">
      <p class="modal-card-title has-text-white" id="modal-title"></p>
      <button class="delete" aria-label="close" onclick="closeModal()"></button>
    </header>
    <section class="modal-card-body has-background-black-bis has-text-light" id="modal-body">
      <div id="modal-info"></div>
      <hr class="has-background-grey-darker my-4">
      <div id="modal-jobs"><p class="has-text-grey is-size-7">Loading jobs&hellip;</p></div>
    </section>
    <footer class="modal-card-foot has-background-black-ter">
      <a class="button is-small is-dark" id="modal-builds-link" href="/builds">
        <span class="icon is-small"><i class="fas fa-list"></i></span><span>All builds on this builder</span>
      </a>
    </footer>
  </div>
</div>

{_footer_html()}

<script>
const SERVER_VERSION = {_json.dumps(__version__)};
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}
{_LIVE_JS}
{_BUILDERS_JS}
</script>
</body>
</html>"""


# The builds page's live updates: rows change in place as jobs move; a job the
# page does not show yet (new, or now matching the filters) reloads page 1.
_BUILDS_LIVE_JS = r"""
const changedRows = new Set();
let _reloadTimer = null;
let _everLive = false;
function scheduleReload() {
  clearTimeout(_reloadTimer);
  _reloadTimer = setTimeout(() => loadJobs(), 1000);
}
function jobMatchesFilters(j) {
  const v = id => document.getElementById(id).value.trim();
  if (v('filter-status') && j.status !== v('filter-status')) return false;
  if (v('filter-platform') && j.platform !== v('filter-platform')) return false;
  if (v('filter-arch') && j.arch !== v('filter-arch')) return false;
  if (v('filter-config') && j.config !== v('filter-config')) return false;
  if (v('filter-link') && j.link !== v('filter-link')) return false;
  if (v('filter-recipe') && j.recipe_name !== v('filter-recipe')) return false;
  const dag = v('filter-dag');
  if (dag && !(dag.endsWith('*') ? (j.dag_id || '').startsWith(dag.slice(0, -1)) : j.dag_id === dag)) return false;
  if (v('filter-builder') && String(j.builder_id) !== v('filter-builder')) return false;
  return true;
}
function onBuildsLive(msg) {
  if (msg.type === 'resync') { scheduleReload(); return; }
  if (msg.type !== 'builds') return;
  // DAG progress is computed from the whole page: reload it rather than patch.
  const dagView = !!document.getElementById('filter-dag').value.trim();
  let redraw = false, reload = false;
  for (const j of msg.jobs) {
    const i = currentJobs.findIndex(x => x.id === j.id);
    if (i >= 0) {
      if (dagView || !jobMatchesFilters(j)) { reload = true; continue; }
      const was = currentJobs[i];
      j.depends_on = was.depends_on;  // the live feed leaves them out
      // Flash a row when the job moves, not on every log line it writes.
      if (['status', 'builder_id', 'started_at', 'finished_at'].some(k => was[k] !== j[k])) {
        changedRows.add(j.id);
      }
      currentJobs[i] = j;
      redraw = true;
    } else if (currentOffset === 0 && jobMatchesFilters(j)) {
      reload = true;
    }
  }
  if (reload) scheduleReload();
  else if (redraw) renderJobs();
}
function onBuildsLiveState(state) {
  liveUp = state === 'live';
  setLiveBadge(document.getElementById('live-badge'), state);
  if (state === 'live') {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
    if (_everLive) scheduleReload();  // catch up on what happened while away
    _everLive = true;
  } else if (state === 'offline') {
    loadJobs();  // re-arms the fallback poll if jobs are active
  }
}
// Running jobs' durations tick between updates.
setInterval(() => {
  if (currentJobs.some(j => j.status === 'running')) renderJobs();
}, 5000);
"""


def builds_html() -> str:
    """Return HTML for the Builds Dashboard page."""
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Build Jobs &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li class="is-active"><a href="#" class="has-text-light">Build Jobs</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="level mb-2">
      <div class="level-left">
        <h1 class="title has-text-white mb-0">
          <span class="icon mr-2"><i class="fas fa-hammer"></i></span> Build Jobs
        </h1>
      </div>
      <div class="level-right">
        <span id="live-badge" class="live-badge is-connecting is-size-7 has-text-grey-light">
          <span class="live-dot"></span><span class="live-label">Connecting&hellip;</span>
        </span>
      </div>
    </div>
    <p class="subtitle has-text-grey-light mt-1" id="builds-summary">Loading&hellip;</p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>This server requires a token to read. Enter your API token.</p>
      <div class="field has-addons mt-2">
        <div class="control is-expanded">
          <input class="input is-dark" id="token-input" type="password" placeholder="cvctok_&hellip;">
        </div>
        <div class="control">
          <button class="button is-link" onclick="saveToken()">Save</button>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="columns is-multiline mb-3" id="filters-bar" style="display:none">
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Status</label>
        <div class="control"><div class="select is-small is-dark">
          <select id="filter-status" onchange="loadJobs(true)">
            <option value="">All</option>
            <option value="pending">Pending</option>
            <option value="dispatched">Dispatched</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div></div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Platform</label>
        <div class="control"><div class="select is-small is-dark">
          <select id="filter-platform" onchange="loadJobs(true)">
            <option value="">All</option>
            <option value="linux">Linux</option>
            <option value="freebsd">FreeBSD</option>
            <option value="netbsd">NetBSD</option>
            <option value="openbsd">OpenBSD</option>
            <option value="haiku">Haiku</option>
            <option value="macos">macOS</option>
            <option value="windows">Windows</option>
            <option value="wasm">WASM</option>
            <option value="wasm-mt">WASM (threads)</option>
            <option value="wasi">WASI</option>
            <option value="cosmo">Cosmo</option>
          </select>
        </div></div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Arch</label>
        <div class="control"><div class="select is-small is-dark">
          <select id="filter-arch" onchange="loadJobs(true)">
            <option value="">All</option>
            <option value="x86_64">x86_64</option>
            <option value="aarch64">aarch64</option>
            <option value="wasm32">wasm32</option>
            <option value="fat">fat</option>
          </select>
        </div></div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Config</label>
        <div class="control"><div class="select is-small is-dark">
          <select id="filter-config" onchange="loadJobs(true)">
            <option value="">All</option>
            <option value="release">Release</option>
            <option value="debug">Debug</option>
          </select>
        </div></div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Link</label>
        <div class="control"><div class="select is-small is-dark">
          <select id="filter-link" onchange="loadJobs(true)">
            <option value="">All</option>
            <option value="shared">Shared</option>
            <option value="static">Static</option>
          </select>
        </div></div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Recipe</label>
        <div class="control">
          <input class="input is-small is-dark" id="filter-recipe" placeholder="e.g. zlib"
                 onkeyup="if(event.key==='Enter')loadJobs(true)">
        </div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">DAG</label>
        <div class="control">
          <input class="input is-small is-dark" id="filter-dag" placeholder="DAG ID"
                 onkeyup="if(event.key==='Enter')loadJobs(true)">
        </div></div>
      </div>
      <div class="column is-narrow">
        <div class="field"><label class="label has-text-grey-lighter is-small">Builder</label>
        <div class="control">
          <input class="input is-small is-dark" id="filter-builder" placeholder="ID" style="width:5em"
                 onkeyup="if(event.key==='Enter')loadJobs(true)">
        </div></div>
      </div>
      <div class="column is-narrow pt-5">
        <button class="button is-small is-link mt-2" onclick="loadJobs(true)">
          <span class="icon"><i class="fas fa-search"></i></span><span>Filter</span>
        </button>
        <button class="button is-small is-dark mt-2 ml-1" onclick="loadJobs()" title="Refresh">
          <span class="icon"><i class="fas fa-sync-alt"></i></span>
        </button>
      </div>
    </div>

    <!-- DAG progress (shown when filtering by dag_id) -->
    <div id="dag-section" class="box has-background-black-ter mb-4" style="display:none">
      <h3 class="title is-6 has-text-white">
        <span class="icon mr-1"><i class="fas fa-project-diagram"></i></span>
        DAG: <span id="dag-id-label"></span>
      </h3>
      <div id="dag-progress"></div>
    </div>

    <div class="table-container">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th>#</th>
            <th>Status</th>
            <th>Recipe</th>
            <th>Platform</th>
            <th>Arch</th>
            <th>Config</th>
            <th>Link</th>
            <th>Builder</th>
            <th>Duration</th>
            <th>DAG</th>
            <th>Submitted</th>
            <th>Log</th>
          </tr>
        </thead>
        <tbody id="builds-body"></tbody>
      </table>
    </div>

    <div class="level" id="pagination" style="display:none">
      <div class="level-left">
        <button class="button is-small is-dark" id="btn-prev" onclick="prevPage()">
          <span class="icon"><i class="fas fa-chevron-left"></i></span><span>Prev</span>
        </button>
      </div>
      <div class="level-item"><span id="page-info" class="has-text-grey-light is-size-7"></span></div>
      <div class="level-right">
        <button class="button is-small is-dark" id="btn-next" onclick="nextPage()">
          <span>Next</span><span class="icon"><i class="fas fa-chevron-right"></i></span>
        </button>
      </div>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}
{_LIVE_JS}
{_BUILDS_LIVE_JS}

let currentOffset = 0;
const PAGE_SIZE = 50;
let totalJobs = 0;
let _refreshTimer = null;
let currentJobs = [];
let liveUp = false;

function saveToken() {{
  const t = document.getElementById('token-input').value.trim();
  if (t) {{ localStorage.setItem('cvcpkg_token', t); location.reload(); }}
}}

function prevPage() {{ if (currentOffset >= PAGE_SIZE) {{ currentOffset -= PAGE_SIZE; loadJobs(); }} }}
function nextPage() {{ if (currentOffset + PAGE_SIZE < totalJobs) {{ currentOffset += PAGE_SIZE; loadJobs(); }} }}

function renderJobs() {{
  const jobs = currentJobs;
  const tbody = document.getElementById('builds-body');
  if (jobs.length === 0) {{
    tbody.innerHTML = '<tr><td colspan="12" class="has-text-centered has-text-grey py-4">No build jobs found.</td></tr>';
  }} else {{
  tbody.innerHTML = jobs.map(j => {{
    const logLink = (j.status === 'running')
      ? '<a class="button is-small is-info is-outlined" href="/build/' + j.id + '" title="Live Log">' +
        '<span class="icon is-small"><i class="fas fa-stream"></i></span></a>'
      : (j.status === 'succeeded' || j.status === 'failed')
      ? '<a class="button is-small is-outlined" href="/build/' + j.id + '" title="View Log">' +
        '<span class="icon is-small"><i class="fas fa-file-alt"></i></span></a>'
      : '';
    const dagLink = j.dag_id
      ? '<a href="/builds?dag_id=' + encodeURIComponent(j.dag_id) + '" class="tag is-dark is-rounded is-small">' +
        '<span class="icon is-small"><i class="fas fa-project-diagram"></i></span>&nbsp;' + esc(j.dag_id) + '</a>'
      : '';
    const errTip = (j.status === 'failed' && j.error_message)
      ? ' title="' + esc(j.error_message.substring(0, 200)) + '"'
      : '';
    const builderName = j.builder_id ? 'b' + j.builder_id : '\\u2014';
    return '<tr' + (changedRows.has(j.id) ? ' class="is-new"' : '') + '>' +
      '<td class="has-text-grey-light">' + j.id + '</td>' +
      '<td><a href="/build/' + j.id + '"><span class="tag ' + statusCls(j.status) + ' is-rounded"' + errTip + '>' + esc(j.status) + '</span></a></td>' +
      '<td><a href="' + (j.org_slug ? '/package/' + encodeURIComponent(j.org_slug) + '/' + encodeURIComponent(j.recipe_name) : '/package/' + encodeURIComponent(j.recipe_name)) +
      '" class="has-text-link">' + (j.org_slug ? esc(j.org_slug) + '/' : '') + esc(j.recipe_name) + '</a></td>' +
      '<td>' + platformTag(j.platform) + '</td>' +
      '<td class="has-text-grey-light">' + esc(j.arch) + '</td>' +
      '<td class="has-text-grey-light">' + esc(j.config) + '</td>' +
      '<td class="has-text-grey-light">' + esc(j.link) + '</td>' +
      '<td class="has-text-grey-light">' + esc(builderName) + '</td>' +
      '<td class="has-text-grey-light">' + fmtDuration(j.started_at, j.finished_at || (j.status === 'running' ? new Date().toISOString() : null)) + '</td>' +
      '<td>' + dagLink + '</td>' +
      '<td class="has-text-grey-light is-size-7">' + fmtDate(j.submitted_at) + '</td>' +
      '<td>' + logLink + '</td>' +
      '</tr>';
  }}).join('');
  }} // end else (jobs.length > 0)
  changedRows.clear();
}}

async function loadJobs(resetOffset) {{
  if (resetOffset) currentOffset = 0;
  const status = document.getElementById('filter-status').value;
  const platform = document.getElementById('filter-platform').value;
  const arch = document.getElementById('filter-arch').value;
  const config = document.getElementById('filter-config').value;
  const link = document.getElementById('filter-link').value;
  const recipe = document.getElementById('filter-recipe').value.trim();
  const dagId = document.getElementById('filter-dag').value.trim();
  const builderId = document.getElementById('filter-builder').value.trim();

  // Sync URL query string
  const qp = new URLSearchParams();
  if (status) qp.set('status', status);
  if (platform) qp.set('platform', platform);
  if (arch) qp.set('arch', arch);
  if (config) qp.set('config', config);
  if (link) qp.set('link', link);
  if (recipe) qp.set('recipe_name', recipe);
  if (dagId) qp.set('dag_id', dagId);
  if (builderId) qp.set('builder_id', builderId);
  const qs = qp.toString();
  history.replaceState(null, '', qs ? '?' + qs : location.pathname);

  let url = '/v1/builds?limit=' + PAGE_SIZE + '&offset=' + currentOffset;
  if (status) url += '&status=' + encodeURIComponent(status);
  if (platform) url += '&platform=' + encodeURIComponent(platform);
  if (arch) url += '&arch=' + encodeURIComponent(arch);
  if (config) url += '&config=' + encodeURIComponent(config);
  if (link) url += '&link=' + encodeURIComponent(link);
  if (recipe) url += '&recipe_name=' + encodeURIComponent(recipe);
  if (dagId) url += '&dag_id=' + encodeURIComponent(dagId);
  if (builderId) url += '&builder_id=' + encodeURIComponent(builderId);

  try {{
    const resp = await fetch(url, {{ headers: authHeaders() }});
    if (resp.status === 401 || resp.status === 403) {{
      document.getElementById('auth-prompt').style.display = '';
      document.getElementById('builds-summary').textContent = 'Authentication required';
      return;
    }}
    if (!resp.ok) {{ document.getElementById('builds-summary').textContent = 'Error'; return; }}
    const data = await resp.json();
    totalJobs = data.total;
    const jobs = data.jobs || [];

    document.getElementById('builds-summary').textContent =
      totalJobs + ' job(s)' + (status ? ' (' + status + ')' : '') +
      (builderId ? ' \u2014 Builder #' + builderId : '');
    document.getElementById('filters-bar').style.display = '';
    document.getElementById('pagination').style.display = totalJobs > PAGE_SIZE ? '' : 'none';
    document.getElementById('page-info').textContent = totalJobs === 0
      ? 'No results'
      : 'Showing ' + (currentOffset + 1) + '-' + Math.min(currentOffset + PAGE_SIZE, totalJobs) + ' of ' + totalJobs;
    document.getElementById('btn-prev').disabled = currentOffset === 0;
    document.getElementById('btn-next').disabled = currentOffset + PAGE_SIZE >= totalJobs;

    // DAG section
    const dagSection = document.getElementById('dag-section');
    if (dagId && jobs.length > 0) {{
      dagSection.style.display = '';
      document.getElementById('dag-id-label').textContent = dagId;
      const counts = {{}};
      jobs.forEach(j => {{ counts[j.status] = (counts[j.status] || 0) + 1; }});
      const parts = Object.entries(counts).map(([s, c]) =>
        '<span class="tag ' + statusCls(s) + ' is-rounded mr-1">' + s + ': ' + c + '</span>'
      ).join('');
      const done = jobs.filter(j => j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled').length;
      const pct = totalJobs > 0 ? Math.round(100 * done / totalJobs) : 0;
      document.getElementById('dag-progress').innerHTML =
        '<progress class="progress is-info mb-2" value="' + done + '" max="' + totalJobs + '">' + pct + '%</progress>' +
        '<p class="is-size-7">' + parts + '</p>';
    }} else {{
      dagSection.style.display = 'none';
    }}

    currentJobs = jobs;
    renderJobs();

    // Without the live socket, poll while active (running/dispatched/pending)
    // builds exist; with it, changes arrive as they happen.
    const hasActive = jobs.some(j => ['running', 'dispatched', 'pending'].includes(j.status));
    if (hasActive && !liveUp && !_refreshTimer) {{
      _refreshTimer = setInterval(() => loadJobs(), 15000);
    }} else if ((!hasActive || liveUp) && _refreshTimer) {{
      clearInterval(_refreshTimer);
      _refreshTimer = null;
    }}
  }} catch (e) {{
    document.getElementById('builds-summary').textContent = 'Error: ' + e.message;
  }}
}}

async function init() {{
  // Pre-fill dag_id from query string
  const params = new URLSearchParams(location.search);
  const dagParam = params.get('dag_id');
  if (dagParam) document.getElementById('filter-dag').value = dagParam;
  const statusParam = params.get('status');
  if (statusParam) document.getElementById('filter-status').value = statusParam;
  const recipeParam = params.get('recipe_name');
  if (recipeParam) document.getElementById('filter-recipe').value = recipeParam;
  const platformParam = params.get('platform');
  if (platformParam) document.getElementById('filter-platform').value = platformParam;
  const archParam = params.get('arch');
  if (archParam) document.getElementById('filter-arch').value = archParam;
  const configParam = params.get('config');
  if (configParam) document.getElementById('filter-config').value = configParam;
  const linkParam = params.get('link');
  if (linkParam) document.getElementById('filter-link').value = linkParam;
  const builderParam = params.get('builder_id');
  if (builderParam) document.getElementById('filter-builder').value = builderParam;
  await loadJobs();
  openLive(['builds'], {{ onMessage: onBuildsLive, onState: onBuildsLiveState }});
}}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


# The build detail page's live view: the job's log streams over /v1/live/ws
# (from the byte it got to, across reconnects) and its status and timings
# update as the job moves.  Without the socket it falls back to the REST log
# and a slow poll.
_BUILD_DETAIL_LIVE_JS = r"""
const TERMINAL = ['succeeded', 'failed', 'cancelled', 'timed_out'];
let currentJob = null;
let logNext = 0;          // the next log byte the page expects
let logDone = false;
let gotLiveLog = false;
let fallbackTimer = null;

function appendLog(text) {
  if (!text) return;
  const out = document.getElementById('log-output');
  const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 40;
  out.appendChild(document.createTextNode(text));
  if (atBottom) out.scrollTop = out.scrollHeight;
}
function setLogLive(on) {
  document.getElementById('log-live').style.display = on ? '' : 'none';
}
function onDetailLive(msg) {
  if (msg.type === 'log' && msg.job === JOB_ID) {
    if (msg.offset < logNext) return;  // already shown (a reconnect re-sends)
    if (!gotLiveLog) { document.getElementById('log-output').textContent = ''; gotLiveLog = true; }
    appendLog(msg.data);
    logNext = msg.next;
    setLogLive(!TERMINAL.includes((currentJob || {}).status));
  } else if (msg.type === 'log_reset' && msg.job === JOB_ID) {
    document.getElementById('log-output').textContent = '';
    logNext = 0;
  } else if (msg.type === 'log_end' && msg.job === JOB_ID) {
    logDone = true;
    setLogLive(false);
    if (msg.status === 'not_found' || !gotLiveLog) {
      if (!gotLiveLog) document.getElementById('log-output').textContent = '(no log available)';
    } else {
      document.getElementById('log-download').style.display = '';
    }
  } else if (msg.type === 'builds') {
    const j = msg.jobs.find(x => x.id === JOB_ID);
    if (j) {
      j.depends_on = (currentJob && currentJob.depends_on) || j.depends_on;
      currentJob = j;
      populateMeta(j);
    }
  } else if (msg.type === 'resync') {
    loadMeta();
  }
}
function onDetailState(state) {
  clearInterval(fallbackTimer);
  fallbackTimer = null;
  if (state === 'offline' && !logDone) {
    // No socket (yet, or at all): show what there is and keep the header fresh.
    if (!gotLiveLog) loadStaticLog();
    fallbackTimer = setInterval(async () => {
      const j = await loadMeta();
      if (j) currentJob = j;
      if (j && TERMINAL.includes(j.status)) { clearInterval(fallbackTimer); loadStaticLog(); }
    }, 10000);
  }
}
function startLive() {
  openLive(['builds'], {
    onMessage: onDetailLive,
    onState: onDetailState,
    hello: () => (logDone ? {} : { log: { job: JOB_ID, offset: logNext } }),
  });
  // Elapsed time ticks while the job runs.
  setInterval(() => {
    if (currentJob && currentJob.status === 'running' && currentJob.started_at) {
      document.getElementById('meta-duration').textContent =
        fmtDuration(currentJob.started_at, new Date().toISOString());
    }
  }, 1000);
}
"""


def build_detail_html(job_id: int) -> str:
    """Return HTML for a single Build Job detail page with live log streaming."""
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"Build #{job_id} &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li><a href="/builds" class="has-text-grey-light">Build Jobs</a></li>
        <li class="is-active"><a href="#" class="has-text-light">#{job_id}</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="level">
      <div class="level-left">
        <h1 class="title has-text-white" id="build-title">Build #{job_id}</h1>
      </div>
      <div class="level-right">
        <button id="cancel-btn" class="button is-danger is-outlined mr-3" style="display:none"
                onclick="cancelBuild()">
          <span class="icon"><i class="fas fa-times-circle"></i></span><span>Cancel</span>
        </button>
        <span id="build-status" class="tag is-light is-medium is-rounded">Loading&hellip;</span>
      </div>
    </div>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>This server requires a token to read. Enter your API token.</p>
      <div class="field has-addons mt-2">
        <div class="control is-expanded">
          <input class="input is-dark" id="token-input" type="password" placeholder="cvctok_&hellip;">
        </div>
        <div class="control">
          <button class="button is-link" onclick="saveToken()">Save</button>
        </div>
      </div>
    </div>

    <!-- Metadata -->
    <div class="columns" id="build-meta" style="display:none">
      <div class="column is-half">
        <div class="box has-background-black-ter">
          <div class="table-container">
          <table class="table is-dark is-fullwidth is-narrow">
            <tbody>
              <tr><th class="has-text-grey-lighter" style="width:35%">Recipe</th><td id="meta-recipe"></td></tr>
              <tr><th class="has-text-grey-lighter">Platform / Arch</th><td id="meta-platform"></td></tr>
              <tr><th class="has-text-grey-lighter">Config / Link</th><td id="meta-config"></td></tr>
              <tr><th class="has-text-grey-lighter">Builder</th><td id="meta-builder"></td></tr>
              <tr><th class="has-text-grey-lighter">DAG</th><td id="meta-dag"></td></tr>
            </tbody>
          </table>
          </div>
        </div>
      </div>
      <div class="column is-half">
        <div class="box has-background-black-ter">
          <div class="table-container">
          <table class="table is-dark is-fullwidth is-narrow">
            <tbody>
              <tr><th class="has-text-grey-lighter" style="width:35%">Submitted</th><td id="meta-submitted"></td></tr>
              <tr><th class="has-text-grey-lighter">Started</th><td id="meta-started"></td></tr>
              <tr><th class="has-text-grey-lighter">Finished</th><td id="meta-finished"></td></tr>
              <tr><th class="has-text-grey-lighter">Duration</th><td id="meta-duration"></td></tr>
              <tr><th class="has-text-grey-lighter">Submitted by</th><td id="meta-submitter"></td></tr>
            </tbody>
          </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Error message -->
    <div id="error-box" class="notification is-danger is-light" style="display:none">
      <pre id="error-text" style="white-space:pre-wrap;word-break:break-word;color:#333"></pre>
    </div>

    <!-- Dependencies (if DAG) -->
    <div id="deps-section" class="box has-background-black-ter mb-4" style="display:none">
      <h3 class="title is-6 has-text-white">
        <span class="icon mr-1"><i class="fas fa-project-diagram"></i></span> Dependencies
      </h3>
      <div id="deps-list"></div>
    </div>

    <!-- Log output -->
    <div class="box has-background-black-ter" id="log-section">
      <div class="level mb-2">
        <div class="level-left">
          <h3 class="title is-6 has-text-white mb-0">
            <span class="icon mr-1"><i class="fas fa-terminal"></i></span> Build Log
            <span id="log-live" class="tag is-info is-rounded ml-2" style="display:none">
              <i class="fas fa-circle mr-1"></i>Live
            </span>
          </h3>
        </div>
        <div class="level-right">
          <a id="log-download" class="button is-small is-dark" href="/v1/builds/{job_id}/log"
             target="_blank" style="display:none">
            <span class="icon"><i class="fas fa-download"></i></span><span>Download</span>
          </a>
        </div>
      </div>
      <pre id="log-output" style="background:#1a1a2e;color:#eee;padding:1rem;border-radius:6px;max-height:70vh;overflow-y:auto;font-size:0.85rem;line-height:1.5;white-space:pre-wrap;word-break:break-word"></pre>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}
{_LIVE_JS}

const JOB_ID = {job_id};
{_BUILD_DETAIL_LIVE_JS}

function saveToken() {{
  const t = document.getElementById('token-input').value.trim();
  if (t) {{ localStorage.setItem('cvcpkg_token', t); location.reload(); }}
}}

async function cancelBuild() {{
  if (!confirm('Cancel build #' + JOB_ID + '?')) return;
  const btn = document.getElementById('cancel-btn');
  btn.disabled = true;
  btn.classList.add('is-loading');
  try {{
    const resp = await fetch('/v1/builds/' + JOB_ID + '/cancel',
      {{ method: 'POST', headers: authHeaders() }});
    if (resp.ok) {{
      await loadMeta();
    }} else {{
      alert('Cancel failed: ' + resp.status);
    }}
  }} catch (e) {{
    alert('Cancel failed: ' + e.message);
  }} finally {{
    btn.disabled = false;
    btn.classList.remove('is-loading');
  }}
}}

function populateMeta(j) {{
  document.getElementById('build-meta').style.display = '';
  document.getElementById('build-status').className = 'tag ' + statusCls(j.status) + ' is-medium is-rounded';
  document.getElementById('build-status').textContent = j.status;
  document.getElementById('build-title').innerHTML =
    'Build #' + j.id + ' &mdash; <a href="' + (j.org_slug ? '/package/' + encodeURIComponent(j.org_slug) + '/' + encodeURIComponent(j.recipe_name) : '/package/' + encodeURIComponent(j.recipe_name)) +
    '" class="has-text-link">' + (j.org_slug ? esc(j.org_slug) + '/' : '') + esc(j.recipe_name) + '</a>';
  document.getElementById('meta-recipe').innerHTML =
    '<a href="' + (j.org_slug ? '/package/' + encodeURIComponent(j.org_slug) + '/' + encodeURIComponent(j.recipe_name) : '/package/' + encodeURIComponent(j.recipe_name)) +
    '" class="has-text-link">' + (j.org_slug ? esc(j.org_slug) + '/' : '') + esc(j.recipe_name) + '</a>';
  document.getElementById('meta-platform').innerHTML = platformTag(j.platform) + ' / ' + esc(j.arch);
  document.getElementById('meta-config').textContent = j.config + ' / ' + j.link;
  document.getElementById('meta-builder').textContent = j.builder_id ? 'Builder #' + j.builder_id : '\\u2014';
  document.getElementById('meta-dag').innerHTML = j.dag_id
    ? '<a href="/builds?dag_id=' + encodeURIComponent(j.dag_id) + '" class="has-text-link">' + esc(j.dag_id) + '</a>'
    : '\\u2014';
  // Show cancel button for cancellable statuses
  const cancellable = ['pending', 'dispatched', 'running'];
  document.getElementById('cancel-btn').style.display =
    cancellable.includes(j.status) ? '' : 'none';

  document.getElementById('meta-submitted').textContent = j.submitted_at ? fmtDate(j.submitted_at) : '\\u2014';
  document.getElementById('meta-started').textContent = j.started_at ? fmtDate(j.started_at) : '\\u2014';
  document.getElementById('meta-finished').textContent = j.finished_at ? fmtDate(j.finished_at) : '\\u2014';
  document.getElementById('meta-duration').textContent = fmtDuration(j.started_at, j.finished_at);
  document.getElementById('meta-submitter').textContent = j.submitted_by || '\\u2014';

  if (j.error_message) {{
    document.getElementById('error-box').style.display = '';
    document.getElementById('error-text').textContent = j.error_message;
  }}

  if (j.depends_on && j.depends_on.length > 0) {{
    document.getElementById('deps-section').style.display = '';
    document.getElementById('deps-list').innerHTML = j.depends_on.map(depId =>
      '<a href="/build/' + depId + '" class="tag is-dark is-rounded mr-1 mb-1">#' + depId + '</a>'
    ).join('');
  }}
}}

async function loadStaticLog() {{
  const output = document.getElementById('log-output');
  const dlBtn = document.getElementById('log-download');
  try {{
    const resp = await fetch('/v1/builds/' + JOB_ID + '/log',
      {{ headers: authHeaders() }});
    if (resp.ok) {{
      output.textContent = await resp.text();
      dlBtn.style.display = '';
    }} else {{
      output.textContent = '(no log available)';
    }}
  }} catch (_) {{
    output.textContent = '(failed to load log)';
  }}
}}

async function loadMeta() {{
  try {{
    const resp = await fetch('/v1/builds/' + JOB_ID, {{ headers: authHeaders() }});
    if (!resp.ok) return null;
    const j = await resp.json();
    populateMeta(j);
    return j;
  }} catch (_) {{ return null; }}
}}

async function init() {{
  const resp = await fetch('/v1/builds/' + JOB_ID, {{ headers: authHeaders() }});
  if (resp.status === 401 || resp.status === 403) {{
    document.getElementById('auth-prompt').style.display = '';
    document.getElementById('build-status').textContent = 'Auth required';
    return;
  }}
  if (!resp.ok) {{
    document.getElementById('build-status').textContent = 'Not found';
    return;
  }}
  const job = await resp.json();
  currentJob = job;
  populateMeta(job);
  startLive();
}}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


_ACTIVITY_JS = r"""
const KINDS = {
  publish: { label: 'Published', cls: 'is-success', icon: 'fa-upload' },
  yank:    { label: 'Yanked',    cls: 'is-warning', icon: 'fa-ban' },
  unyank:  { label: 'Unyanked',  cls: 'is-info',    icon: 'fa-undo' },
  delete:  { label: 'Deleted',   cls: 'is-danger is-light', icon: 'fa-trash' },
  nuke:    { label: 'Nuked',     cls: 'is-danger',  icon: 'fa-radiation' },
};
const TEXT_FILTERS = ['name', 'platform', 'arch', 'org', 'actor'];

const st = {
  kinds: new Set(), name: '', platform: '', arch: '', org: '', actor: '',
  offset: 0, limit: 50, maxId: null, total: 0, rows: [], ids: new Set(),
  paused: false, pending: 0, live: false, pollTimer: null, loading: false, seq: 0,
};

// ── URL <-> filter state ─────────────────────────────────────
function readUrl() {
  const q = new URLSearchParams(location.search);
  st.kinds = new Set(q.getAll('kind').filter(k => KINDS[k]));
  for (const f of TEXT_FILTERS) st[f] = q.get(f) || '';
  const lim = parseInt(q.get('limit') || '50', 10);
  st.limit = [25, 50, 100, 200].includes(lim) ? lim : 50;
  st.offset = Math.max(0, parseInt(q.get('offset') || '0', 10) || 0);
}
function writeUrl() {
  const q = new URLSearchParams();
  for (const k of st.kinds) q.append('kind', k);
  for (const f of TEXT_FILTERS) if (st[f]) q.set(f, st[f]);
  if (st.limit !== 50) q.set('limit', st.limit);
  if (st.offset) q.set('offset', st.offset);
  const qs = q.toString();
  history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
}
function syncControls() {
  document.querySelectorAll('.kind-btn').forEach(b => {
    b.classList.toggle('is-selected', st.kinds.has(b.dataset.kind));
    b.setAttribute('aria-pressed', st.kinds.has(b.dataset.kind) ? 'true' : 'false');
  });
  for (const f of TEXT_FILTERS) document.getElementById('f-' + f).value = st[f];
  document.getElementById('f-limit').value = String(st.limit);
}

// ── Fetching ────────────────────────────────────────────────
function apiUrl() {
  const q = new URLSearchParams();
  for (const k of st.kinds) q.append('kind', k);
  for (const f of ['name', 'platform', 'arch', 'actor']) if (st[f]) q.set(f, st[f]);
  if (st.org) q.set('org', st.org);
  q.set('limit', st.limit);
  q.set('offset', st.offset);
  if (st.maxId !== null) q.set('max_id', st.maxId);
  return '/v1/activity?' + q.toString();
}
async function load(opts) {
  opts = opts || {};
  if (opts.fresh) { st.maxId = null; st.pending = 0; }
  // Filters and pages change faster than responses arrive: only the newest
  // request may draw, or a slow stale one overwrites the page it replaced.
  const seq = ++st.seq;
  st.loading = true;
  try {
    const resp = await fetch(apiUrl(), { headers: authHeaders() });
    if (seq !== st.seq) return;
    if (resp.status === 401) {
      document.getElementById('auth-prompt').style.display = '';
      setSummary('Authentication required');
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      setSummary('Error loading activity: ' + (body.detail || resp.status));
      return;
    }
    const data = await resp.json();
    if (seq !== st.seq) return;
    st.maxId = data.max_id;
    st.total = data.total;
    st.rows = data.events;
    st.ids = new Set(st.rows.map(e => e.id));
    if (st.offset > 0 && st.offset >= st.total && st.total > 0) {
      st.offset = Math.floor((st.total - 1) / st.limit) * st.limit;
      return load();
    }
    render();
  } catch (e) {
    if (seq === st.seq) setSummary('Error: ' + e.message);
  } finally {
    if (seq === st.seq) st.loading = false;
  }
}

// ── Live updates ────────────────────────────────────────────
function matches(e) {
  if (st.kinds.size && !st.kinds.has(e.kind)) return false;
  if (st.name && !e.name.toLowerCase().includes(st.name.toLowerCase())) return false;
  if (st.platform && e.platform !== st.platform) return false;
  if (st.arch && e.arch !== st.arch) return false;
  if (st.org && e.org_slug !== st.org) return false;
  if (st.actor && !(e.actor || '').toLowerCase().includes(st.actor.toLowerCase())) return false;
  return true;
}
function atHead() { return st.offset === 0 && !st.paused; }
function onLive(msg) {
  if (msg.type === 'ready') {
    // Events may have landed between the page load and the socket opening.
    if (st.maxId !== null && msg.latest_id > st.maxId) {
      if (atHead()) load({ fresh: true }); else showPending(true);
    }
    return;
  }
  if (msg.type === 'resync') { if (atHead()) load({ fresh: true }); else showPending(true); return; }
  if (msg.type !== 'activity') return;
  const fresh = msg.events.filter(e => matches(e) && !st.ids.has(e.id) && e.id > (st.maxId || 0));
  if (!fresh.length) return;
  if (!atHead()) { st.pending += fresh.length; showPending(); return; }
  fresh.sort((a, b) => b.id - a.id);
  for (const e of fresh) { st.ids.add(e.id); e._new = true; }
  st.rows = fresh.concat(st.rows).slice(0, st.limit);
  st.total += fresh.length;
  st.maxId = Math.max(st.maxId || 0, fresh[0].id);
  render();
}
function onLiveState(state) {
  st.live = state === 'live';
  setLiveBadge(document.getElementById('live-badge'), state);
  clearInterval(st.pollTimer);
  st.pollTimer = null;
  if (state === 'offline') {
    st.pollTimer = setInterval(() => { if (atHead() && !st.loading) load({ fresh: true }); }, 15000);
  }
}
function showPending(unknownCount) {
  const el = document.getElementById('pending');
  el.style.display = '';
  document.getElementById('pending-text').textContent = unknownCount && !st.pending
    ? 'New events have arrived.'
    : st.pending + ' new event' + (st.pending === 1 ? '' : 's') + ' since this page was loaded.';
}
function showLatest() {
  st.offset = 0;
  st.paused = false;
  syncPause();
  document.getElementById('pending').style.display = 'none';
  writeUrl();
  load({ fresh: true });
}
function togglePause() {
  st.paused = !st.paused;
  syncPause();
  if (!st.paused && st.offset === 0 && st.pending) showLatest();
}
function syncPause() {
  const b = document.getElementById('btn-pause');
  b.querySelector('i').className = 'fas ' + (st.paused ? 'fa-play' : 'fa-pause');
  b.querySelector('.label').textContent = st.paused ? 'Resume' : 'Pause';
  b.title = st.paused ? 'Resume live updates' : 'Hold the list still; new events are counted';
}

// ── Rendering ───────────────────────────────────────────────
function setSummary(text) { document.getElementById('summary').textContent = text; }
function pkgHref(e) {
  return e.org_slug
    ? '/package/' + encodeURIComponent(e.org_slug) + '/' + encodeURIComponent(e.name)
    : '/package/' + encodeURIComponent(e.name);
}
function kindTag(kind) {
  const k = KINDS[kind] || { label: kind, cls: 'is-light', icon: 'fa-circle' };
  return '<span class="tag is-rounded ' + k.cls + '"><i class="fas ' + k.icon + ' mr-1"></i>' +
    esc(k.label) + '</span>';
}
function render() {
  const body = document.getElementById('events-body');
  if (!st.rows.length) {
    const filtered = st.kinds.size || TEXT_FILTERS.some(f => st[f]);
    body.innerHTML = '<tr><td colspan="7" class="has-text-centered has-text-grey py-5">' +
      (filtered ? 'No events match these filters.' : 'No activity yet.') + '</td></tr>';
  } else {
    body.innerHTML = st.rows.map(e => {
      const variant = [e.build_type, e.link].filter(Boolean).join(' / ');
      const actor = e.actor
        ? '<span class="has-text-light">' + esc(e.actor) + '</span>'
        : '<span class="has-text-grey">&mdash;</span>';
      const detail = e.detail
        ? '<br><span class="is-size-7 has-text-grey">' + esc(e.detail) + '</span>' : '';
      const org = e.org_slug
        ? '<a href="/org/' + encodeURIComponent(e.org_slug) + '" class="has-text-link">' + esc(e.org_slug) + '</a>'
        : '<span class="has-text-grey">&mdash;</span>';
      const row = '<tr' + (e._new ? ' class="is-new"' : '') + '>' +
        '<td class="is-size-7"><span class="rel-time" data-ts="' + esc(e.created_at) + '" title="' +
          esc(new Date(e.created_at).toString()) + '">' + esc(fmtRelative(e.created_at)) + '</span></td>' +
        '<td>' + kindTag(e.kind) + '</td>' +
        '<td><a href="' + pkgHref(e) + '" class="has-text-white has-text-weight-semibold">' + esc(e.name) +
          '</a> <code class="is-size-7">' + esc(e.version) + '</code></td>' +
        '<td>' + platformTag(e.platform) + ' <span class="has-text-grey-light is-size-7">' + esc(e.arch) +
          '</span>' + (variant ? '<br><span class="is-size-7 has-text-grey">' + esc(variant) + '</span>' : '') + '</td>' +
        '<td>' + org + '</td>' +
        '<td class="is-size-7">' + actor + detail + '</td>' +
        '<td class="is-size-7 has-text-right">' + (e.kind === 'publish' ? fmtSize(e.size_bytes) : '') + '</td>' +
        '</tr>';
      delete e._new;
      return row;
    }).join('');
  }
  const from = st.total ? st.offset + 1 : 0;
  const to = Math.min(st.offset + st.rows.length, st.total);
  setSummary(st.total.toLocaleString() + ' event' + (st.total === 1 ? '' : 's') +
    (st.kinds.size || TEXT_FILTERS.some(f => st[f]) ? ' match these filters' : ''));
  document.getElementById('page-info').textContent =
    st.total ? 'Showing ' + from.toLocaleString() + '–' + to.toLocaleString() + ' of ' + st.total.toLocaleString() : '';
  document.getElementById('btn-prev').disabled = st.offset === 0;
  document.getElementById('btn-next').disabled = st.offset + st.limit >= st.total;
  renderPages();
}
function renderPages() {
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const cur = Math.floor(st.offset / st.limit) + 1;
  const want = new Set([1, pages, cur - 1, cur, cur + 1].filter(p => p >= 1 && p <= pages));
  const list = [...want].sort((a, b) => a - b);
  let html = '', prev = 0;
  for (const p of list) {
    if (p - prev > 1) html += '<li><span class="pagination-ellipsis has-text-grey">&hellip;</span></li>';
    html += '<li><a class="pagination-link' + (p === cur ? ' is-current' : '') + '" aria-label="Page ' + p +
      '"' + (p === cur ? ' aria-current="page"' : '') + ' onclick="goPage(' + p + ')">' + p + '</a></li>';
    prev = p;
  }
  document.getElementById('page-list').innerHTML = pages > 1 ? html : '';
}
function goPage(p) {
  st.offset = (p - 1) * st.limit;
  writeUrl();
  if (st.offset === 0) { document.getElementById('pending').style.display = 'none'; load({ fresh: true }); }
  else load();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function prevPage() { if (st.offset > 0) goPage(Math.floor(st.offset / st.limit)); }
function nextPage() { if (st.offset + st.limit < st.total) goPage(Math.floor(st.offset / st.limit) + 2); }
function refreshTimes() {
  document.querySelectorAll('.rel-time').forEach(el => { el.textContent = fmtRelative(el.dataset.ts); });
}

// ── Filter controls ─────────────────────────────────────────
function applyFilters() {
  st.offset = 0;
  writeUrl();
  document.getElementById('pending').style.display = 'none';
  load({ fresh: true });
}
let _debounce = null;
function onTextFilter(f, value) {
  st[f] = value.trim();
  clearTimeout(_debounce);
  _debounce = setTimeout(applyFilters, 300);
}
function toggleKind(k) {
  if (st.kinds.has(k)) st.kinds.delete(k); else st.kinds.add(k);
  syncControls();
  applyFilters();
}
function clearFilters() {
  st.kinds.clear();
  for (const f of TEXT_FILTERS) st[f] = '';
  syncControls();
  applyFilters();
}
function setLimit(v) { st.limit = parseInt(v, 10) || 50; applyFilters(); }
function saveToken() {
  const t = document.getElementById('token-input').value.trim();
  if (t) { localStorage.setItem('cvcpkg_token', t); location.reload(); }
}

async function init() {
  readUrl();
  syncControls();
  syncPause();
  await load({ fresh: true });
  openLive(['activity'], { onMessage: onLive, onState: onLiveState });
  setInterval(refreshTimes, 30000);
}
document.addEventListener('DOMContentLoaded', init);
"""

_ACTIVITY_PLATFORMS = (
    ("linux", "Linux"),
    ("macos", "macOS"),
    ("windows", "Windows"),
    ("freebsd", "FreeBSD"),
    ("netbsd", "NetBSD"),
    ("openbsd", "OpenBSD"),
    ("haiku", "Haiku"),
    ("wasm", "WASM"),
    ("wasm-mt", "WASM (threads)"),
    ("wasi", "WASI"),
    ("cosmo", "Cosmo"),
    ("any", "Any"),
)
_ACTIVITY_ARCHES = ("x86_64", "aarch64", "arm64", "riscv64", "wasm32", "fat", "noarch")


def activity_html() -> str:
    """The activity page: recent publishes, yanks and removals, updated live."""
    kind_buttons = "".join(
        f'<button class="button is-small is-dark kind-btn mr-1 mb-1" data-kind="{k}" '
        f'onclick="toggleKind(\'{k}\')" aria-pressed="false">'
        f'<span class="icon"><i class="fas {icon}"></i></span><span>{label}</span></button>'
        for k, label, icon in (
            ("publish", "Published", "fa-upload"),
            ("yank", "Yanked", "fa-ban"),
            ("unyank", "Unyanked", "fa-undo"),
            ("delete", "Deleted", "fa-trash"),
            ("nuke", "Nuked", "fa-radiation"),
        )
    )
    platform_opts = "".join(
        f'<option value="{v}">{label}</option>' for v, label in _ACTIVITY_PLATFORMS
    )
    arch_opts = "".join(f'<option value="{a}">{a}</option>' for a in _ACTIVITY_ARCHES)
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Activity &mdash; cvcpkg", canonical="/activity")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li class="is-active"><a href="#" class="has-text-light">Activity</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <div class="level mb-2">
      <div class="level-left">
        <h1 class="title has-text-white mb-0">
          <span class="icon mr-2"><i class="fas fa-bolt"></i></span> Activity
        </h1>
      </div>
      <div class="level-right">
        <span id="live-badge" class="live-badge is-connecting is-size-7 has-text-grey-light mr-3">
          <span class="live-dot"></span><span class="live-label">Connecting&hellip;</span>
        </span>
        <button class="button is-small is-dark" id="btn-pause" onclick="togglePause()">
          <span class="icon"><i class="fas fa-pause"></i></span><span class="label">Pause</span>
        </button>
      </div>
    </div>
    <p class="subtitle is-6 has-text-grey-light mt-1">
      Publishes, yanks and removals across the registry, one row per bundle variant.
      <span id="summary" class="has-text-grey"></span>
    </p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>This server requires a token to read. Enter your API token.</p>
      <div class="field has-addons mt-2">
        <div class="control is-expanded">
          <input class="input is-dark" id="token-input" type="password" placeholder="cvctok_&hellip;">
        </div>
        <div class="control">
          <button class="button is-link" onclick="saveToken()">Save</button>
        </div>
      </div>
    </div>

    <div class="box has-background-black-ter mb-4" id="filters">
      <div class="mb-2" role="group" aria-label="Actions">{kind_buttons}</div>
      <div class="columns is-mobile is-multiline is-variable is-2 mb-0">
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-name">Package</label>
          <input class="input is-small is-dark" id="f-name" placeholder="name contains&hellip;"
                 oninput="onTextFilter('name', this.value)">
        </div>
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-platform">Platform</label>
          <div class="select is-small is-dark">
            <select id="f-platform" onchange="onTextFilter('platform', this.value)">
              <option value="">All</option>{platform_opts}
            </select>
          </div>
        </div>
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-arch">Arch</label>
          <div class="select is-small is-dark">
            <select id="f-arch" onchange="onTextFilter('arch', this.value)">
              <option value="">All</option>{arch_opts}
            </select>
          </div>
        </div>
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-org">Org</label>
          <input class="input is-small is-dark" id="f-org" placeholder="org slug" style="width:9em"
                 oninput="onTextFilter('org', this.value)">
        </div>
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-actor">By</label>
          <input class="input is-small is-dark" id="f-actor" placeholder="who contains&hellip;" style="width:10em"
                 oninput="onTextFilter('actor', this.value)">
        </div>
        <div class="column is-narrow">
          <label class="label has-text-grey-lighter is-small" for="f-limit">Per page</label>
          <div class="select is-small is-dark">
            <select id="f-limit" onchange="setLimit(this.value)">
              <option value="25">25</option><option value="50">50</option>
              <option value="100">100</option><option value="200">200</option>
            </select>
          </div>
        </div>
        <div class="column is-narrow" style="align-self:flex-end">
          <button class="button is-small is-dark" onclick="clearFilters()">
            <span class="icon"><i class="fas fa-times"></i></span><span>Clear</span>
          </button>
        </div>
      </div>
    </div>

    <div class="notification is-link is-light py-2 px-4" id="pending" style="display:none">
      <span id="pending-text"></span>
      <a class="ml-2 has-text-weight-semibold" onclick="showLatest()">Show the latest</a>
    </div>

    <div class="table-container">
      <table class="table is-fullwidth is-hoverable is-dark">
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Package</th>
            <th>Variant</th>
            <th>Org</th>
            <th>By</th>
            <th class="has-text-right">Size</th>
          </tr>
        </thead>
        <tbody id="events-body">
          <tr><td colspan="7" class="has-text-centered has-text-grey py-5">Loading&hellip;</td></tr>
        </tbody>
      </table>
    </div>

    <nav class="pagination is-small is-centered" role="navigation" aria-label="pagination">
      <button class="pagination-previous button is-dark is-small" id="btn-prev" onclick="prevPage()">
        <span class="icon"><i class="fas fa-chevron-left"></i></span><span>Newer</span>
      </button>
      <button class="pagination-next button is-dark is-small" id="btn-next" onclick="nextPage()">
        <span>Older</span><span class="icon"><i class="fas fa-chevron-right"></i></span>
      </button>
      <ul class="pagination-list" id="page-list"></ul>
    </nav>
    <p class="has-text-centered is-size-7 has-text-grey" id="page-info"></p>
  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}
{_LIVE_JS}
{_ACTIVITY_JS}
</script>
</body>
</html>"""


def recipes_html() -> str:
    """Return HTML for the Recipes management page."""
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Recipes &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Home</a></li>
        <li class="is-active"><a href="#" class="has-text-light">Recipes</a></li>
      </ul>
    </nav>
  </div>
</section>

<section class="section pt-2 has-background-black-bis">
  <div class="container">
    <h1 class="title has-text-white">
      <span class="icon mr-2"><i class="fas fa-scroll"></i></span> Server Recipes
    </h1>
    <p class="subtitle has-text-grey-light" id="recipe-summary">Loading&hellip;</p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>This server requires a token to read. Enter your API token.</p>
      <div class="field has-addons mt-2">
        <div class="control is-expanded">
          <input class="input is-dark" id="token-input" type="password" placeholder="cvctok_&hellip;">
        </div>
        <div class="control">
          <button class="button is-link" onclick="saveToken()">Save</button>
        </div>
      </div>
    </div>

    <div class="field mb-4" id="search-box" style="display:none">
      <div class="control has-icons-left">
        <input class="input is-dark" id="recipe-search" type="text" placeholder="Search recipes&hellip;"
               oninput="filterRecipes()">
        <span class="icon is-left"><i class="fas fa-search"></i></span>
      </div>
    </div>

    <div class="table-container">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th>Name</th>
            <th>Version</th>
            <th>Hash</th>
            <th>Size</th>
            <th>Uploaded By</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody id="recipes-body"></tbody>
      </table>
    </div>
  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}

let allRecipes = [];

function saveToken() {{
  const t = document.getElementById('token-input').value.trim();
  if (t) {{ localStorage.setItem('cvcpkg_token', t); location.reload(); }}
}}

function filterRecipes() {{
  const q = document.getElementById('recipe-search').value.toLowerCase();
  const filtered = allRecipes.filter(r => r.name.toLowerCase().includes(q));
  renderRecipes(filtered);
}}

function renderRecipes(recipes) {{
  const tbody = document.getElementById('recipes-body');
  if (recipes.length === 0) {{
    tbody.innerHTML = '<tr><td colspan="6" class="has-text-centered has-text-grey">No recipes found</td></tr>';
    return;
  }}
  tbody.innerHTML = recipes.map(r => {{
    const hash = (r.recipe_hash || '').substring(0, 12);
    return '<tr>' +
      '<td><span class="icon is-small mr-1 has-text-info"><i class="fas fa-scroll"></i></span>' +
      '<a href="/package/' + encodeURIComponent(r.name) + '" class="has-text-link">' + esc(r.name) + '</a></td>' +
      '<td class="has-text-grey-light">' + esc(r.version || '\\u2014') + '</td>' +
      '<td><code class="has-text-grey">' + esc(hash || '\\u2014') + '</code></td>' +
      '<td class="has-text-grey-light">' + fmtSize(r.bundle_size || 0) + '</td>' +
      '<td class="has-text-grey-light">' + esc(r.uploaded_by || '\\u2014') + '</td>' +
      '<td class="has-text-grey-light is-size-7">' + fmtDate(r.updated_at || r.created_at) + '</td>' +
      '</tr>';
  }}).join('');
}}

async function init() {{
  try {{
    // Public read (a token only adds the viewer's private orgs' recipes).
    // The server pages at 1000; a registry holds a few thousand recipes.
    allRecipes = [];
    for (let offset = 0; ; ) {{
      const resp = await fetch('/v1/recipes?limit=1000&offset=' + offset, {{ headers: authHeaders() }});
      if (resp.status === 401 || resp.status === 403) {{
        document.getElementById('auth-prompt').style.display = '';
        document.getElementById('recipe-summary').textContent = 'Authentication required';
        return;
      }}
      if (!resp.ok) {{
        document.getElementById('recipe-summary').textContent = 'Error loading recipes';
        return;
      }}
      const data = await resp.json();
      const page = data.recipes || [];
      allRecipes = allRecipes.concat(page);
      offset += page.length;
      if (page.length === 0 || offset >= (data.total || 0)) break;
    }}
    document.getElementById('recipe-summary').textContent = allRecipes.length + ' recipe(s) on server';
    document.getElementById('search-box').style.display = '';
    renderRecipes(allRecipes);
  }} catch (e) {{
    document.getElementById('recipe-summary').textContent = 'Error: ' + e.message;
  }}
}}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


def guide_html() -> str:
    """Return the complete HTML for the Getting Started guide page."""
    repo = _html.escape(_GITHUB_REPO)
    return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("Getting Started &mdash; cvcpkg")}
<style>{_GUIDE_CSS}</style>
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<section class="section">
  <div class="container" style="max-width: 860px;">

    <h1 class="title is-2 has-text-white mb-2">
      <span class="icon mr-2"><i class="fas fa-rocket"></i></span>
      Getting Started with cvcpkg
    </h1>
    <p class="subtitle is-5 has-text-grey-lighter mb-5">
      Install pre-built C/C++ libraries in seconds. No compilation required.
    </p>

    <!-- Table of Contents -->
    <div class="box has-background-black-ter mb-6">
      <p class="has-text-weight-bold has-text-grey-light mb-3">
        <span class="icon"><i class="fas fa-list-ul"></i></span> Contents
      </p>
      <ol class="toc ml-4">
        <li><a href="#install">Installation</a></li>
        <li><a href="#quick-start">Quick Start</a></li>
        <li><a href="#requirements">Your Project's Recipe Set</a></li>
        <li><a href="#commands">CLI Reference &mdash; every command, with examples</a></li>
        <li><a href="#cmake">CMake Integration</a></li>
        <li><a href="#recipes">Creating Recipes</a></li>
        <li><a href="#accounts">Accounts &amp; Signing In</a></li>
        <li><a href="#publishing">Publishing Builds</a></li>
        <li><a href="#orgs">Organizations</a></li>
        <li><a href="#server">Self-Hosting</a></li>
        <li><a href="#remote-builders">Remote Builders</a></li>
        <li><a href="#server-config">Server Configuration</a></li>
        <li><a href="#api">REST API</a></li>
      </ol>
    </div>

    <!-- Installation -->
    <div id="install" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-download"></i></span>
        Installation
      </h2>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Linux, macOS, or BSD — install the standalone binary (no Python required):
        </p>
        <div class="guide-code"><pre><code>curl -fsSL {_SITE_URL}/install.sh | sh</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Install to a custom location:</p>
        <div class="guide-code"><pre><code>curl -fsSL {_SITE_URL}/install.sh | sh -s -- --install-dir /opt/cvcpkg/bin</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Windows (PowerShell):</p>
        <div class="guide-code"><pre><code>irm {_SITE_URL}/install.ps1 | iex</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Windows — install to a custom location:</p>
        <div class="guide-code"><pre><code>&amp; ([scriptblock]::Create((irm {_SITE_URL}/install.ps1))) -InstallDir C:\\tools\\cvcpkg</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Or install from PyPI:</p>
        <div class="guide-code"><pre><code>pip install cvcpkg</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Or install from the repository:</p>
        <div class="guide-code"><pre><code>git clone https://github.com/{repo}.git
cd cvcpkg
pip install .</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Verify the installation:</p>
        <div class="guide-code"><pre><code>cvcpkg --version</code></pre></div>
      </div>
    </div>

    <!-- Quick Start -->
    <div id="quick-start" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-bolt"></i></span>
        Quick Start
      </h2>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Install a package into a local prefix:
        </p>
        <div class="guide-code"><pre><code># Install zlib into ./deps
cvcpkg install zlib --prefix ./deps

# Install multiple packages
cvcpkg install boost hdf5 fftw3 --prefix ./deps</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Choose build configuration and link mode:
        </p>
        <div class="guide-code"><pre><code># Release + shared (default)
cvcpkg install qt6 --prefix ./deps

# Debug + static
cvcpkg install qt6 --prefix ./deps --config debug --link static</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          List installed packages:
        </p>
        <div class="guide-code"><pre><code>cvcpkg list --prefix ./deps</code></pre></div>
      </div>
    </div>

    <!-- Recipe-first workflow -->
    <div id="requirements" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-file-alt"></i></span>
        Your Project's Recipe Set
      </h2>
      <p class="has-text-grey-lighter mb-4">
        A project describes its dependencies with a <strong>recipe</strong> that
        lives beside its source, in a <code>recipes/</code> directory the project
        vendors and version-controls. cvcpkg picks that directory up
        automatically when run from the project root &mdash; no flag, no separate
        manifest to keep in sync with the build.
      </p>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Already have a project that builds? Generate the recipe from it:
        </p>
        <div class="guide-code"><pre><code># Detects CMake, autotools, Meson, a plain Makefile, or Python packaging
cvcpkg generate .

# Or scaffold a blank one to fill in by hand
cvcpkg init myapp</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Declare what you depend on in <code>recipes/myapp/recipe.yaml</code>:
        </p>
        <div class="guide-code"><pre><code>depends:
  build:
    - name: boost
      version: "&gt;=1.86"
    - name: hdf5
    - name: qt6
    - name: vtk
  host_tools:
    - cmake
    - ninja</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Install that dependency closure as <strong>prebuilt</strong> bundles,
          then build only your own code against them:
        </p>
        <div class="guide-code"><pre><code>cvcpkg install-deps myapp --prefix ./deps
cvcpkg build myapp --prefix ./deps</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Lock versions for reproducible builds:
        </p>
        <div class="guide-code"><pre><code>cvcpkg lock     # creates cvc-lock.yaml
cvcpkg sync     # installs exactly what's in the lockfile
cvcpkg verify   # check the prefix still matches the lockfile</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-info-circle has-text-warning"></i></span>
          <strong class="has-text-white">Migrating from a requirements file?</strong>
          <code>cvcpkg install --from &lt;file&gt;</code> and <code>cvcpkg world</code>
          still read a components list, and keep working through v2.0. New
          projects should carry a recipe instead: it is the same information in
          the file that already describes how the project builds, so the two
          cannot drift apart.
        </p>
      </div>
    </div>

    <!-- CLI Commands -->
    <div id="commands" class="guide-section">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-terminal"></i></span>
        CLI Reference
      </h2>
      <p class="has-text-grey-lighter mb-4">
        Every top-level command, grouped the same way <code>cvcpkg --help</code>
        groups them. Any command takes <code>--help</code> for its full option
        list. <code>CVCPKG_SERVER_URL</code> and <code>CVCPKG_TOKEN</code> save
        repeating <code>--server</code>/<code>--token</code>.
      </p>

      <h3 class="title is-5 has-text-white mt-5">Find and install packages</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>search</code></td><td>Search the catalog by name, tag, or description</td>
                <td><code>cvcpkg search hdf5 --tag io</code></td></tr>
            <tr><td><code>info</code></td><td>Show a component's versions, platforms, and deps</td>
                <td><code>cvcpkg info boost</code></td></tr>
            <tr><td><code>install</code></td><td>Install prebuilt bundles into a prefix</td>
                <td><code>cvcpkg install boost hdf5 --prefix ./deps</code></td></tr>
            <tr><td><code>install-deps</code></td><td>Install a recipe's whole dependency closure</td>
                <td><code>cvcpkg install-deps myapp --prefix ./deps</code></td></tr>
            <tr><td><code>list</code></td><td>List what is installed, or what is available</td>
                <td><code>cvcpkg list --prefix ./deps</code></td></tr>
            <tr><td><code>upgrade</code></td><td>Move installed components to newer catalog versions</td>
                <td><code>cvcpkg upgrade --prefix ./deps</code></td></tr>
            <tr><td><code>download</code></td><td>Fetch archives without extracting them</td>
                <td><code>cvcpkg download qt6 --platform linux --output-dir ./dl</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Reproducible prefixes</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>lock</code></td><td>Write/refresh <code>cvc-lock.yaml</code> pinning exact builds</td>
                <td><code>cvcpkg lock</code></td></tr>
            <tr><td><code>sync</code></td><td>Make the prefix match the lockfile exactly</td>
                <td><code>cvcpkg sync --prefix ./deps</code></td></tr>
            <tr><td><code>verify</code></td><td>Check prefix integrity against the lockfile</td>
                <td><code>cvcpkg verify --prefix ./deps</code></td></tr>
            <tr><td><code>repair</code></td><td>Restore broken or missing bundles in a prefix</td>
                <td><code>cvcpkg repair --prefix ./deps</code></td></tr>
            <tr><td><code>image</code></td><td>Locate and check VM/disk images in a prefix</td>
                <td><code>cvcpkg image ls --prefix ./deps</code></td></tr>
            <tr><td><code>cpkg</code></td><td>Interop with a cpkg (getcpkg.net) project</td>
                <td><code>cvcpkg cpkg deps boost --prefix ./deps</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Recipes and building</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>init</code></td><td>Scaffold a blank recipe from a template</td>
                <td><code>cvcpkg init mylib --build-system meson</code></td></tr>
            <tr><td><code>generate</code></td><td>Derive a recipe from a project that already builds</td>
                <td><code>cvcpkg generate ../mylib --dry-run</code></td></tr>
            <tr><td><code>recipes</code></td><td>List or inspect the recipes cvcpkg can see</td>
                <td><code>cvcpkg recipes --show grpc</code></td></tr>
            <tr><td><code>build</code></td><td>Build named recipes from source into a prefix</td>
                <td><code>cvcpkg build mylib --prefix ./deps</code></td></tr>
            <tr><td><code>build-all</code></td><td>Build every recipe, in dependency order</td>
                <td><code>cvcpkg build-all --prefix ./prefix</code></td></tr>
            <tr><td><code>pack</code></td><td>Build, then archive the result for publishing</td>
                <td><code>cvcpkg pack mylib --prefix ./deps</code></td></tr>
            <tr><td><code>pack-all</code></td><td>Build and archive every recipe</td>
                <td><code>cvcpkg pack-all --prefix ./prefix</code></td></tr>
            <tr><td><code>validate</code></td><td>Check recipes against the JSON schema and each other</td>
                <td><code>cvcpkg validate recipes/mylib</code></td></tr>
            <tr><td><code>world</code></td><td><em>Legacy.</em> Build everything a requirements file names</td>
                <td><code>cvcpkg world --from requirements.yaml</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Remote build fleet</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>builds monitor</code></td>
                <td><strong>Live <code>top</code>-style dashboard</strong> of builders and running jobs</td>
                <td><code>cvcpkg builds monitor</code></td></tr>
            <tr><td><code>builds submit</code></td><td>Queue one remote build job</td>
                <td><code>cvcpkg builds submit --recipe zlib --platform linux</code></td></tr>
            <tr><td><code>builds submit-dag</code></td><td>Queue a whole dependency graph of jobs</td>
                <td><code>cvcpkg builds submit-dag --platform linux,freebsd gsl</code></td></tr>
            <tr><td><code>builds list</code></td><td>List build jobs, filtered by state</td>
                <td><code>cvcpkg builds list --status running</code></td></tr>
            <tr><td><code>builds log</code></td><td>View or follow a job's build log</td>
                <td><code>cvcpkg builds log 4821 -f</code></td></tr>
            <tr><td><code>builds follow-dag</code></td><td>Follow every job in a DAG at once</td>
                <td><code>cvcpkg builds follow-dag dag-91c2</code></td></tr>
            <tr><td><code>builds cancel</code> / <code>pause</code> / <code>resume</code></td>
                <td>Control queued or running jobs (<code>-dag</code> variants act on a whole DAG)</td>
                <td><code>cvcpkg builds cancel-dag dag-91c2</code></td></tr>
            <tr><td><code>builder list</code></td><td>List registered builders and their state</td>
                <td><code>cvcpkg builder list --status online</code></td></tr>
            <tr><td><code>builder status</code></td><td>Show one builder in detail</td>
                <td><code>cvcpkg builder status 4</code></td></tr>
            <tr><td><code>builder logs</code></td><td>Recent build activity, optionally per builder</td>
                <td><code>cvcpkg builder logs star-00</code></td></tr>
            <tr><td><code>builder run</code></td><td>Register this machine as a builder and poll for work</td>
                <td><code>cvcpkg builder run --name star-00 --max-jobs 4</code></td></tr>
            <tr><td><code>builder fleet</code></td><td>Run one builder against several servers at once</td>
                <td><code>cvcpkg builder fleet --config /etc/cvcpkg/fleet.yaml</code></td></tr>
            <tr><td><code>builder gc</code></td><td>Reclaim disk from orphaned build scratch dirs</td>
                <td><code>cvcpkg builder gc --work-dir /tmp/cvcpkg-builder</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Publishing</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>publish</code></td><td>Upload bundle archives to a server</td>
                <td><code>cvcpkg publish dist/mylib-*.tar.zst --org my-team</code></td></tr>
            <tr><td><code>recipe push</code></td><td>Upload a recipe directory so the server can build it</td>
                <td><code>cvcpkg recipe push mylib --recipes-dir recipes</code></td></tr>
            <tr><td><code>recipe pull</code></td><td>Fetch a recipe (and its scripts) back down</td>
                <td><code>cvcpkg recipe pull mylib --output-dir ./recipes</code></td></tr>
            <tr><td><code>yank</code> / <code>unyank</code></td><td>Hide a version from resolution, or restore it</td>
                <td><code>cvcpkg yank mylib 2.1.0</code></td></tr>
            <tr><td><code>nuke</code></td><td>Permanently delete a yanked bundle (admin)</td>
                <td><code>cvcpkg nuke mylib 2.1.0</code></td></tr>
            <tr><td><code>rev-bump</code></td><td>Bump <code>cvc_revision</code> for a recipe and its dependents</td>
                <td><code>cvcpkg rev-bump openssl</code></td></tr>
            <tr><td><code>next-revision</code></td><td>Print the revision a <code>--bump</code> pack would use</td>
                <td><code>cvcpkg next-revision zlib</code></td></tr>
            <tr><td><code>cascade-bump</code></td><td>Published-aware family bump across a recipe tree</td>
                <td><code>cvcpkg cascade-bump boost</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Signing</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>key</code></td><td>Create, list, and trust Ed25519 signing keys</td>
                <td><code>cvcpkg key generate --label release</code></td></tr>
            <tr><td><code>sign</code></td><td>Sign an archive</td>
                <td><code>cvcpkg sign dist/mylib-2.1.0.tar.zst --signing-key release</code></td></tr>
            <tr><td><code>verify-sig</code></td><td>Verify an archive against the trusted keys</td>
                <td><code>cvcpkg verify-sig dist/mylib-2.1.0.tar.zst</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Catalog and cache</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>catalog</code></td><td>Browse or refresh the component catalog</td>
                <td><code>cvcpkg catalog --refresh</code></td></tr>
            <tr><td><code>catalog-generate</code></td><td>Build a unified catalog from per-platform indexes</td>
                <td><code>cvcpkg catalog-generate --output-dir ./catalog</code></td></tr>
            <tr><td><code>cache</code></td><td>Inspect or clear the local build cache</td>
                <td><code>cvcpkg cache list</code></td></tr>
            <tr><td><code>gc</code></td><td>Prune the download cache</td>
                <td><code>cvcpkg gc</code></td></tr>
            <tr><td><code>clean</code></td><td>Remove leftover build work directories</td>
                <td><code>cvcpkg clean</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Server, orgs, and accounts</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>login</code></td><td>Sign in with your SSO identity (browser or device code)</td>
                <td><code>cvcpkg login --server https://cvcpkg.org</code></td></tr>
            <tr><td><code>logout</code></td><td>Sign out and revoke the local session</td>
                <td><code>cvcpkg logout</code></td></tr>
            <tr><td><code>whoami</code></td><td>Show who you are signed in as, and your orgs</td>
                <td><code>cvcpkg whoami</code></td></tr>
            <tr><td><code>auth</code></td><td>Manage sessions/devices; list identity providers</td>
                <td><code>cvcpkg auth providers</code></td></tr>
            <tr><td><code>register</code></td><td>Request an API token from a server</td>
                <td><code>cvcpkg register --server https://cvcpkg.org</code></td></tr>
            <tr><td><code>token</code></td><td>Create, list, and revoke API tokens</td>
                <td><code>cvcpkg token list</code></td></tr>
            <tr><td><code>user</code></td><td>Look up a user profile</td>
                <td><code>cvcpkg user info alice</code></td></tr>
            <tr><td><code>org</code></td><td>Create organizations and manage their members</td>
                <td><code>cvcpkg org create my-team --display-name "My Team"</code></td></tr>
            <tr><td><code>server</code></td><td>Administer a running server</td>
                <td><code>cvcpkg server stats</code></td></tr>
            <tr><td><code>webhook</code></td><td>Manage server webhooks</td>
                <td><code>cvcpkg webhook register https://ci.example/hook</code></td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Diagnostics and legacy</h3>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead><tr><th>Command</th><th>What it does</th><th>Example</th></tr></thead>
          <tbody>
            <tr><td><code>doctor</code></td><td>Check this machine can build and install packages</td>
                <td><code>cvcpkg doctor</code></td></tr>
            <tr><td><code>telemetry</code></td><td>Opt in/out of anonymous environment telemetry</td>
                <td><code>cvcpkg telemetry status</code></td></tr>
            <tr><td><code>add</code> / <code>remove</code></td>
                <td><em>Legacy.</em> Edit a requirements file's component list</td>
                <td><code>cvcpkg add boost --from requirements.yaml</code></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- CMake Integration -->
    <div id="cmake" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-cogs"></i></span>
        CMake Integration
      </h2>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Point <code>CMAKE_PREFIX_PATH</code> at your cvcpkg prefix:
        </p>
        <div class="guide-code"><pre><code>cmake -B build \\
  -DCMAKE_PREFIX_PATH=$(pwd)/deps \\
  -DCMAKE_BUILD_TYPE=Release</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Use <code>find_package()</code> in your CMakeLists.txt as usual
          &mdash; all packages install standard CMake config files:
        </p>
        <div class="guide-code"><pre><code>find_package(Boost REQUIRED COMPONENTS system filesystem)
find_package(HDF5 REQUIRED COMPONENTS CXX)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets)
find_package(VTK REQUIRED)</code></pre></div>
      </div>
    </div>

    <!-- Creating Recipes -->
    <div id="recipes" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-mortar-pestle"></i></span>
        Creating Recipes
      </h2>
      <p class="has-text-grey-lighter mb-4">
        A recipe describes how to fetch, build, and package a library.
        Each recipe lives in its own directory under <code>recipes/</code>
        with a <code>recipe.yaml</code> and one or more build scripts.
      </p>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Create a recipe directory:</p>
        <div class="guide-code"><pre><code>mkdir -p recipes/mylib
cd recipes/mylib</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Write <code>recipe.yaml</code> &mdash; the full schema:
        </p>
        <div class="guide-code"><pre><code>schema_version: 1

recipe:
  name: mylib
  upstream_version: "2.1.0"
  cvc_revision: 1
  maintainer: "Your Name"
  maintainer_email: "you@example.com"
  description: "A great library"
  homepage: https://example.com/mylib
  license: MIT
  tags: [utils, io]

source:
  type: tarball
  url: https://example.com/mylib-2.1.0.tar.gz
  sha256: abc123...   # required for tarball sources
  strip_components: 1

depends:
  build:
    - name: zlib      # build-time dependency
    - name: boost
      version: "&gt;=1.86"
  host_tools:
    - cmake
    - ninja

build:
  matrix:
    - platform: linux
      script: build.sh
    - platform: macos
      script: build.sh
    - platform: windows
      script: build.ps1

package:
  files:
    - lib/libmylib*
    - include/mylib/**
  cmake_packages:
    - {{ name: mylib, targets: [mylib::mylib] }}

test:
  script: test.sh</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Write a build script (<code>build.sh</code>). Standard
          <code>CVC_*</code> environment variables are provided:
        </p>
        <div class="guide-code"><pre><code>#!/usr/bin/env bash
set -euo pipefail

cmake -S "$CVC_SOURCE_DIR" -B "$CVC_BUILD_DIR" -G Ninja \\
  -DCMAKE_INSTALL_PREFIX="$CVC_INSTALL_DIR" \\
  -DCMAKE_PREFIX_PATH="$CVC_DEPS_PREFIX" \\
  -DCMAKE_BUILD_TYPE="$CMAKE_BUILD_TYPE" \\
  -DBUILD_SHARED_LIBS="$BUILD_SHARED_LIBS"

cmake --build "$CVC_BUILD_DIR" --parallel
cmake --install "$CVC_BUILD_DIR"</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Validate your recipe and build locally:
        </p>
        <div class="guide-code"><pre><code># Validate recipe.yaml schema
cvcpkg validate recipes/mylib

# Build and package
cvcpkg build recipes/mylib --prefix ./deps

# The archive is written to dist/
ls dist/mylib-*.tar.gz</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-info-circle has-text-link"></i></span>
          <strong class="has-text-white">Source types:</strong>
          <code>tarball</code>, <code>git</code>,
          <code>vcpkg</code>, <code>brew</code>, <code>apt</code>,
          <code>vendored</code>, and <code>prebuilt</code>.
          For tarballs, the <code>sha256</code> field is required.
          A <code>mirror</code> URL can be specified as a fallback.
        </p>
      </div>
    </div>

    <!-- Accounts & Signing In -->
    <div id="accounts" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-right-to-bracket"></i></span>
        Accounts &amp; Signing In
      </h2>
      <p class="has-text-grey-lighter mb-4">
        You sign in to cvcpkg with your <strong class="has-text-white">SSO
        identity</strong> (an OIDC provider such as a tx.wtf ring). Signing in
        gives you a named identity &mdash; a <em>handle</em> like
        <code>alice</code> &mdash; that org owners can add to their organizations
        by name, and lets you mint your own API tokens. Machines (CI, builders)
        keep using API tokens directly; humans sign in.
      </p>

      <h3 class="title is-5 has-text-white mt-5">On the website</h3>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Click <strong class="has-text-white">Sign in</strong> (or open
          <a class="has-text-link" href="/login">/login</a>) and choose your
          identity provider. If the server is connected to several providers you
          pick which one; if it has just one you go straight there.
        </p>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          You land on <a class="has-text-link" href="/account">/account</a>, where
          you can see your handle and role and
          <strong class="has-text-white">mint API tokens</strong> for use with the
          CLI or CI. Each token is named <code>&lt;you&gt;.&lt;label&gt;</code> and
          the secret is shown once.
        </p>
      </div>

      <h3 class="title is-5 has-text-white mt-5">From the CLI</h3>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Sign in from a terminal. On a desktop this opens your browser; over SSH
          or on a headless box it prints a short code to approve in any browser:
        </p>
        <div class="guide-code"><pre><code>cvcpkg login --server https://cvcpkg.org

# force the device-code flow (no local browser)
cvcpkg login --server https://cvcpkg.org --code</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          If the server offers several identity providers, list them and pick one
          (otherwise you choose in the browser):
        </p>
        <div class="guide-code"><pre><code>cvcpkg auth providers
cvcpkg login --server https://cvcpkg.org --provider ringb</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Check who you are, and sign out when done. Once you are signed in, other
          commands (install, search, <code>org</code>, publish&hellip;) use that
          session automatically &mdash; no <code>--token</code> needed:
        </p>
        <div class="guide-code"><pre><code>cvcpkg whoami
cvcpkg auth devices     # your active sessions
cvcpkg logout</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-robot has-text-link"></i></span>
          <strong class="has-text-white">CI and automation</strong> should not use
          <code>cvcpkg login</code>. Mint a machine token on
          <a class="has-text-link" href="/account">/account</a> and export it as
          <code>CVCPKG_TOKEN</code> instead.
        </p>
      </div>
    </div>

    <!-- Publishing Builds -->
    <div id="publishing" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-cloud-upload-alt"></i></span>
        Publishing Builds
      </h2>
      <p class="has-text-grey-lighter mb-4">
        Publishing requires a token with the <code>publisher</code> or
        <code>admin</code> role. Publishing to the community archive is
        admin-gated &mdash; contact an administrator for access.
        Organization members can publish to their own org namespace.
      </p>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Publish an archive to a cvcpkg server:
        </p>
        <div class="guide-code"><pre><code>cvcpkg publish dist/mylib-2.1.0-linux-x86_64-release-shared.tar.gz \\
  --server https://cvcpkg.org \\
  --token cvctok_...</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Publish to an organization namespace:
        </p>
        <div class="guide-code"><pre><code>cvcpkg publish dist/mylib-*.tar.gz \\
  --server https://cvcpkg.org \\
  --token cvctok_... \\
  --org my-team</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Tag a release (optional; untagged builds are "live"):
        </p>
        <div class="guide-code"><pre><code>cvcpkg publish dist/mylib-*.tar.gz \\
  --server https://cvcpkg.org \\
  --token cvctok_... \\
  --release-tag v2.1.0</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-info-circle has-text-link"></i></span>
          <strong class="has-text-white">Token roles:</strong>
          <code>reader</code> (read-only access),
          <code>publisher</code> (publish + yank),
          <code>admin</code> (full access including token/org management
          and hard deletes). Use <code>CVCPKG_SERVER_URL</code> and
          <code>CVCPKG_TOKEN</code> environment variables to avoid
          passing flags every time.
        </p>
      </div>
    </div>

    <!-- Organizations -->
    <div id="orgs" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-building"></i></span>
        Organizations
      </h2>
      <p class="has-text-grey-lighter mb-4">
        Organizations let teams publish and manage packages under a shared
        namespace. Each org has a configurable storage quota (default
        <strong class="has-text-white">10&nbsp;GiB</strong>) set by the
        <code>CVCPKG_ORG_STORAGE_LIMIT_BYTES</code> environment variable.
        Admins can adjust the limit per-org via the API.
      </p>
      <p class="has-text-grey-lighter mb-4">
        You create and administer organizations two ways: from the
        <strong class="has-text-white">website</strong> after signing in, or from
        the <strong class="has-text-white">CLI</strong>. Creating an org needs a
        <code>publisher</code> or <code>admin</code> role; managing its members
        needs to be an <strong>owner</strong> of that org (or a global admin).
      </p>

      <h3 class="title is-5 has-text-white mt-5">Create an organization</h3>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          From the CLI &mdash; you become its first owner:
        </p>
        <div class="guide-code"><pre><code>cvcpkg login --server https://cvcpkg.org      # once, if not already signed in
cvcpkg org create my-team --display-name "My Team"

# optional: --description, --homepage, --private (members-only visibility)</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Or over the REST API directly:
        </p>
        <div class="guide-code"><pre><code>curl -X POST https://cvcpkg.org/v1/orgs \\
  -H "Authorization: Bearer cvctok_..." \\
  -H "Content-Type: application/json" \\
  -d '{{"slug": "my-team", "display_name": "My Team"}}'</code></pre></div>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Administer members &mdash; on the website</h3>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Open your org at <code>/org/&lt;slug&gt;</code> and click
          <strong class="has-text-white">Manage</strong> (owners only). From
          <code>/org/&lt;slug&gt;/manage</code> you can add a member by their
          <strong>handle</strong>, choose their role, and remove members &mdash; no
          command line needed. Grant <code>owner</code> to make someone a
          co-administrator of the org.
        </p>
      </div>

      <h3 class="title is-5 has-text-white mt-5">Administer members &mdash; from the CLI</h3>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          A member is either an SSO <strong>username</strong> (a person &mdash; use
          <code>--user</code>; they must have signed in at least once so the handle
          exists) or a <strong>machine-token</strong> name (use
          <code>--token-name</code>). Grant <code>--role owner</code> to make them an
          org admin.
        </p>
        <div class="guide-code"><pre><code># add a person as a co-owner
cvcpkg org add-member my-team --user alice --role owner

# add a CI/machine token as a plain member
cvcpkg org add-member my-team --token-name acme-ci --role member

# list members (shows each one's kind: user / token / orphan)
cvcpkg org members my-team

# remove a member
cvcpkg org remove-member my-team --user alice</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          The same over the REST API (<code>principal_kind</code> is
          <code>user</code>, <code>token</code>, or <code>auto</code>):
        </p>
        <div class="guide-code"><pre><code>curl -X POST "https://cvcpkg.org/v1/orgs/my-team/members?token_name=alice&amp;role=owner&amp;principal_kind=user" \\
  -H "Authorization: Bearer cvctok_..."</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Update the storage limit (admin only):
        </p>
        <div class="guide-code"><pre><code># Set to 50 GiB
curl -X PATCH https://cvcpkg.org/v1/orgs/my-team \\
  -H "Authorization: Bearer cvctok_..." \\
  -H "Content-Type: application/json" \\
  -d '{{"storage_limit_bytes": 53687091200}}'</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-info-circle has-text-link"></i></span>
          <strong class="has-text-white">Storage enforcement:</strong>
          When publishing to an organization, the server checks that
          <code>storage_used + upload_size &le; storage_limit</code>.
          If the limit is exceeded the upload is rejected with
          <span class="tag is-danger is-light">HTTP 413</span>.
          Organizations require the database backend
          (<code>CVCPKG_DATABASE_URL</code>).
        </p>
      </div>
    </div>

    <!-- Self-Hosting -->
    <div id="server" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-server"></i></span>
        Self-Hosting a Package Server
      </h2>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Run a local server for development or private packages:
        </p>
        <div class="guide-code"><pre><code>\
cvcpkg-server run --state-dir ./my-packages --port 8420</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Or use the Docker Compose production stack:
        </p>
        <div class="guide-code"><pre><code># from the repo root
cp .env.production.example .env.production
# Edit .env.production with your secrets
docker compose -f docker-compose.production.yml \\
  --env-file .env.production up -d</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Create an API token and publish packages:
        </p>
        <div class="guide-code"><pre><code>\
# Create a publisher token
cvcpkg-server token create --name ci-bot --role publisher

# Publish an archive
cvcpkg publish my-lib-1.0-linux-x86_64-release-shared.tar.zst \\
  --server http://localhost:8420 \\
  --token cvctok_...</code></pre></div>
      </div>
    </div>

    <!-- Remote Builders -->
    <div id="remote-builders" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-hard-hat"></i></span>
        Remote Builders
      </h2>
      <p class="has-text-grey-lighter mb-4">
        Remote builders are worker machines that automatically build
        packages from recipes. The server dispatches build jobs to
        registered builders via WebSocket. Results (archives and logs)
        are streamed back and published to the package index.
      </p>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          <strong class="has-text-white">Push recipes to the server</strong>
          &mdash; recipes define how to build a package:
        </p>
        <div class="guide-code"><pre><code>\
cvcpkg recipe push zlib \\
  --server https://cvcpkg.org --token cvctok_...

# Or push all local recipes at once
cvcpkg recipe push-all --server https://cvcpkg.org --token cvctok_...</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          <strong class="has-text-white">Register a builder</strong>
          &mdash; create a builder token and start the worker:
        </p>
        <div class="guide-code"><pre><code>\
# Create a builder token (on the server)
cvcpkg-server token create --name builder-01 --role builder

# Start the builder worker
cvcpkg builder run \\
  --server https://cvcpkg.org \\
  --token cvctok_... \\
  --max-jobs 2</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          <strong class="has-text-white">Submit build jobs</strong>
          &mdash; build a single recipe or a full dependency graph:
        </p>
        <div class="guide-code"><pre><code>\
# Single build
cvcpkg builds submit --recipe zlib --platform linux --arch x86_64 \\
  --server https://cvcpkg.org --token cvctok_...

# DAG build (resolves dependencies automatically)
cvcpkg builds submit-dag tiff --platform linux --arch x86_64 \\
  --server https://cvcpkg.org --token cvctok_...</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          <strong class="has-text-white">Monitor builds</strong>
          &mdash; use the web UI or CLI to track progress:
        </p>
        <div class="guide-code"><pre><code>\
# List recent builds
cvcpkg builds list --server https://cvcpkg.org --token cvctok_...

# Or visit the web dashboard:
#   /builders  &mdash; registered builders and status
#   /builds    &mdash; all build jobs with filters
#   /build/42  &mdash; single build with live log</code></pre></div>
      </div>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-info-circle has-text-link"></i></span>
          <strong class="has-text-white">DAG builds</strong> automatically
          resolve recipe dependencies and submit them in the correct order.
          Leaf dependencies build first; the target recipe builds only after
          all its dependencies succeed. Use the
          <a href="/builds" class="has-text-link">Build Jobs</a> dashboard
          to view DAG progress.
        </p>
      </div>
    </div>

    <!-- Server Configuration -->
    <div id="server-config" class="guide-section">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-sliders-h"></i></span>
        Server Configuration
      </h2>
      <p class="has-text-grey-lighter mb-4">
        Server settings are controlled via environment variables or
        <code>cvcpkg-server run</code> CLI flags. State (index, tokens,
        audit log, archives) is stored under the state directory
        (<code>CVCPKG_SERVER_STATE_DIR</code>, default
        <code>/var/lib/cvcpkg-server</code>). The HMAC signing key is
        stored in <code>&lt;state-dir&gt;/hmac_key</code> (mode 0600).
      </p>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead>
            <tr>
              <th>Variable / Flag</th>
              <th>Default</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr><td><code>CVCPKG_DATABASE_URL</code><br>
                    <span class="is-size-7 has-text-grey">--database-url</span></td>
                <td><em>empty</em></td>
                <td>PostgreSQL URL; enables DB backend (required for orgs)</td></tr>
            <tr><td><code>CVCPKG_SERVER_STATE_DIR</code><br>
                    <span class="is-size-7 has-text-grey">--state-dir</span></td>
                <td><code>./cvcpkg-server-data</code></td>
                <td>Directory for index, tokens, audit log, and archives</td></tr>
            <tr><td><code>CVCPKG_SERVER_STORAGE_URI</code><br>
                    <span class="is-size-7 has-text-grey">--storage</span></td>
                <td><code>file://&lt;state-dir&gt;</code></td>
                <td>Storage backend URI (file, S3, etc.)</td></tr>
            <tr><td><code>CVCPKG_MAX_UPLOAD_BYTES</code><br>
                    <span class="is-size-7 has-text-grey">--max-upload-bytes</span></td>
                <td>4 GiB</td>
                <td>Maximum bundle upload size.  Accepts a byte count or a
                    human size (<code>8GB</code>, <code>512MB</code>);
                    units are binary.</td></tr>
            <tr><td><code>CVCPKG_CHUNK_SIZE</code></td>
                <td>8 MiB</td>
                <td>Chunk size for chunked uploads</td></tr>
            <tr><td><code>CVCPKG_UPLOAD_SESSION_TTL</code></td>
                <td>3600 s</td>
                <td>Chunked upload session timeout</td></tr>
            <tr><td><code>CVCPKG_RATE_LIMIT_RPM</code></td>
                <td>300</td>
                <td>Write-endpoint rate limit (requests/min, 0 = disabled)</td></tr>
            <tr><td><code>CVCPKG_ORG_STORAGE_LIMIT_BYTES</code></td>
                <td>10 GiB</td>
                <td>Default per-organization storage quota</td></tr>
            <tr><td><code>CVCPKG_CORS_ORIGINS</code></td>
                <td><em>empty</em></td>
                <td>Comma-separated allowed CORS origins</td></tr>
            <tr><td><code>CVCPKG_SERVER_REQUIRE_AUTH_READS</code><br>
                    <span class="is-size-7 has-text-grey">--require-auth-reads</span></td>
                <td>false</td>
                <td>Require auth token for read endpoints</td></tr>
            <tr><td><code>CVCPKG_LOG_JSON</code><br>
                    <span class="is-size-7 has-text-grey">--log-json</span></td>
                <td>false</td>
                <td>Structured JSON log output</td></tr>
            <tr><td><span class="is-size-7 has-text-grey">--host</span></td>
                <td>0.0.0.0</td>
                <td>Bind address</td></tr>
            <tr><td><span class="is-size-7 has-text-grey">--port</span></td>
                <td>8420</td>
                <td>Listen port</td></tr>
            <tr><td><span class="is-size-7 has-text-grey">--workers</span></td>
                <td>1</td>
                <td>Number of uvicorn workers</td></tr>
          </tbody>
        </table>
      </div>
      <p class="has-text-grey-lighter mt-4">
        <span class="icon"><i class="fas fa-info-circle has-text-link"></i></span>
        For Docker deployments, copy <code>.env.production.example</code>
        and set <code>POSTGRES_PASSWORD</code>, <code>POSTGRES_USER</code>,
        <code>POSTGRES_DB</code>, <code>BACKEND_PORT</code>, and
        <code>CVCPKG_RELEASE</code>. See
        <code>docker-compose.production.yml</code>.
      </p>
      <div class="box has-background-black-ter mt-4">
        <p class="has-text-grey-lighter">
          <span class="icon"><i class="fas fa-shield-alt has-text-link"></i></span>
          <strong class="has-text-white">Branding:</strong>
          Customize the landing page with
          <code>CVCPKG_SITE_TITLE</code>,
          <code>CVCPKG_SITE_TAGLINE</code>, and
          <code>CVCPKG_SITE_HERO</code>.
          Set <code>CVCPKG_GITHUB_REPO</code> to change the
          GitHub link (default: <code>cy-pca/cvcpkg</code>).
          <code>CVCPKG_SITE_LOGO</code> overrides the favicon and social
          image &mdash; either an <code>https://</code> URL or a path to a
          local image file (png/svg/ico/jpg/webp); unset serves the bundled
          CyberPC Angel, LLC gears logo.  <code>CVCPKG_SITE_URL</code> sets
          the origin used to build absolute social-image URLs
          (default: <code>https://cvcpkg.org</code>).
        </p>
      </div>
    </div>

    <!-- REST API -->
    <div id="api" class="guide-section">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-code"></i></span>
        REST API
      </h2>
      <p class="has-text-grey-lighter mb-4">
        The cvcpkg server exposes a full REST API. Key endpoints:
      </p>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead>
            <tr>
              <th>Method</th>
              <th>Endpoint</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr><td><span class="tag is-success is-light">GET</span></td>
                <td><code>/v1/packages</code></td>
                <td>List all packages (filterable, paginated)</td></tr>
            <tr><td><span class="tag is-success is-light">GET</span></td>
                <td><code>/v1/packages/{{name}}</code></td>
                <td>Get builds for a specific package</td></tr>
            <tr><td><span class="tag is-success is-light">GET</span></td>
                <td><code>/v1/catalog</code></td>
                <td>Full catalog (YAML)</td></tr>
            <tr><td><span class="tag is-success is-light">GET</span></td>
                <td><code>/v1/deps</code></td>
                <td>Dependency graph and recipe metadata</td></tr>
            <tr><td><span class="tag is-success is-light">GET</span></td>
                <td><code>/v1/download/{{file}}</code></td>
                <td>Download a package archive</td></tr>
            <tr><td><span class="tag is-info is-light">POST</span></td>
                <td><code>/v1/publish</code></td>
                <td>Publish a new package</td></tr>
            <tr><td><span class="tag is-warning is-light">POST</span></td>
                <td><code>/v1/packages/{{name}}/{{ver}}/yank</code></td>
                <td>Yank a package version</td></tr>
            <tr><td><span class="tag is-danger is-light">DEL</span></td>
                <td><code>/v1/packages/{{name}}/{{ver}}</code></td>
                <td>Delete a package (admin)</td></tr>
          </tbody>
        </table>
      </div>
      <p class="has-text-grey-lighter mt-4">
        <span class="icon"><i class="fas fa-external-link-alt"></i></span>
        See the full interactive API documentation at
        <a href="/docs" class="has-text-link">/docs</a> (Swagger UI) or
        <a href="/redoc" class="has-text-link">/redoc</a> (ReDoc).
      </p>
    </div>

  </div>
</section>

{_footer_html()}

<script>
{_NAVBAR_JS}
</script>
</body>
</html>"""
