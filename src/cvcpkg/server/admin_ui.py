"""Server-rendered admin dashboard (Phase 3).

``/admin`` — a lightweight, server-side-rendered administration UI.
No SPA framework and no client-side data fetching: every number and
chart on the page is rendered into the HTML by the server, so the
analytics endpoints' Bearer-token auth never has to be smuggled into
browser JS.  Styling matches the landing page (Bulma).

Auth: an admin-role API token is exchanged at ``POST /admin/login`` for
a short-lived, HMAC-signed session cookie.  The raw token is never
stored — the cookie carries only ``<expiry>.<hmac(expiry)>``.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import time

_SESSION_COOKIE = "cvcpkg_admin_session"
_SESSION_TTL_SECONDS = 8 * 3600  # one working day


# ── Session cookie helpers ──────────────────────────────────────


def make_session_value(key: bytes, *, now: float | None = None) -> str:
    """Return a signed session cookie value: ``<expiry_ts>.<hmac_hex>``."""
    exp = int((now if now is not None else time.time()) + _SESSION_TTL_SECONDS)
    sig = hmac.new(key, f"admin-session:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_session_value(key: bytes, value: str, *, now: float | None = None) -> bool:
    """True if *value* is a validly signed, unexpired session cookie."""
    try:
        exp_str, sig = value.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if (now if now is not None else time.time()) >= exp:
        return False
    want = hmac.new(key, f"admin-session:{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


# ── HTML helpers ────────────────────────────────────────────────

_PAGE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · cvcpkg admin</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@0.9.4/css/bulma.min.css">
<style>
  .cvc-num {{ font-variant-numeric: tabular-nums; }}
  .cvc-spark {{ width: 100%; height: 120px; }}
  .cvc-muted {{ color: #7a7a7a; }}
</style>
</head>
<body>
<nav class="navbar is-dark" role="navigation">
  <div class="navbar-brand"><span class="navbar-item has-text-weight-bold">cvcpkg admin</span></div>
  <div class="navbar-end">{nav_right}</div>
</nav>
<section class="section">
  <div class="container">
{body}
  </div>
</section>
</body>
</html>"""


def _esc(s: object) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _human_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:,.1f} {unit}" if unit != "B" else f"{int(v):,} B"
        v /= 1024
    return f"{n} B"


def _sparkline_svg(points: list[int], *, width: int = 640, height: int = 120) -> str:
    """Inline SVG line chart for a daily series (no JS required)."""
    if not points:
        return '<p class="cvc-muted">no data</p>'
    peak = max(max(points), 1)
    n = len(points)
    step = width / max(n - 1, 1)
    coords = []
    for i, v in enumerate(points):
        x = i * step
        y = height - (v / peak) * (height - 8) - 4
        coords.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(coords)
    baseline = f"0,{height} {width},{height}"
    return (
        f'<svg class="cvc-spark" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{baseline}" stroke="#dbdbdb" fill="none" stroke-width="1"/>'
        f'<polyline points="{polyline}" stroke="#3273dc" fill="none" stroke-width="2"/>'
        f"</svg>"
    )


def _stat_card(label: str, value: str, *, sub: str = "") -> str:
    sub_html = f'<p class="is-size-7 cvc-muted">{_esc(sub)}</p>' if sub else ""
    return (
        '<div class="column is-3"><div class="box has-text-centered">'
        f'<p class="heading">{_esc(label)}</p>'
        f'<p class="title is-4 cvc-num">{_esc(value)}</p>{sub_html}'
        "</div></div>"
    )


def _table(headers: list[str], rows: list[list[str]], *, empty: str = "no data") -> str:
    if not rows:
        return f'<p class="cvc-muted">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="cvc-num">{_esc(c)}</td>' for c in row) + "</tr>"
        for row in rows
    )
    return (
        '<table class="table is-fullwidth is-striped is-narrow">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


# ── Pages ───────────────────────────────────────────────────────


def login_html(*, error: str = "") -> str:
    error_html = (
        f'<div class="notification is-danger is-light">{_esc(error)}</div>' if error else ""
    )
    body = f"""
<div class="columns is-centered"><div class="column is-4">
  <h1 class="title is-4">Sign in</h1>
  {error_html}
  <form method="post" action="/admin/login">
    <div class="field">
      <label class="label">Admin API token</label>
      <div class="control">
        <input class="input" type="password" name="token" placeholder="cvctok_…"
               autocomplete="off" autofocus required>
      </div>
      <p class="help">Requires an <strong>admin</strong>-role token. Exchanged for a
      signed session cookie ({_SESSION_TTL_SECONDS // 3600}h); the token itself is not stored.</p>
    </div>
    <button class="button is-link is-fullwidth" type="submit">Sign in</button>
  </form>
</div></div>"""
    return _PAGE_SHELL.format(title="Sign in", nav_right="", body=body)


def dashboard_html(data: dict) -> str:
    """Render the overview dashboard.

    *data* keys (all pre-fetched by the route; may be partial):
      stats            — server stats dict (packages_count, version, …)
      downloads        — /v1/analytics/downloads-shaped dict
      bandwidth        — /v1/analytics/bandwidth-shaped dict
      platforms        — /v1/analytics/platforms-shaped dict
      telemetry        — /v1/analytics/telemetry-shaped dict
      trend_daily      — list of {"date","count"} for the trend chart
      days             — analytics window in days
    """
    stats = data.get("stats") or {}
    downloads = data.get("downloads") or {}
    bandwidth = data.get("bandwidth") or {}
    platforms = data.get("platforms") or {}
    telemetry = data.get("telemetry") or {}
    trend = data.get("trend_daily") or []
    days = data.get("days", 30)

    cards = "".join(
        [
            _stat_card("Packages", f"{stats.get('packages_count', 0):,}"),
            _stat_card("Downloads (all time)", f"{downloads.get('total_all_time', 0):,}"),
            _stat_card(f"Bandwidth ({days}d)", _human_bytes(int(bandwidth.get("total_bytes", 0)))),
            _stat_card(f"Telemetry pings ({days}d)", f"{telemetry.get('total', 0):,}"),
        ]
    )

    top_rows = [
        [p["name"], f"{p['count']:,}", _human_bytes(int(p.get("bytes_sent", 0)))]
        for p in (downloads.get("top_packages") or [])[:15]
    ]
    plat_rows = [
        [p["platform"], p.get("arch", ""), f"{p['count']:,}"]
        for p in (platforms.get("platforms") or [])[:15]
    ]
    client_rows = [
        [c["version"] or "(not a cvcpkg client)", f"{c['count']:,}"]
        for c in (platforms.get("client_versions") or [])[:10]
    ]
    tele_py_rows = [
        [v["version"] or "?", f"{v['count']:,}"]
        for v in (telemetry.get("python_versions") or [])[:10]
    ]

    trend_svg = _sparkline_svg([int(d.get("count", 0)) for d in trend])
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    nav_right = (
        '<div class="navbar-item">'
        '<form method="post" action="/admin/logout">'
        '<button class="button is-small is-light" type="submit">Sign out</button>'
        "</form></div>"
    )
    meta_line = (
        f"server v{_esc(stats.get('version', '?'))} · generated {generated} · window {days}d"
    )
    body = f"""
<h1 class="title is-4">Overview
  <span class="is-size-7 has-text-weight-normal cvc-muted">{meta_line}</span>
</h1>
<div class="columns is-multiline">{cards}</div>

<div class="box">
  <h2 class="subtitle is-6">Downloads per day ({days}d)</h2>
  {trend_svg}
</div>

<div class="columns">
  <div class="column is-6"><div class="box">
    <h2 class="subtitle is-6">Top packages ({days}d)</h2>
    {_table(["package", "downloads", "bytes"], top_rows)}
  </div></div>
  <div class="column is-6"><div class="box">
    <h2 class="subtitle is-6">Platform mix ({days}d)</h2>
    {_table(["platform", "arch", "downloads"], plat_rows)}
    <h2 class="subtitle is-6 mt-4">cvcpkg client versions ({days}d)</h2>
    {_table(["client", "downloads"], client_rows)}
  </div></div>
</div>

<div class="box">
  <h2 class="subtitle is-6">Opt-in telemetry ({days}d) — Python versions</h2>
  {_table(["python", "pings"], tele_py_rows, empty="no telemetry received")}
</div>
"""
    return _PAGE_SHELL.format(title="Overview", nav_right=nav_right, body=body)
