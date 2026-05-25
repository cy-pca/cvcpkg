"""Landing page HTML for cvcpkg-server.

Serves a self-contained single-page index at ``/`` that displays
published packages, supports sorting and filtering, and links to
the API docs.
"""

from __future__ import annotations

from cvcpkg import __version__

_CSS = r"""
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --accent: #58a6ff;
  --accent-hover: #79c0ff;
  --green: #3fb950;
  --yellow: #d29922;
  --red: #f85149;
  --radius: 8px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
}

a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); text-decoration: underline; }

.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }

/* ── Header ─── */
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 20px 0;
}

.header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-icon {
  width: 40px; height: 40px;
  background: linear-gradient(135deg, var(--accent), var(--green));
  border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: 700; color: var(--bg);
}

.logo h1 {
  font-size: 24px;
  font-weight: 600;
}

.logo .version {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: 400;
  margin-left: 4px;
}

.header-links {
  display: flex;
  gap: 20px;
  font-size: 14px;
}

/* ── Stats bar ─── */
.stats-bar {
  display: flex;
  gap: 24px;
  padding: 16px 0;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--accent);
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ── Description section ─── */
.description {
  padding: 24px 0;
  border-bottom: 1px solid var(--border);
}

.description p {
  color: var(--text-muted);
  font-size: 15px;
  max-width: 800px;
  line-height: 1.7;
}

.description p strong { color: var(--text); }

/* ── Controls ─── */
.controls {
  display: flex;
  gap: 12px;
  padding: 20px 0;
  flex-wrap: wrap;
  align-items: center;
}

.search-box {
  flex: 1;
  min-width: 200px;
  max-width: 400px;
  position: relative;
}

.search-box input {
  width: 100%;
  padding: 8px 12px 8px 36px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.search-box input:focus { border-color: var(--accent); }

.search-box::before {
  content: "⌕";
  position: absolute;
  left: 10px; top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  font-size: 16px;
  pointer-events: none;
}

select, .btn {
  padding: 8px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 14px;
  cursor: pointer;
  outline: none;
  transition: border-color 0.2s;
}

select:focus, .btn:focus { border-color: var(--accent); }
select:hover, .btn:hover { border-color: var(--text-muted); }

.sort-group {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

/* ── Package table ─── */
.pkg-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 40px;
}

.pkg-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  border-bottom: 2px solid var(--border);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}

.pkg-table th:hover { color: var(--accent); }
.pkg-table th.sorted { color: var(--accent); }
.pkg-table th .arrow { margin-left: 4px; font-size: 10px; }

.pkg-table td {
  padding: 12px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
  vertical-align: middle;
}

.pkg-table tr:hover td { background: rgba(88, 166, 255, 0.04); }

.pkg-name {
  font-weight: 600;
  color: var(--accent);
}

.pkg-version {
  font-family: "SFMono-Regular", "Cascadia Code", "Fira Code", monospace;
  font-size: 13px;
  background: rgba(88, 166, 255, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}

.pkg-platform {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.platform-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;
}

.platform-badge.linux { background: rgba(63, 185, 80, 0.15); color: var(--green); }
.platform-badge.darwin, .platform-badge.macos { background: rgba(210, 153, 34, 0.15); color: var(--yellow); }
.platform-badge.windows, .platform-badge.win64 { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
.platform-badge.other { background: rgba(139, 148, 158, 0.15); color: var(--text-muted); }

.badge-yanked {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(248, 81, 73, 0.15);
  color: var(--red);
  font-weight: 500;
  margin-left: 6px;
}

.pkg-size {
  font-family: monospace;
  color: var(--text-muted);
  font-size: 13px;
}

.pkg-date {
  color: var(--text-muted);
  font-size: 13px;
  white-space: nowrap;
}

/* ── Empty state ─── */
.empty-state {
  text-align: center;
  padding: 80px 20px;
  color: var(--text-muted);
}

.empty-state .icon { font-size: 48px; margin-bottom: 16px; }
.empty-state h2 { font-size: 20px; color: var(--text); margin-bottom: 8px; }
.empty-state p { max-width: 500px; margin: 0 auto; font-size: 14px; }
.empty-state code {
  display: inline-block;
  margin-top: 16px;
  padding: 8px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--green);
}

/* ── Footer ─── */
footer {
  border-top: 1px solid var(--border);
  padding: 24px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

footer a { color: var(--text-muted); }
footer a:hover { color: var(--accent); }

/* ── Loading ─── */
.loading {
  text-align: center;
  padding: 40px;
  color: var(--text-muted);
}

.spinner {
  display: inline-block;
  width: 24px; height: 24px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* ── Responsive ─── */
@media (max-width: 768px) {
  .pkg-table th:nth-child(n+5),
  .pkg-table td:nth-child(n+5) { display: none; }
  .header-inner { flex-direction: column; align-items: flex-start; }
}
"""

_JS = r"""
let allPackages = [];
let currentSort = { key: 'name', dir: 'asc' };
let searchTerm = '';
let platformFilter = '';

async function init() {
  try {
    const resp = await fetch('/v1/packages?limit=1000');
    const data = await resp.json();
    allPackages = data.packages || [];
    updateStats();
    render();
  } catch (err) {
    document.getElementById('pkg-body').innerHTML =
      '<tr><td colspan="7" class="empty-state"><p>Failed to load packages.</p></td></tr>';
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
}

function render() {
  let pkgs = [...allPackages];

  // Filter
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

  // Sort
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

  const tbody = document.getElementById('pkg-body');
  if (pkgs.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <div class="icon">&#x1F4E6;</div>
          <h2>No packages${searchTerm || platformFilter ? ' match your filter' : ' published yet'}</h2>
          <p>${searchTerm || platformFilter
            ? 'Try adjusting your search or platform filter.'
            : 'Publish your first package to see it here.'}</p>
          ${!searchTerm && !platformFilter
            ? '<code>cvcpkg publish --server https://pkg.tx.wtf &lt;archive&gt;</code>'
            : ''}
        </div>
      </td></tr>`;
    return;
  }

  tbody.innerHTML = pkgs.map(p => `
    <tr>
      <td><span class="pkg-name">${esc(p.name)}</span>${
        p.yanked ? '<span class="badge-yanked">yanked</span>' : ''
      }</td>
      <td><span class="pkg-version">${esc(p.version)}</span></td>
      <td>${platformBadge(p.platform)}</td>
      <td>${esc(p.arch)}</td>
      <td>${esc(p.build_type)}/${esc(p.link)}</td>
      <td class="pkg-size">${fmtSize(p.size_bytes)}</td>
      <td class="pkg-date">${fmtDate(p.published_at)}</td>
    </tr>
  `).join('');

  // Update sort arrows
  document.querySelectorAll('.pkg-table th[data-key]').forEach(th => {
    const arrow = th.querySelector('.arrow');
    if (th.dataset.key === currentSort.key) {
      th.classList.add('sorted');
      arrow.textContent = currentSort.dir === 'asc' ? '▲' : '▼';
    } else {
      th.classList.remove('sorted');
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

function platformBadge(platform) {
  if (!platform) return '<span class="text-muted">—</span>';
  let cls = 'other';
  const lp = platform.toLowerCase();
  if (lp.includes('linux')) cls = 'linux';
  else if (lp.includes('darwin') || lp.includes('macos')) cls = 'darwin';
  else if (lp.includes('win')) cls = 'windows';
  return `<span class="platform-badge ${cls}">${esc(platform)}</span>`;
}

function fmtSize(bytes) {
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let sz = bytes;
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
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function esc(s) {
  if (s == null) return '';
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(s)));
  return div.innerHTML;
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search').addEventListener('input', e => {
    searchTerm = e.target.value;
    render();
  });

  document.getElementById('platform-filter').addEventListener('change', e => {
    platformFilter = e.target.value;
    render();
  });

  document.querySelectorAll('.pkg-table th[data-key]').forEach(th => {
    th.addEventListener('click', () => sortBy(th.dataset.key));
  });

  init();
});
"""


def landing_html() -> str:
    """Return the complete HTML for the landing page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>cvcpkg &mdash; Package Archive</title>
  <style>{_CSS}</style>
</head>
<body>

<header>
  <div class="container header-inner">
    <div class="logo">
      <div class="logo-icon">C</div>
      <h1>cvcpkg <span class="version">v{__version__}</span></h1>
    </div>
    <nav class="header-links">
      <a href="/docs">API Docs</a>
      <a href="/v1/catalog">Catalog JSON</a>
      <a href="https://github.com/transfix/libcvc-deps">GitHub</a>
    </nav>
  </div>
</header>

<main class="container">
  <div class="stats-bar">
    <div class="stat">
      <span class="stat-value" id="stat-packages">—</span>
      <span class="stat-label">Packages</span>
    </div>
    <div class="stat">
      <span class="stat-value" id="stat-builds">—</span>
      <span class="stat-label">Builds</span>
    </div>
    <div class="stat">
      <span class="stat-value" id="stat-platforms">—</span>
      <span class="stat-label">Platforms</span>
    </div>
    <div class="stat">
      <span class="stat-value" id="stat-size">—</span>
      <span class="stat-label">Total Size</span>
    </div>
  </div>

  <div class="description">
    <p>
      <strong>cvcpkg</strong> is a cross-platform, language-agnostic package archive
      built for the scientific computing community.  It provides pre-built binary
      packages for C/C++ libraries across Linux, macOS, and Windows.  Each
      <strong>cvcpkg release</strong> defines a curated, tested set of recipes
      that is treated as a long-term support snapshot &mdash; guaranteeing
      reproducible downstream builds.  Updated and community-contributed recipes
      are made available live while we harden the next release.
    </p>
  </div>

  <div class="controls">
    <div class="search-box">
      <input type="text" id="search" placeholder="Search packages..." />
    </div>
    <div class="sort-group">
      <label for="platform-filter">Platform:</label>
      <select id="platform-filter">
        <option value="">All platforms</option>
      </select>
    </div>
  </div>

  <table class="pkg-table">
    <thead>
      <tr>
        <th data-key="name">Name <span class="arrow"></span></th>
        <th data-key="version">Version <span class="arrow"></span></th>
        <th data-key="platform">Platform <span class="arrow"></span></th>
        <th data-key="arch">Arch <span class="arrow"></span></th>
        <th data-key="build_type">Build <span class="arrow"></span></th>
        <th data-key="size_bytes">Size <span class="arrow"></span></th>
        <th data-key="published_at">Published <span class="arrow"></span></th>
      </tr>
    </thead>
    <tbody id="pkg-body">
      <tr><td colspan="7" class="loading"><div class="spinner"></div></td></tr>
    </tbody>
  </table>
</main>

<footer>
  <div class="container">
    <p>
      <a href="https://github.com/transfix/libcvc-deps">cvcpkg</a> &mdash;
      cross-platform binary package archive for scientific computing
    </p>
  </div>
</footer>

<script>{_JS}</script>
</body>
</html>"""


class HealthResponse:
    """Unused — for type-checking landing page only."""

    pass
