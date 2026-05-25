"""Landing page HTML for cvcpkg-server.

Serves a self-contained single-page index at ``/`` that displays
published packages, supports sorting and filtering, and links to
the API docs.  Uses Bulma CSS for styling.
"""

from __future__ import annotations

from cvcpkg import __version__

# Minimal overrides on top of Bulma's dark theme
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
"""

_JS = r"""
let allPackages = [];
let currentSort = { key: 'name', dir: 'asc' };
let searchTerm = '';
let platformFilter = '';
let releaseFilter = '';

async function init() {
  try {
    const resp = await fetch('/v1/packages?limit=1000');
    const data = await resp.json();
    allPackages = data.packages || [];
    updateStats();
    render();
  } catch (err) {
    document.getElementById('pkg-body').innerHTML =
      '<tr><td colspan="7" class="has-text-centered has-text-grey-light">Failed to load packages.</td></tr>';
  }
}

function updateStats() {
  const names = new Set(allPackages.map(p => p.name));
  const platforms = new Set(allPackages.map(p => p.platform).filter(Boolean));
  const totalSize = allPackages.reduce((s, p) => s + (p.size_bytes || 0), 0);

  document.getElementById('stat-packages').textContent = names.size;
  document.getElementById('stat-builds').textContent = allPackages.length;
  document.getElementById('stat-platforms').textContent = platforms.size;
  document.getElementById('stat-size').textContent = fmtSizeLarge(totalSize);

  // Populate platform filter
  const sel = document.getElementById('platform-filter');
  const existing = new Set(Array.from(sel.options).map(o => o.value));
  [...platforms].sort().forEach(p => {
    if (!existing.has(p)) {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p;
      sel.appendChild(opt);
    }
  });

  // Populate release filter
  const releases = new Set(allPackages.map(p => p.release_tag).filter(Boolean));
  const relSel = document.getElementById('release-filter');
  const existingRel = new Set(Array.from(relSel.options).map(o => o.value));
  [...releases].sort().reverse().forEach(r => {
    if (!existingRel.has(r)) {
      const opt = document.createElement('option');
      opt.value = r;
      opt.textContent = r;
      relSel.appendChild(opt);
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
      (p.arch || '').toLowerCase().includes(q)
    );
  }
  if (platformFilter) {
    pkgs = pkgs.filter(p => p.platform === platformFilter);
  }
  if (releaseFilter === 'live') {
    pkgs = pkgs.filter(p => !p.release_tag);
  } else if (releaseFilter) {
    pkgs = pkgs.filter(p => p.release_tag === releaseFilter);
  }

  pkgs.sort((a, b) => {
    let va = a[currentSort.key] || '';
    let vb = b[currentSort.key] || '';
    if (currentSort.key === 'size_bytes') {
      va = va || 0; vb = vb || 0;
      return currentSort.dir === 'asc' ? va - vb : vb - va;
    }
    if (currentSort.key === 'published_at') {
      return currentSort.dir === 'asc'
        ? new Date(va) - new Date(vb)
        : new Date(vb) - new Date(va);
    }
    va = String(va).toLowerCase();
    vb = String(vb).toLowerCase();
    if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
    if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
    return 0;
  });

  document.getElementById('pkg-count').textContent =
    pkgs.length + (pkgs.length === 1 ? ' result' : ' results');

  const tbody = document.getElementById('pkg-body');
  if (pkgs.length === 0) {
    const hasFilter = searchTerm || platformFilter || releaseFilter;
    tbody.innerHTML = `
      <tr><td colspan="8">
        <div class="empty-hero has-text-centered">
          <span class="icon is-large has-text-grey-light">
            <i class="fas fa-box-open fa-3x"></i>
          </span>
          <p class="title is-5 has-text-grey-light mt-4">
            ${hasFilter ? 'No packages match your filter' : 'No packages published yet'}
          </p>
          <p class="subtitle is-6 has-text-grey">
            ${hasFilter
              ? 'Try adjusting your search or platform filter.'
              : 'Publish your first package to see it here.'}
          </p>
          ${!hasFilter
            ? '<pre class="has-background-dark has-text-success p-3 mt-3" style="display:inline-block;border-radius:6px;">cvcpkg publish --server https://pkg.tx.wtf &lt;archive&gt;</pre>'
            : ''}
        </div>
      </td></tr>`;
    return;
  }

  tbody.innerHTML = pkgs.map(p => `
    <tr>
      <td>
        <strong class="has-text-link">${esc(p.name)}</strong>
        ${p.yanked ? '<span class="tag is-danger is-light is-small ml-2">yanked</span>' : ''}
      </td>
      <td><code>${esc(p.version)}</code></td>
      <td>${platformTag(p.platform)}</td>
      <td><span class="is-size-7">${esc(p.arch)}</span></td>
      <td><span class="is-size-7">${esc(p.build_type)}/${esc(p.link)}</span></td>
      <td><span class="is-family-monospace is-size-7 has-text-grey-light">${fmtSize(p.size_bytes)}</span></td>
      <td>${releaseTag(p.release_tag)}</td>
      <td><span class="is-size-7 has-text-grey-light">${fmtDate(p.published_at)}</span></td>
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

function platformTag(platform) {
  if (!platform) return '<span class="has-text-grey">\u2014</span>';
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
  if (!bytes) return '\u2014';
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
  if (!iso) return '\u2014';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function esc(s) {
  if (s == null) return '';
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(s)));
  return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', () => {
  const burger = document.querySelector('.navbar-burger');
  if (burger) {
    burger.addEventListener('click', () => {
      burger.classList.toggle('is-active');
      document.getElementById(burger.dataset.target).classList.toggle('is-active');
    });
  }

  document.getElementById('search').addEventListener('input', e => {
    searchTerm = e.target.value;
    render();
  });

  document.getElementById('platform-filter').addEventListener('change', e => {
    platformFilter = e.target.value;
    render();
  });

  document.getElementById('release-filter').addEventListener('change', e => {
    releaseFilter = e.target.value;
    render();
  });

  document.querySelectorAll('th.is-sortable').forEach(th => {
    th.addEventListener('click', () => sortBy(th.dataset.key));
  });

  init();
});
"""


def landing_html() -> str:
    """Return the complete HTML for the landing page."""
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" class="has-background-black-bis">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>cvcpkg \u2014 Package Archive</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css" />
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"
        integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA=="
        crossorigin="anonymous" referrerpolicy="no-referrer" />
  <style>{_CSS}</style>
</head>
<body class="has-background-black-bis has-text-light">

<!-- Navbar -->
<nav class="navbar is-dark" role="navigation" aria-label="main navigation">
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
        <span aria-hidden="true"></span>
      </a>
    </div>
    <div id="navMenu" class="navbar-menu">
      <div class="navbar-end">
        <a class="navbar-item" href="/docs">
          <span class="icon"><i class="fas fa-book"></i></span>
          <span>API Docs</span>
        </a>
        <a class="navbar-item" href="/v1/catalog">
          <span class="icon"><i class="fas fa-list"></i></span>
          <span>Catalog</span>
        </a>
        <a class="navbar-item" href="https://github.com/transfix/libcvc-deps">
          <span class="icon"><i class="fab fa-github"></i></span>
          <span>GitHub</span>
        </a>
      </div>
    </div>
  </div>
</nav>

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
        macOS, and Windows \u2014 with curated LTS releases for reproducible
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
          <p class="title is-3 has-text-link" id="stat-packages">\u2014</p>
          <p class="heading has-text-grey-light">Packages</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-info" id="stat-builds">\u2014</p>
          <p class="heading has-text-grey-light">Builds</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-success" id="stat-platforms">\u2014</p>
          <p class="heading has-text-grey-light">Platforms</p>
        </div>
      </div>
      <div class="column is-3-desktop is-6-mobile">
        <div class="stat-box">
          <p class="title is-3 has-text-warning" id="stat-size">\u2014</p>
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
                   placeholder="Search packages..." />
            <span class="icon is-left">
              <i class="fas fa-search"></i>
            </span>
          </div>
        </div>
      </div>
      <div class="column is-3">
        <div class="field">
          <div class="control">
            <div class="select is-dark is-fullwidth">
              <select id="platform-filter">
                <option value="">All platforms</option>
              </select>
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
            <th class="is-sortable" data-key="name">Name <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="version">Version <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="platform">Platform <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="arch">Arch <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="build_type">Build <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="size_bytes">Size <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="release_tag">Release <span class="sort-arrow"></span></th>
            <th class="is-sortable" data-key="published_at">Published <span class="sort-arrow"></span></th>
          </tr>
        </thead>
        <tbody id="pkg-body">
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

<!-- Footer -->
<footer class="footer has-background-black-ter has-text-grey-light">
  <div class="content has-text-centered">
    <p>
      <a href="https://github.com/transfix/libcvc-deps" class="has-text-grey-light">
        <span class="icon"><i class="fab fa-github"></i></span> cvcpkg
      </a>
      \u2014 cross-platform binary package archive for scientific computing
    </p>
  </div>
</footer>

<script>{_JS}</script>
</body>
</html>"""
