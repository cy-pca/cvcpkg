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

.release-tag { font-size: 0.75em; }
.release-tag.is-release { background-color: rgba(72, 199, 116, 0.15); color: #48c774; }
.release-tag.is-live { background-color: rgba(255, 221, 87, 0.15); color: #ffdd57; }

.empty-hero { padding: 4rem 1rem; }
.footer { padding: 2rem 1.5rem; }
.pkg-card { transition: transform 0.1s; }
.pkg-card:hover { transform: translateY(-2px); }
a.pkg-link { color: #3273dc; text-decoration: none; }
a.pkg-link:hover { text-decoration: underline; }
"""

# ── Shared HTML fragments ────────────────────────────────────────


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
        <span aria-hidden="true"></span><span aria-hidden="true"></span>
        <span aria-hidden="true"></span><span aria-hidden="true"></span>
      </a>
    </div>
    <div id="navMenu" class="navbar-menu">
      <div class="navbar-end">
        <a class="navbar-item" href="/docs">
          <span class="icon"><i class="fas fa-book"></i></span><span>API Docs</span>
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
  return div.innerHTML;
}
function platformTag(platform) {
  if (!platform) return '<span class="has-text-grey">&mdash;</span>';
  let cls = '';
  const lp = platform.toLowerCase();
  if (lp.includes('linux')) cls = 'linux';
  else if (lp.includes('darwin') || lp.includes('macos')) cls = 'darwin';
  else if (lp.includes('win')) cls = 'windows';
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
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}
"""

# ── Landing page JS ─────────────────────────────────────────────

_LANDING_JS = r"""
let allPackages = [];
let currentSort = { key: 'name', dir: 'asc' };
let searchTerm = '';
let platformFilter = '';
let releaseFilter = '';

let recipeMeta = {};

async function init() {
  try {
    const resp = await fetch('/v1/packages?limit=1000');
    const data = await resp.json();
    allPackages = data.packages || [];
  } catch (err) {
    document.getElementById('pkg-body').innerHTML =
      '<tr><td colspan="6" class="has-text-centered has-text-grey-light">Failed to load packages.</td></tr>';
    return;
  }
  // Fetch recipe metadata for license/description fallback
  try {
    const dresp = await fetch('/v1/deps');
    const ddata = await dresp.json();
    recipeMeta = ddata.meta || {};
    // Enrich packages with recipe metadata when DB fields are empty
    allPackages.forEach(p => {
      const m = recipeMeta[p.name];
      if (!m) return;
      if (!p.license && m.license) p.license = m.license;
      if (!p.description && m.description) p.description = m.description;
    });
  } catch (_) {}
  updateStats();
  render();
}

function updateStats() {
  const names = new Set(allPackages.map(p => p.name));
  const platforms = new Set(allPackages.map(p => p.platform).filter(Boolean));
  const totalSize = allPackages.reduce((s, p) => s + (p.size_bytes || 0), 0);

  document.getElementById('stat-packages').textContent = names.size;
  document.getElementById('stat-builds').textContent = allPackages.length;
  document.getElementById('stat-platforms').textContent = platforms.size;
  document.getElementById('stat-size').textContent = fmtSizeLarge(totalSize);

  const sel = document.getElementById('platform-filter');
  const existing = new Set(Array.from(sel.options).map(o => o.value));
  [...platforms].sort().forEach(p => {
    if (!existing.has(p)) {
      const opt = document.createElement('option');
      opt.value = p; opt.textContent = p; sel.appendChild(opt);
    }
  });

  const releases = new Set(allPackages.map(p => p.release_tag).filter(Boolean));
  const relSel = document.getElementById('release-filter');
  const existingRel = new Set(Array.from(relSel.options).map(o => o.value));
  [...releases].sort().reverse().forEach(r => {
    if (!existingRel.has(r)) {
      const opt = document.createElement('option');
      opt.value = r; opt.textContent = r; relSel.appendChild(opt);
    }
  });
}

function render() {
  let pkgs = [...allPackages];

  if (searchTerm) {
    const q = searchTerm.toLowerCase();
    pkgs = pkgs.filter(p =>
      p.name.toLowerCase().includes(q) ||
      p.version.toLowerCase().includes(q) ||
      (p.platform || '').toLowerCase().includes(q) ||
      (p.arch || '').toLowerCase().includes(q) ||
      (p.build_type || '').toLowerCase().includes(q) ||
      (p.link || '').toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q) ||
      (p.tags || '').toLowerCase().includes(q) ||
      (p.maintainer || '').toLowerCase().includes(q) ||
      (p.license || '').toLowerCase().includes(q) ||
      (p.release_tag || '').toLowerCase().includes(q)
    );
  }
  if (platformFilter) pkgs = pkgs.filter(p => p.platform === platformFilter);
  if (releaseFilter === 'live') pkgs = pkgs.filter(p => !p.release_tag);
  else if (releaseFilter) pkgs = pkgs.filter(p => p.release_tag === releaseFilter);

  // Group by package name
  const groups = {};
  pkgs.forEach(p => {
    if (!groups[p.name]) {
      groups[p.name] = {
        name: p.name,
        version: p.version,
        description: p.description || '',
        license: p.license || '',
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

  let sorted = Object.values(groups);
  sorted.sort((a, b) => {
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

  document.getElementById('pkg-count').textContent =
    sorted.length + (sorted.length === 1 ? ' package' : ' packages') +
    ' (' + pkgs.length + (pkgs.length === 1 ? ' build' : ' builds') + ')';

  const tbody = document.getElementById('pkg-body');
  if (sorted.length === 0) {
    const hasFilter = searchTerm || platformFilter || releaseFilter;
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
    return;
  }

  tbody.innerHTML = sorted.map(g => `
    <tr class="pkg-card">
      <td>
        <a class="pkg-link" href="/package/${encodeURIComponent(g.name)}">
          <strong>${esc(g.name)}</strong>
        </a>
        ${g.description ? '<br><span class="is-size-7 has-text-grey-light">' + esc(g.description) + '</span>' : ''}
      </td>
      <td><code>${esc(g.version)}</code></td>
      <td>${[...g.platforms].sort().map(p => platformTag(p)).join(' ')}</td>
      <td><span class="tag is-dark is-rounded">${g.builds.length}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(g.totalSize)}</span></td>
      <td>
        ${g.license ? '<span class="tag is-dark is-rounded is-small">' + esc(g.license) + '</span>' : '<span class="has-text-grey">&mdash;</span>'}
      </td>
    </tr>
  `).join('');

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
}

function sortBy(key) {
  if (currentSort.key === key) {
    currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    currentSort = { key, dir: 'asc' };
  }
  render();
}
"""


def landing_html() -> str:
    """Return the complete HTML for the landing page."""
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
{_head_html("cvcpkg &mdash; Package Archive")}
<body class="has-background-black-bis has-text-light">

{_navbar_html()}

<!-- Hero -->
<section class="hero hero-gradient is-medium">
  <div class="hero-body">
    <div class="container">
      <p class="title is-2 has-text-white">
        <span class="icon is-large mr-2"><i class="fas fa-cubes"></i></span>
        cvcpkg
      </p>
      <p class="subtitle is-5 has-text-grey-lighter" style="max-width: 740px;">
        A cross-platform, language-agnostic binary package archive for the
        scientific computing community. Pre-built C/C++ libraries for Linux,
        macOS, and Windows &mdash; with curated LTS releases for reproducible
        downstream builds.
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

<!-- Package index -->
<section class="section has-background-black-bis">
  <div class="container">
    <div class="columns is-vcentered mb-4">
      <div class="column is-5">
        <div class="field">
          <div class="control has-icons-left">
            <input class="input is-dark" type="text" id="search"
                   placeholder="Search packages by name, platform, license, tags..." />
            <span class="icon is-left"><i class="fas fa-search"></i></span>
          </div>
        </div>
      </div>
      <div class="column is-3">
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
                <option value="live">Live only</option>
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
            <th class="is-sortable" data-key="license">License <span class="sort-arrow"></span></th>
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
  </div>
</section>

{_footer_html()}

<script>
{_HELPERS_JS}
{_LANDING_JS}

document.addEventListener('DOMContentLoaded', () => {{
  const burger = document.querySelector('.navbar-burger');
  if (burger) {{
    burger.addEventListener('click', () => {{
      burger.classList.toggle('is-active');
      document.getElementById(burger.dataset.target).classList.toggle('is-active');
    }});
  }}

  document.getElementById('search').addEventListener('input', e => {{
    searchTerm = e.target.value;
    render();
  }});
  document.getElementById('platform-filter').addEventListener('change', e => {{
    platformFilter = e.target.value;
    render();
  }});
  document.getElementById('release-filter').addEventListener('change', e => {{
    releaseFilter = e.target.value;
    render();
  }});
  document.querySelectorAll('th.is-sortable').forEach(th => {{
    th.addEventListener('click', () => sortBy(th.dataset.key));
  }});

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
      '<tr><td colspan="8" class="has-text-centered has-text-grey-light">Failed to load package data.</td></tr>';
  }
  // Fetch dependency graph and recipe metadata
  try {
    const dresp = await fetch('/v1/deps');
    const ddata = await dresp.json();
    renderDeps(ddata.forward || {}, ddata.reverse || {}, ddata.meta || {});
  } catch (_) {}
}

function renderDeps(forward, reverse, meta) {
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
    const plat = firstBuild.platform;
    const arch = firstBuild.arch;

    // Group builds by platform
    const byPlatform = {};
    allBuilds.forEach(b => {
      const key = b.platform + '/' + b.arch;
      if (!byPlatform[key]) byPlatform[key] = [];
      byPlatform[key].push(b);
    });

    const depNames = [...allDeps].sort();
    let lines = ['mkdir -p /opt/cvcpkg', ''];
    if (depNames.length > 0) {
      lines.push('# Download dependencies first:');
      depNames.forEach(d => {
        lines.push('# ' + d + ': https://pkg.tx.wtf/package/' + encodeURIComponent(d));
      });
      lines.push('');
    }
    lines.push('# Download and extract ' + pkgName + ':');
    const url = 'https://pkg.tx.wtf' + firstBuild.archive_url;
    const fname = firstBuild.archive_url.split('/').pop();
    lines.push('curl -LO ' + url);
    lines.push('tar --zstd -xf ' + fname + ' -C /opt/cvcpkg');
    lines.push('');
    lines.push('# Point CMake at the prefix:');
    lines.push('cmake -DCMAKE_PREFIX_PATH=/opt/cvcpkg ..');

    scriptEl.textContent = lines.join('\n');
    manualEl.style.display = '';
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
      <td><span class="is-size-7">${esc(b.build_type)}</span></td>
      <td><span class="is-size-7">${esc(b.link)}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(b.size_bytes)}</span></td>
      <td>${releaseTag(b.release_tag)}</td>
      <td><span class="is-size-7 has-text-grey-light">${fmtDate(b.published_at)}</span></td>
      <td>
        <a class="button is-small is-link is-outlined" href="${esc(b.archive_url)}" title="Download">
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
        </h1>
        <p class="subtitle is-5 has-text-grey-lighter" id="pkg-description" style="display:none"></p>

        <div class="content">
          <div style="display:none"><strong class="has-text-grey-light">Homepage:</strong>
            <a id="pkg-homepage" href="#" class="has-text-link" target="_blank" rel="noopener noreferrer"></a>
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
        <p class="has-text-grey-lighter">Install the pre-built binary from pkg.tx.wtf:</p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cvcpkg install {safe_name} --server https://pkg.tx.wtf</pre>

        <p class="has-text-grey-lighter mt-4">Or build from source using the recipe:</p>
        <pre class="has-background-dark has-text-success p-3" style="border-radius:6px;">cvcpkg build recipes/{safe_name} --prefix /path/to/prefix</pre>

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

<!-- Available builds -->
<section class="section has-background-black-bis">
  <div class="container">
    <h2 class="title is-4 has-text-white mb-4">
      <span class="icon mr-1"><i class="fas fa-box"></i></span> Available Builds
    </h2>

    <div class="table-container" id="builds-table">
      <table class="table is-fullwidth is-hoverable is-dark is-striped">
        <thead>
          <tr>
            <th class="is-sortable" data-key="platform">Platform <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="arch">Arch <span class="sort-arrow"></span></th>
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
            <td colspan="8" class="has-text-centered py-6">
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
  const burger = document.querySelector('.navbar-burger');
  if (burger) {{
    burger.addEventListener('click', () => {{
      burger.classList.toggle('is-active');
      document.getElementById(burger.dataset.target).classList.toggle('is-active');
    }});
  }}
  document.querySelectorAll('#builds-table th.is-sortable').forEach(th => {{
    th.addEventListener('click', () => sortBy(th.dataset.key));
  }});
  init({_js_string_literal(name)});
}});
</script>
</body>
</html>"""


# ── Shared HTML fragments ────────────────────────────────────────


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


def _navbar_html() -> str:
    return f"""<nav class="navbar is-dark" role="navigation" aria-label="main navigation">
  <div class="container">
    <div class="navbar-brand">
      <a class="navbar-item" href="/">
        <span class="logo-icon">C</span>
        <strong class="is-size-4">cvcpkg</strong>
        <span class="tag is-dark is-rounded ml-2">v{__version__}</span>
      </a>
    </div>
    <div id="navMenu" class="navbar-menu">
      <div class="navbar-end">
        <a class="navbar-item" href="/orgs">
          <span class="icon"><i class="fas fa-building"></i></span><span>Organizations</span>
        </a>
        <a class="navbar-item" href="/docs">
          <span class="icon"><i class="fas fa-book"></i></span><span>API Docs</span>
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


def _footer_frag() -> str:
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

{_footer_frag()}

<script>
{_HELPERS_JS}

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

{_footer_frag()}

<script>
{_HELPERS_JS}

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
      '<figure class="image is-64x64"><img src="' + esc(o.logo_url) + '" style="border-radius:8px"></figure>';
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
        return '<tr><td><strong class="has-text-link">' + esc(name) + '</strong></td>' +
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
