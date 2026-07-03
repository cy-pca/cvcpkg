"""Landing page HTML for cvcpkg-server.

Serves a self-contained single-page index at ``/`` that displays
published packages grouped by name, supports sorting and filtering,
and links to per-package detail pages.  Uses Bulma CSS for styling.
"""

from __future__ import annotations

import html as _html
import json as _json
import os

from cvcpkg import __version__

_GITHUB_REPO = os.environ.get("CVCPKG_GITHUB_REPO", "transfix/libcvc-deps")
_GITHUB_URL = f"https://github.com/{_GITHUB_REPO}"
_SITE_TITLE = os.environ.get("CVCPKG_SITE_TITLE", "cvcpkg")
_SITE_TAGLINE = os.environ.get("CVCPKG_SITE_TAGLINE", "Package Archive")
_SITE_HERO = os.environ.get(
    "CVCPKG_SITE_HERO",
    "A cross-platform, language-agnostic binary package archive for the"
    " scientific computing community. Pre-built C/C++ libraries for Linux,"
    " macOS, and Windows \u2014 with curated LTS releases for reproducible"
    " downstream builds.",
)

# ── Shared CSS ───────────────────────────────────────────────────

_CSS = r"""
html { background-color: #0a0a0a; }

.hero-gradient {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
}

.logo-icon {
  width: 48px; height: 48px;
  background: linear-gradient(135deg, #3273dc, #48c774);
  border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 24px; font-weight: 700; color: #fff;
  margin-right: 12px;
  vertical-align: middle;
}

.stat-box {
  text-align: center;
  padding: 1.25rem 1rem;
}
.stat-box .title { margin-bottom: 0.25rem !important; }

th.is-sortable { cursor: pointer; user-select: none; white-space: nowrap; }
th.is-sortable:hover { color: #3273dc; }
th.is-sorted { color: #3273dc; }
.sort-arrow { font-size: 0.65em; margin-left: 4px; }

.platform-tag.linux { background-color: rgba(72, 199, 116, 0.15); color: #48c774; }
.platform-tag.darwin { background-color: rgba(255, 221, 87, 0.15); color: #ffdd57; }
.platform-tag.windows { background-color: rgba(50, 115, 220, 0.15); color: #3273dc; }
.platform-tag.freebsd { background-color: rgba(255, 56, 56, 0.15); color: #ff3838; }
.platform-tag.netbsd { background-color: rgba(255, 145, 51, 0.15); color: #ff9133; }
.platform-tag.openbsd { background-color: rgba(241, 196, 15, 0.15); color: #f1c40f; }
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
@media screen and (max-width: 768px) {
  .badge-mainline, .badge-community {
    font-size: 0.6em; padding: 2px 6px;
  }
}
.note-card { border-left: 3px solid #3273dc; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }
.recipe-viewer { max-height: 500px; overflow-y: auto; }
.recipe-viewer pre { white-space: pre; word-break: normal; overflow-x: auto; }
.collapsible-header { cursor: pointer; user-select: none; }
.collapsible-header:hover { color: #3273dc; }

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
        <span class="logo-icon">C</span>
        <strong class="is-size-4">cvcpkg</strong>
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
  </div>
</footer>"""


def _head_html(title: str) -> str:
    return f"""<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
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
  updateStatsFromResponse(data);
  updateFilterOptions(data.facets || {});
  renderResults(data);
}

function updateStatsFromResponse(data) {
  const facets = data.facets || {};
  const packages = (data.package_count || 0);
  const builds = (data.total || 0);
  const platforms = (facets.platforms || []).length;
  const size = data.total_size_bytes || 0;
  document.getElementById('stat-packages').textContent = packages;
  document.getElementById('stat-builds').textContent = builds;
  document.getElementById('stat-platforms').textContent = platforms;
  document.getElementById('stat-size').textContent = fmtSizeLarge(size);
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
  _syncSelect('release-filter', facets.releases || [], state.release);
  _syncSelect('tag-filter', facets.tags || [], state.tag);
}

function groupBundlesByName(pkgs) {
  const groups = {};
  pkgs.forEach(p => {
    if (!groups[p.name]) {
      groups[p.name] = {
        name: p.name,
        version: p.version,
        builds: [],
        platforms: new Set(),
        totalSize: 0,
      };
    }
    const g = groups[p.name];
    g.builds.push(p);
    if (p.platform) g.platforms.add(p.platform);
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
  const countEl = document.getElementById('pkg-count');
  if (shownBuilds < totalBuilds) {
    countEl.textContent =
      'Showing ' + grouped.length + ' of ' + totalPackages
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
      const isMainline = recipeNames.includes(g.name);
      const badge = isMainline
        ? '<span class="badge-mainline" title="Official cvcpkg recipe"><i class="fas fa-check-circle"></i> cvcpkg</span>'
        : '<span class="badge-community" title="Community upload"><i class="fas fa-users"></i> community</span>';
      return `
      <tr class="pkg-card">
        <td>
          <a class="pkg-link" href="/package/${encodeURIComponent(g.name)}">
            <strong>${esc(g.name)}</strong>
          </a>
        </td>
        <td><code>${esc(g.version)}</code></td>
        <td>${[...g.platforms].sort().map(p => platformTag(p)).join(' ')}</td>
        <td><span class="tag is-dark is-rounded">${g.builds.length}</span></td>
        <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(g.totalSize)}</span></td>
        <td>${badge}</td>
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
    if (shownBuilds < totalBuilds) {
      more.style.display = '';
      more.disabled = false;
      more.textContent = 'Load more (' + (totalBuilds - shownBuilds) + ' remaining)';
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

const debouncedSearch = _debounce(runSearch, 250);

async function init() {
  // Fetch recipe metadata once; used for license/description fallback and
  // the "mainline vs community" badge.
  try {
    const dresp = await fetch('/v1/deps');
    const ddata = await dresp.json();
    recipeMeta = ddata.meta || {};
    recipeNames = ddata.recipe_names || [];
  } catch (_) {}
  await runSearch();
}

function loadMore() {
  const btn = document.getElementById('load-more');
  if (btn) btn.disabled = true;
  state.offset = loadedPackages.length;
  runSearch({ append: true });
}
"""


def landing_html() -> str:
    """Return the complete HTML for the landing page."""
    page_title = _html.escape(f"{_SITE_TITLE} \u2014 {_SITE_TAGLINE}", quote=False)
    hero_title = _html.escape(_SITE_TITLE)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(page_title)}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<!-- Hero -->
<section class="hero hero-gradient is-medium">
  <div class="hero-body">
    <div class="container">
      <p class="title is-2 has-text-white">
        <span class="icon is-large mr-2"><i class="fas fa-cubes"></i></span>
        {hero_title}
      </p>
      <p class="subtitle is-5 has-text-grey-lighter" style="max-width: 740px;">
        {_html.escape(_SITE_HERO, quote=False)}
      </p>
    </div>
  </div>
</section>

<!-- Stats -->
<section class="section pt-4 pb-4 has-background-black-ter">
  <div class="container">
    <div class="columns is-mobile is-multiline">
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-link" id="stat-packages">&mdash;</p>
          <p class="heading has-text-grey-light">Packages</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-info" id="stat-builds">&mdash;</p>
          <p class="heading has-text-grey-light">Builds</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-success" id="stat-platforms">&mdash;</p>
          <p class="heading has-text-grey-light">Platforms</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-warning" id="stat-size">&mdash;</p>
          <p class="heading has-text-grey-light">Total Size</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- Build system quick links -->
<section class="section pt-2 pb-4 has-background-black-ter">
  <div class="container">
    <div class="columns is-mobile is-multiline is-centered">
      <div class="column is-narrow">
        <a href="/builders" class="button is-dark is-outlined">
          <span class="icon"><i class="fas fa-server"></i></span><span>Builders</span>
        </a>
      </div>
      <div class="column is-narrow">
        <a href="/builds" class="button is-dark is-outlined">
          <span class="icon"><i class="fas fa-hammer"></i></span><span>Build Jobs</span>
        </a>
      </div>
      <div class="column is-narrow">
        <a href="/recipes" class="button is-dark is-outlined">
          <span class="icon"><i class="fas fa-scroll"></i></span><span>Recipes</span>
        </a>
      </div>
    </div>
  </div>
</section>

<!-- Package index -->
<section class="section has-background-black-bis">
  <div class="container">
    <div class="columns is-vcentered mb-4">
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
        <div class="field">
          <div class="control has-icons-left">
            <div class="select is-dark is-fullwidth">
              <select id="tag-filter"><option value="">All tags</option></select>
            </div>
            <span class="icon is-left"><i class="fas fa-tag"></i></span>
          </div>
        </div>
      </div>
      <div class="column is-2">
        <div class="field">
          <div class="control">
            <div class="select is-dark is-fullwidth">
              <select id="platform-filter"><option value="">All platforms</option></select>
            </div>
          </div>
        </div>
      </div>
      <div class="column is-2">
        <div class="field">
          <div class="control">
            <div class="select is-dark is-fullwidth">
              <select id="release-filter">
                <option value="">All channels</option>
                <option value="live" data-static="true">Live only</option>
              </select>
            </div>
          </div>
        </div>
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
            <th class="is-sortable" data-key="builds">Builds <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="totalSize">Size <span class="sort-arrow"></span></th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody id="pkg-body">
          <tr>
            <td colspan="6" class="has-text-centered py-6">
              <span class="icon is-large has-text-link">
                <i class="fas fa-spinner fa-spin fa-2x"></i>
              </span>
            </td>
          </tr>
        </tbody>
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
    runSearch();
  }});
  document.getElementById('tag-filter').addEventListener('change', e => {{
    state.tag = e.target.value;
    runSearch();
  }});
  document.getElementById('release-filter').addEventListener('change', e => {{
    state.release = e.target.value;
    runSearch();
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
let allBuilds = [];
let currentSort = { key: 'platform', dir: 'asc' };

async function init(name) {
  pkgName = name;
  try {
    const resp = await fetch('/v1/packages/' + encodeURIComponent(name));
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

    // Pick best build for first platform available
    const firstBuild = allBuilds[0];

    const depNames = [...allDeps].sort();
    let lines = ['mkdir -p /opt/cvcpkg', ''];
    if (depNames.length > 0) {
      lines.push('# Download dependencies first:');
      depNames.forEach(d => {
        lines.push('# ' + d + ': https://cvcpkg.org/package/' + encodeURIComponent(d));
      });
      lines.push('');
    }
    lines.push('# Download and extract ' + pkgName + ':');
    const url = 'https://cvcpkg.org' + firstBuild.archive_url;
    const fname = firstBuild.archive_url.split('/').pop();
    lines.push('curl -LO ' + url);
    lines.push('tar --zstd -xf ' + fname + ' -C /opt/cvcpkg');
    lines.push('');
    lines.push('# Point CMake at the prefix:');
    lines.push('cmake -DCMAKE_PREFIX_PATH=/opt/cvcpkg ..');

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

  // Load recipe YAML viewer
  if (isMainline) {
    fetchRecipe();
  }
}

async function fetchRecipe() {
  try {
    const resp = await fetch('/v1/recipe/' + encodeURIComponent(pkgName));
    if (!resp.ok) return;
    const text = await resp.text();
    const recipeEl = document.getElementById('recipe-content');
    const recipeSection = document.getElementById('recipe-section');
    if (recipeEl && recipeSection) {
      recipeEl.textContent = text;
      recipeSection.style.display = '';
    }
  } catch (_) {}
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

  if (p.description) {
    document.getElementById('pkg-description').textContent = p.description;
    document.getElementById('pkg-description').style.display = '';
  }
  if (p.homepage) {
    const el = document.getElementById('pkg-homepage');
    el.href = p.homepage;
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

function renderBuilds() {
  let builds = [...allBuilds];
  if (builds.length === 0) {
    document.getElementById('builds-body').innerHTML =
      '<tr><td colspan="9" class="has-text-centered has-text-grey-light py-4">No builds available.</td></tr>';
    return;
  }
  builds.sort((a, b) => {
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
  });

  const tbody = document.getElementById('builds-body');
  tbody.innerHTML = builds.map(b => `
    <tr>
      <td>${platformTag(b.platform)}</td>
      <td><span class="is-size-7">${esc(b.arch)}</span></td>
      <td><code class="is-size-7">${esc(b.version || '')}</code></td>
      <td><span class="is-size-7">${esc(b.build_type)}</span></td>
      <td><span class="is-size-7">${esc(b.link)}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(b.size_bytes)}</span></td>
      <td>${releaseTag(b.release_tag)}</td>
      <td><span class="is-size-7 has-text-grey-light">${fmtDate(b.published_at)}</span></td>
      <td>
        <a class="button is-small is-link is-outlined" href="${b.archive_url && b.archive_url.startsWith('/') ? esc(b.archive_url) : '#'}" title="Download">
          <span class="icon is-small"><i class="fas fa-download"></i></span>
        </a>
      </td>
    </tr>
  `).join('');

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
    const resp = await fetch('/v1/builds?recipe_name=' + encodeURIComponent(name) + '&limit=20',
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('cvcpkg_token') || '') } });
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
    """Encode a Python string as a safe JavaScript string literal."""
    return _json.dumps(s)


def package_detail_html(name: str) -> str:
    """Return the HTML for a package detail page."""
    safe_name = _html.escape(name, quote=True)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html(f"{safe_name} &mdash; cvcpkg")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<!-- Breadcrumb -->
<section class="section pt-4 pb-2 has-background-black-bis">
  <div class="container">
    <nav class="breadcrumb" aria-label="breadcrumbs">
      <ul>
        <li><a href="/" class="has-text-grey-light">Packages</a></li>
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
          <span id="pkg-title">{safe_name}</span>
          <span class="tag is-link is-rounded is-medium ml-3" id="pkg-version">&hellip;</span>
          <span class="tag is-warning is-rounded is-medium ml-2" id="pkg-license" style="display:none"></span>
          <span id="pkg-source-badge" class="ml-2" style="display:none"></span>
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

    <!-- Integration guide -->
    <div class="box has-background-black-ter mt-4">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-terminal"></i></span> Quick Start
      </h3>
      <div class="content">
        <p class="has-text-grey-lighter">Install the pre-built binary:</p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cvcpkg install {safe_name} --prefix /path/to/prefix</pre>

        <p class="has-text-grey-lighter mt-4">Or build from source using the recipe:</p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cvcpkg build {safe_name} --prefix /path/to/prefix</pre>

        <p class="has-text-grey-lighter mt-4">Use in a downstream CMake project:</p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cmake -DCMAKE_PREFIX_PATH=/path/to/prefix ..</pre>
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
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;" id="manual-script">mkdir -p /opt/cvcpkg
# Download {safe_name} and its dependencies, then:
tar --zstd -xf &lt;package&gt;.tar.zst -C /opt/cvcpkg</pre>
        <p class="has-text-grey-lighter mt-3">
          Each package extracts into the same prefix layout (<code>lib/</code>, <code>include/</code>, etc.)
          so they compose correctly. Then point CMake at the prefix:
        </p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cmake -DCMAKE_PREFIX_PATH=/opt/cvcpkg ..</pre>
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

<!-- Recipe viewer -->
<section class="section pt-0 has-background-black-bis" id="recipe-section" style="display:none">
  <div class="container">
    <div class="box has-background-black-ter">
      <h3 class="title is-5 has-text-white collapsible-header" onclick="toggleRecipe()">
        <span class="icon mr-1"><i class="fas fa-file-code"></i></span> Recipe
        <span class="icon is-small ml-2"><i id="recipe-toggle-icon" class="fas fa-chevron-down"></i></span>
        <a class="button is-small is-link is-outlined ml-3" id="recipe-download-link"
           href="/v1/recipe/{safe_name}" download="recipe.yaml" title="Download recipe.yaml"
           onclick="event.stopPropagation()">
          <span class="icon is-small"><i class="fas fa-download"></i></span>
          <span>Download</span>
        </a>
      </h3>
      <div id="recipe-body" style="display:none" class="recipe-viewer">
        <pre class="has-background-dark has-text-light p-3" style="border-radius:6px;"><code id="recipe-content"></code></pre>
      </div>
    </div>
  </div>
</section>

<!-- Available builds -->
<section class="section has-background-black-bis">
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

    <!-- Build Jobs (shown if authenticated) -->
    <div class="box has-background-black-ter mb-5" id="build-jobs-section" style="display:none">
      <h3 class="title is-5 has-text-white">
        <span class="icon mr-1"><i class="fas fa-hard-hat"></i></span> Recent Build Jobs
        <a href="/builds" class="is-size-7 has-text-link ml-3">View all &rarr;</a>
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

{_footer_html()}

<script>
{_HELPERS_JS}
{_DETAIL_JS}

document.addEventListener('DOMContentLoaded', () => {{
  {_NAVBAR_JS}
  document.querySelectorAll('#builds-table th.is-sortable').forEach(th => {{
    th.addEventListener('click', () => sortBy(th.dataset.key));
  }});
  init({_js_string_literal(name)});
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
              <p class="is-size-7 has-text-grey-light">${{esc(o.slug)}}</p>
              ${{o.description ? '<p class="is-size-7 has-text-grey-lighter mt-2">' + esc(o.description) + '</p>' : ''}}
              <p class="is-size-7 has-text-grey mt-2">
                Storage: ${{fmtSize(o.storage_used_bytes)}} / ${{fmtSize(o.storage_limit_bytes)}}
              </p>
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
    import json as _json

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
            <h1 class="title is-2 has-text-white" id="org-name">{safe_slug}</h1>
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
            <div class="column has-text-centered">
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
    const resp = await fetch('/v1/orgs/' + encodeURIComponent({_json.dumps(slug)}));
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
  if (o.description) document.getElementById('org-desc').textContent = o.description;
  if (o.logo_url) {{
    document.getElementById('org-logo').innerHTML =
      '<figure class="image is-64x64"><img src="' + esc(o.logo_url) + '" alt="' + esc(o.display_name || o.slug) + '" style="border-radius:8px"></figure>';
  }}
  if (o.homepage) {{
    const el = document.getElementById('org-homepage');
    el.style.display = '';
    const link = document.getElementById('org-homepage-link');
    link.href = o.homepage;
    link.textContent = o.homepage;
  }}

  document.getElementById('org-storage').textContent = fmtSize(o.storage_used_bytes);
  const pct = o.storage_limit_bytes > 0 ? Math.round(o.storage_used_bytes / o.storage_limit_bytes * 100) : 0;
  document.getElementById('org-storage-bar').value = pct;

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
        return '<tr><td><a href="/package/' + encodeURIComponent(name) + '" class="has-text-link"><strong>' + esc(name) + '</strong></a></td>' +
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
    const href = '/tag/' + encodeURIComponent(t.name) + (t.org_slug ? '?org=' + encodeURIComponent(t.org_slug) : '');
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
      (t.org_slug || '').toLowerCase().includes(q)
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
    import json as _json

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

const TAG_NAME = {_json.dumps(tag_name)};
const TAG_ORG = {_json.dumps(org_slug)};

async function init() {{
  // Load tag metadata (if curated)
  try {{
    const qs = TAG_ORG ? '?org=' + encodeURIComponent(TAG_ORG) : '';
    const resp = await fetch('/v1/tags/all');
    const data = await resp.json();
    const tags = data.tags || [];
    const match = tags.find(t => t.name === TAG_NAME && (t.org_slug || '') === TAG_ORG);
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

  // Load packages with this tag
  try {{
    const resp = await fetch('/v1/packages?limit=1000&search=' + encodeURIComponent(TAG_NAME));
    const data = await resp.json();
    const pkgs = (data.packages || []).filter(p => {{
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
    <h1 class="title has-text-white">
      <span class="icon mr-2"><i class="fas fa-server"></i></span> Builders
    </h1>
    <p class="subtitle has-text-grey-light" id="builder-summary">Loading&hellip;</p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>Enter your API token to view builders.</p>
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

{_footer_html()}

<script>
{_NAVBAR_JS}
{_HELPERS_JS}
{_BUILD_HELPERS_JS}

function saveToken() {{
  const t = document.getElementById('token-input').value.trim();
  if (t) {{ localStorage.setItem('cvcpkg_token', t); location.reload(); }}
}}

async function loadBuilders() {{
  try {{
    const resp = await fetch('/v1/builders', {{ headers: authHeaders() }});
    if (resp.status === 401 || resp.status === 403) {{
      document.getElementById('auth-prompt').style.display = '';
      document.getElementById('builder-summary').textContent = 'Authentication required';
      return;
    }}
    if (!resp.ok) {{ document.getElementById('builder-summary').textContent = 'Error loading builders'; return; }}
    const data = await resp.json();
    const builders = data.builders || [];
    document.getElementById('builder-summary').textContent =
      builders.length + ' builder(s) registered';

    const grid = document.getElementById('builders-grid');
    if (builders.length === 0) {{
      grid.innerHTML = '<div class="column"><div class="notification is-dark">No builders registered.</div></div>';
      return;
    }}

    grid.innerHTML = builders.map(b => {{
      const online = b.status === 'online';
      const statusIcon = online
        ? '<span class="tag is-success is-rounded"><i class="fas fa-circle mr-1"></i>Online</span>'
        : '<span class="tag is-danger is-rounded"><i class="fas fa-circle mr-1"></i>' + esc(b.status) + '</span>';
      const hb = b.last_heartbeat ? fmtRelative(b.last_heartbeat) : 'never';
      return '<div class="column is-6-tablet is-4-desktop">' +
        '<div class="box has-background-black-ter">' +
        '<div class="level mb-2"><div class="level-left"><div class="level-item">' +
        '<h3 class="title is-5 has-text-white mb-0">' +
        '<span class="icon mr-1"><i class="fas fa-microchip"></i></span>' + esc(b.name) + '</h3>' +
        '</div></div><div class="level-right"><div class="level-item">' + statusIcon + '</div></div></div>' +
        '<div class="content is-small">' +
        '<p>' + platformTag(b.platform) + ' <span class="has-text-grey-light">' + esc(b.arch) + '</span></p>' +
        '<p><strong class="has-text-grey-lighter">Jobs:</strong> ' +
        '<span class="has-text-white">' + b.current_jobs + '</span> / ' + b.max_jobs + '</p>' +
        '<p><strong class="has-text-grey-lighter">Heartbeat:</strong> ' +
        '<span class="has-text-grey-light">' + esc(hb) + '</span></p>' +
        '<p><strong class="has-text-grey-lighter">Registered:</strong> ' +
        '<span class="has-text-grey-light">' + fmtDate(b.created_at) + '</span></p>' +
        (b.labels && b.labels.length
          ? '<p>' + b.labels.map(l => '<span class="tag is-dark is-rounded mr-1">' + esc(l) + '</span>').join('') + '</p>'
          : '') +
        '<p class="mt-2"><a href="/builds?builder_id=' + b.id + '" class="button is-small is-dark is-outlined">' +
        '<span class="icon is-small"><i class="fas fa-list"></i></span><span>View builds</span></a></p>' +
        '</div></div></div>';
    }}).join('');
  }} catch (e) {{
    document.getElementById('builder-summary').textContent = 'Error: ' + e.message;
  }}
}}

async function init() {{
  await loadBuilders();
  setInterval(loadBuilders, 30000);
}}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


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
    <h1 class="title has-text-white">
      <span class="icon mr-2"><i class="fas fa-hammer"></i></span> Build Jobs
    </h1>
    <p class="subtitle has-text-grey-light" id="builds-summary">Loading&hellip;</p>

    <div class="notification is-dark" id="auth-prompt" style="display:none">
      <p>Enter your API token to view build jobs.</p>
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
            <option value="darwin">macOS</option>
            <option value="windows">Windows</option>
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

let currentOffset = 0;
const PAGE_SIZE = 50;
let totalJobs = 0;
let _refreshTimer = null;

function saveToken() {{
  const t = document.getElementById('token-input').value.trim();
  if (t) {{ localStorage.setItem('cvcpkg_token', t); location.reload(); }}
}}

function prevPage() {{ if (currentOffset >= PAGE_SIZE) {{ currentOffset -= PAGE_SIZE; loadJobs(); }} }}
function nextPage() {{ if (currentOffset + PAGE_SIZE < totalJobs) {{ currentOffset += PAGE_SIZE; loadJobs(); }} }}

async function loadJobs(resetOffset) {{
  if (resetOffset) currentOffset = 0;
  const status = document.getElementById('filter-status').value;
  const platform = document.getElementById('filter-platform').value;
  const recipe = document.getElementById('filter-recipe').value.trim();
  const dagId = document.getElementById('filter-dag').value.trim();
  const builderId = document.getElementById('filter-builder').value.trim();

  // Sync URL query string
  const qp = new URLSearchParams();
  if (status) qp.set('status', status);
  if (platform) qp.set('platform', platform);
  if (recipe) qp.set('recipe_name', recipe);
  if (dagId) qp.set('dag_id', dagId);
  if (builderId) qp.set('builder_id', builderId);
  const qs = qp.toString();
  history.replaceState(null, '', qs ? '?' + qs : location.pathname);

  let url = '/v1/builds?limit=' + PAGE_SIZE + '&offset=' + currentOffset;
  if (status) url += '&status=' + encodeURIComponent(status);
  if (platform) url += '&platform=' + encodeURIComponent(platform);
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
      return '<tr>' +
        '<td class="has-text-grey-light">' + j.id + '</td>' +
        '<td><a href="/build/' + j.id + '"><span class="tag ' + statusCls(j.status) + ' is-rounded"' + errTip + '>' + esc(j.status) + '</span></a></td>' +
        '<td><a href="/package/' + encodeURIComponent(j.recipe_name) + '" class="has-text-link">' + esc(j.recipe_name) + '</a></td>' +
        '<td>' + platformTag(j.platform) + '</td>' +
        '<td class="has-text-grey-light">' + esc(j.arch) + '</td>' +
        '<td class="has-text-grey-light">' + esc(j.config) + '</td>' +
        '<td class="has-text-grey-light">' + esc(j.link) + '</td>' +
        '<td class="has-text-grey-light">' + esc(builderName) + '</td>' +
        '<td class="has-text-grey-light">' + fmtDuration(j.started_at, j.finished_at) + '</td>' +
        '<td>' + dagLink + '</td>' +
        '<td class="has-text-grey-light is-size-7">' + fmtDate(j.submitted_at) + '</td>' +
        '<td>' + logLink + '</td>' +
        '</tr>';
    }}).join('');
    }} // end else (jobs.length > 0)

    // Auto-refresh when active (running/dispatched/pending) builds exist
    const hasActive = jobs.some(j => ['running', 'dispatched', 'pending'].includes(j.status));
    if (hasActive && !_refreshTimer) {{
      _refreshTimer = setInterval(() => loadJobs(), 15000);
    }} else if (!hasActive && _refreshTimer) {{
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
  const builderParam = params.get('builder_id');
  if (builderParam) document.getElementById('filter-builder').value = builderParam;
  await loadJobs();
}}
document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


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
      <p>Enter your API token to view build details.</p>
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

const JOB_ID = {job_id};

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
    'Build #' + j.id + ' &mdash; <a href="/package/' + encodeURIComponent(j.recipe_name) + '" class="has-text-link">' + esc(j.recipe_name) + '</a>';
  document.getElementById('meta-recipe').innerHTML =
    '<a href="/package/' + encodeURIComponent(j.recipe_name) + '" class="has-text-link">' + esc(j.recipe_name) + '</a>';
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

async function loadLog(isLive) {{
  const output = document.getElementById('log-output');
  const liveTag = document.getElementById('log-live');
  const dlBtn = document.getElementById('log-download');

  if (isLive) {{
    liveTag.style.display = '';
    try {{
      const es = new EventSource('/v1/builds/' + JOB_ID + '/log/stream?token=' +
        encodeURIComponent(localStorage.getItem('cvcpkg_token') || ''));
      es.onmessage = (e) => {{
        output.textContent += e.data + '\\n';
        output.scrollTop = output.scrollHeight;
      }};
      es.addEventListener('done', (e) => {{
        es.close();
        liveTag.style.display = 'none';
        dlBtn.style.display = '';
        // Reload metadata to get final status
        loadMeta();
      }});
      es.onerror = () => {{
        es.close();
        liveTag.style.display = 'none';
        // Fall back to static log
        loadStaticLog();
      }};
    }} catch (_) {{
      loadStaticLog();
    }}
  }} else {{
    loadStaticLog();
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
  populateMeta(job);

  const isLive = job.status === 'running' || job.status === 'dispatched';
  await loadLog(isLive);
}}
document.addEventListener('DOMContentLoaded', init);
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
      <p>Enter your API token to view recipes.</p>
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
    const resp = await fetch('/v1/recipes', {{ headers: authHeaders() }});
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
    allRecipes = data.recipes || [];
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
        <li><a href="#requirements">Requirements Files</a></li>
        <li><a href="#commands">CLI Reference</a></li>
        <li><a href="#cmake">CMake Integration</a></li>
        <li><a href="#recipes">Creating Recipes</a></li>
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
        <p class="has-text-grey-lighter mb-2">Install from PyPI:</p>
        <div class="guide-code"><pre><code>pip install cvcpkg</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">Or install from the repository:</p>
        <div class="guide-code"><pre><code>git clone https://github.com/{repo}.git
cd libcvc-deps/tools/cvcpkg
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

    <!-- Requirements Files -->
    <div id="requirements" class="guide-section" style="counter-reset: guide-step;">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-file-alt"></i></span>
        Requirements Files
      </h2>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Create a <code>cvc-requirements.yaml</code> to declare your
          dependencies:
        </p>
        <div class="guide-code"><pre><code># cvc-requirements.yaml
components:
  - name: boost
    version: "&gt;=1.86"
  - name: hdf5
    version: "^1.14"
  - name: qt6
    version: "~6.8"
  - name: vtk
    version: "^9.5"

config: release
link: shared</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Install everything from the requirements file:
        </p>
        <div class="guide-code"><pre><code>\
cvcpkg install --from cvc-requirements.yaml --prefix ./deps</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Lock versions for reproducible builds:
        </p>
        <div class="guide-code"><pre><code>cvcpkg lock     # creates cvc-lock.yaml
cvcpkg sync     # installs exactly what's in the lockfile</code></pre></div>
      </div>
    </div>

    <!-- CLI Commands -->
    <div id="commands" class="guide-section">
      <h2 class="title is-4 has-text-white">
        <span class="icon mr-1"><i class="fas fa-terminal"></i></span>
        CLI Reference
      </h2>
      <div class="table-container">
        <table class="table is-fullwidth is-hoverable is-dark is-striped">
          <thead>
            <tr>
              <th>Command</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr><td><code>cvcpkg install</code></td>
                <td>Install packages into a prefix</td></tr>
            <tr><td><code>cvcpkg list</code></td>
                <td>List installed packages</td></tr>
            <tr><td><code>cvcpkg info &lt;name&gt;</code></td>
                <td>Show package details and dependencies</td></tr>
            <tr><td><code>cvcpkg add &lt;name&gt;</code></td>
                <td>Add a component to requirements</td></tr>
            <tr><td><code>cvcpkg remove &lt;name&gt;</code></td>
                <td>Remove a component from requirements</td></tr>
            <tr><td><code>cvcpkg lock</code></td>
                <td>Lock dependency versions</td></tr>
            <tr><td><code>cvcpkg sync</code></td>
                <td>Install from lockfile</td></tr>
            <tr><td><code>cvcpkg catalog</code></td>
                <td>Browse or refresh the package catalog</td></tr>
            <tr><td><code>cvcpkg verify</code></td>
                <td>Verify integrity of installed packages</td></tr>
            <tr><td><code>cvcpkg validate</code></td>
                <td>Validate recipe files</td></tr>
            <tr><td><code>cvcpkg gc</code></td>
                <td>Remove unused cached downloads</td></tr>
            <tr><td><code>cvcpkg publish</code></td>
                <td>Publish archives to a cvcpkg server</td></tr>
            <tr><td><code>cvcpkg world</code></td>
                <td>Show the dependency world set</td></tr>
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
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Create an organization:
        </p>
        <div class="guide-code"><pre><code>curl -X POST https://cvcpkg.org/v1/orgs \\
  -H "Authorization: Bearer cvctok_..." \\
  -H "Content-Type: application/json" \\
  -d '{{"slug": "my-team", "display_name": "My Team"}}'</code></pre></div>
      </div>
      <div class="guide-step">
        <p class="has-text-grey-lighter mb-2">
          Add members (requires org owner or admin token):
        </p>
        <div class="guide-code"><pre><code>curl -X POST https://cvcpkg.org/v1/orgs/my-team/members \\
  -H "Authorization: Bearer cvctok_..." \\
  -H "Content-Type: application/json" \\
  -d '{{"token_name": "alice-token", "role": "member"}}'</code></pre></div>
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
        <div class="guide-code"><pre><code>cd tools/cvcpkg
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
cvcpkg builds submit zlib --platform linux --arch x86_64 \\
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
            <tr><td><code>CVCPKG_MAX_UPLOAD_BYTES</code></td>
                <td>1 GiB</td>
                <td>Maximum upload size per file</td></tr>
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
          GitHub link (default: <code>transfix/libcvc-deps</code>).
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
