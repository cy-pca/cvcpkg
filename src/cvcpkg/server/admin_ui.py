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
        .replace("'", "&#x27;")
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


# ── Management pages (increment 2) ──────────────────────────────


def _tabs(active: str) -> str:
    items = [
        ("Overview", "/admin"),
        ("Packages", "/admin/packages"),
        ("Tokens", "/admin/tokens"),
        ("Audit", "/admin/audit"),
        ("Releases", "/admin/releases"),
        ("Health", "/admin/health"),
    ]
    lis = "".join(
        '<li class="{cls}"><a href="{href}">{label}</a></li>'.format(
            cls="is-active" if label.lower() == active else "",
            href=href,
            label=label,
        )
        for label, href in items
    )
    return f'<div class="tabs is-boxed"><ul>{lis}</ul></div>'


_NAV_SIGNOUT = (
    '<div class="navbar-item">'
    '<form method="post" action="/admin/logout">'
    '<button class="button is-small is-light" type="submit">Sign out</button>'
    "</form></div>"
)


def _variant_fields(p: object) -> str:
    """Hidden form fields identifying one package variant."""
    fields = ""
    for field in ("name", "version", "platform", "arch", "build_type", "link"):
        value = _esc(getattr(p, field, ""))
        fields += f'<input type="hidden" name="{field}" value="{value}">'
    return fields


def packages_html(pkgs: list, total: int, *, q: str = "", notice: str = "") -> str:
    notice_html = (
        f'<div class="notification is-success is-light">{_esc(notice)}</div>' if notice else ""
    )
    rows = []
    for p in pkgs:
        yanked = bool(getattr(p, "yanked", False))
        state = '<span class="tag is-warning">yanked</span>' if yanked else ""
        toggle_action = "unyank" if yanked else "yank"
        toggle_class = "is-success is-light" if yanked else "is-warning is-light"
        actions = (
            '<form method="post" action="/admin/packages/action" style="display:inline">'
            f"{_variant_fields(p)}"
            f'<input type="hidden" name="action" value="{toggle_action}">'
            f'<button class="button is-small {toggle_class}" type="submit">'
            f"{toggle_action}</button></form> "
            '<form method="post" action="/admin/packages/action" style="display:inline" '
            "onsubmit=\"return confirm('Delete this variant permanently?')\">"
            f"{_variant_fields(p)}"
            '<input type="hidden" name="action" value="delete">'
            '<button class="button is-small is-danger is-light" type="submit">delete</button>'
            "</form>"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(getattr(p, 'name', ''))} {state}</td>"
            f"<td class='cvc-num'>{_esc(getattr(p, 'version', ''))}</td>"
            f"<td>{_esc(getattr(p, 'platform', ''))}/{_esc(getattr(p, 'arch', ''))}</td>"
            f"<td>{_esc(getattr(p, 'build_type', ''))}/{_esc(getattr(p, 'link', ''))}</td>"
            f"<td>{actions}</td>"
            "</tr>"
        )
    if rows:
        table = (
            '<table class="table is-fullwidth is-striped is-narrow">'
            "<thead><tr><th>package</th><th>version</th><th>platform</th>"
            "<th>variant</th><th>actions</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        table = '<p class="cvc-muted">no packages match</p>'
    body = f"""
{_tabs("packages")}
{notice_html}
<form method="get" action="/admin/packages" class="field has-addons">
  <div class="control is-expanded">
    <input class="input" type="text" name="q" value="{_esc(q)}"
           placeholder="filter by name substring">
  </div>
  <div class="control"><button class="button is-link" type="submit">Filter</button></div>
</form>
<p class="cvc-muted is-size-7">{len(pkgs)} shown of {total} variants (yanked included)</p>
{table}
"""
    return _PAGE_SHELL.format(title="Packages", nav_right=_NAV_SIGNOUT, body=body)


def tokens_html(
    tokens: list,
    *,
    new_token: tuple[str, str] | None = None,
    notice: str = "",
    error: str = "",
) -> str:
    flash = ""
    if new_token:
        tname, raw = new_token
        flash = (
            '<div class="notification is-success">'
            f"Token <strong>{_esc(tname)}</strong> created. Copy it now — "
            "it will not be shown again:<br>"
            f'<code style="word-break:break-all">{_esc(raw)}</code></div>'
        )
    elif notice:
        flash = f'<div class="notification is-success is-light">{_esc(notice)}</div>'
    if error:
        flash += f'<div class="notification is-danger is-light">{_esc(error)}</div>'

    rows = []
    for t in tokens:
        role = getattr(getattr(t, "role", None), "value", getattr(t, "role", ""))
        revoked = bool(getattr(t, "revoked", False))
        created = getattr(t, "created_at", "")
        created_s = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else str(created)
        state = (
            '<span class="tag is-danger is-light">revoked</span>'
            if revoked
            else '<span class="tag is-success is-light">active</span>'
        )
        tok_name = _esc(getattr(t, "name", ""))
        if revoked:
            revoke_btn = ""
        else:
            revoke_btn = (
                '<form method="post" action="/admin/tokens/revoke" style="display:inline" '
                f"onsubmit=\"return confirm('Revoke token {tok_name}?')\">"
                f'<input type="hidden" name="name" value="{tok_name}">'
                '<button class="button is-small is-danger is-light" type="submit">'
                "revoke</button></form>"
            )
        rows.append(
            "<tr>"
            f"<td>{tok_name}</td>"
            f"<td>{_esc(role)}</td>"
            f"<td class='cvc-num'>{_esc(created_s)}</td>"
            f"<td>{state}</td><td>{revoke_btn}</td></tr>"
        )
    if rows:
        table = (
            '<table class="table is-fullwidth is-striped is-narrow">'
            "<thead><tr><th>name</th><th>role</th><th>created</th>"
            "<th>status</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        table = '<p class="cvc-muted">no tokens</p>'
    body = f"""
{_tabs("tokens")}
{flash}
<div class="box">
  <h2 class="subtitle is-6">Create token</h2>
  <form method="post" action="/admin/tokens/create" class="field is-grouped">
    <div class="control is-expanded">
      <input class="input" type="text" name="name" placeholder="token name" required>
    </div>
    <div class="control">
      <div class="select">
        <select name="role">
          <option value="reader">reader</option>
          <option value="publisher" selected>publisher</option>
          <option value="admin">admin</option>
        </select>
      </div>
    </div>
    <div class="control"><button class="button is-link" type="submit">Create</button></div>
  </form>
</div>
{table}
"""
    return _PAGE_SHELL.format(title="Tokens", nav_right=_NAV_SIGNOUT, body=body)


def audit_html(entries: list, total: int, *, chain: tuple[bool, str] | None = None) -> str:
    chain_html = ""
    if chain is not None:
        ok, msg = chain
        cls = "is-success" if ok else "is-danger"
        verdict = "intact" if ok else "BROKEN"
        detail = f": {_esc(msg)}" if msg else ""
        chain_html = f'<div class="notification {cls} is-light">Audit chain {verdict}{detail}</div>'
    rows = []
    for e in entries:
        ts = getattr(e, "timestamp", "")
        ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        action = getattr(getattr(e, "action", None), "value", getattr(e, "action", ""))
        rows.append(
            "<tr>"
            f"<td class='cvc-num'>{_esc(ts_s)}</td>"
            f"<td>{_esc(action)}</td>"
            f"<td>{_esc(getattr(e, 'actor', ''))}</td>"
            f"<td>{_esc(getattr(e, 'target', ''))}</td>"
            f"<td>{_esc(getattr(e, 'detail', ''))}</td></tr>"
        )
    if rows:
        table = (
            '<table class="table is-fullwidth is-striped is-narrow">'
            "<thead><tr><th>time (UTC)</th><th>action</th><th>actor</th>"
            "<th>target</th><th>detail</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        table = '<p class="cvc-muted">no audit entries</p>'
    shown = len(entries)
    body = f"""
{_tabs("audit")}
{chain_html}
<div class="level">
  <div class="level-left">
    <p class="cvc-muted is-size-7">latest {shown} of {total} entries</p>
  </div>
  <div class="level-right">
    <form method="get" action="/admin/audit">
      <input type="hidden" name="verify" value="1">
      <button class="button is-small is-link is-light" type="submit">Verify chain</button>
    </form>
  </div>
</div>
{table}
"""
    return _PAGE_SHELL.format(title="Audit", nav_right=_NAV_SIGNOUT, body=body)


# ── Health + Releases pages (increment 3) ───────────────────────


def _human_duration(seconds: float) -> str:
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, _ = divmod(s, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def health_html(stats: dict, builders: list) -> str:
    cards = "".join(
        [
            _stat_card("Uptime", _human_duration(stats.get("uptime_seconds", 0))),
            _stat_card(
                "Database",
                str(stats.get("database_backend", "yaml")),
                sub="mirror mode" if stats.get("mirror_mode") else "primary",
            ),
            _stat_card("Archive storage", _human_bytes(int(stats.get("total_storage_bytes", 0)))),
            _stat_card("Audit entries", f"{stats.get('audit_entries', 0):,}"),
            _stat_card("Packages", f"{stats.get('packages_count', 0):,}"),
            _stat_card("Organizations", f"{stats.get('orgs_count', 0):,}"),
            _stat_card("Build jobs (total)", f"{stats.get('build_jobs_count', 0):,}"),
            _stat_card(
                "Builders (live WS)",
                f"{stats.get('builders_count', 0):,}",
                sub=f"{stats.get('builders_connected', 0)} on websocket",
            ),
        ]
    )
    rows = []
    for b in builders:
        status = getattr(b, "status", "")
        status_val = getattr(status, "value", status)
        cls = {
            "online": "is-success",
            "busy": "is-info",
            "offline": "is-danger",
        }.get(str(status_val), "is-light")
        hb = getattr(b, "last_heartbeat", "")
        hb_s = hb.strftime("%Y-%m-%d %H:%M:%S") if hasattr(hb, "strftime") else str(hb or "never")
        rows.append(
            "<tr>"
            f"<td>{_esc(getattr(b, 'name', ''))}</td>"
            f"<td>{_esc(getattr(b, 'platform', ''))}/{_esc(getattr(b, 'arch', ''))}</td>"
            f'<td><span class="tag {cls} is-light">{_esc(status_val)}</span></td>'
            f"<td class='cvc-num'>{_esc(getattr(b, 'current_jobs', 0))}/"
            f"{_esc(getattr(b, 'max_jobs', 0))}</td>"
            f"<td class='cvc-num'>{_esc(hb_s)}</td></tr>"
        )
    if rows:
        btable = (
            '<table class="table is-fullwidth is-striped is-narrow">'
            "<thead><tr><th>builder</th><th>platform</th><th>status</th>"
            "<th>jobs</th><th>last heartbeat (UTC)</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        btable = '<p class="cvc-muted">no builders registered</p>'
    body = f"""
{_tabs("health")}
<div class="columns is-multiline">{cards}</div>
<div class="box">
  <h2 class="subtitle is-6">Builder fleet</h2>
  {btable}
</div>
"""
    return _PAGE_SHELL.format(title="Health", nav_right=_NAV_SIGNOUT, body=body)


def releases_html(
    tags: list[dict], *, selected: str | None = None, pkgs: list | None = None
) -> str:
    tag_rows = []
    for t in tags:
        tag = t.get("tag", "")
        label = tag if tag else "(live / untagged)"
        href = f"/admin/releases?tag={_esc(tag)}" if tag else "/admin/releases?tag="
        active = ' class="is-selected"' if selected is not None and selected == tag else ""
        tag_rows.append(
            f'<tr{active}><td><a href="{href}">{_esc(label)}</a></td>'
            f"<td class='cvc-num'>{t.get('count', 0):,}</td></tr>"
        )
    if tag_rows:
        tag_table = (
            '<table class="table is-fullwidth is-striped is-narrow">'
            "<thead><tr><th>release tag</th><th>variants</th></tr></thead>"
            f"<tbody>{''.join(tag_rows)}</tbody></table>"
        )
    else:
        tag_table = '<p class="cvc-muted">no packages published yet</p>'

    detail = ""
    if selected is not None:
        label = selected if selected else "(live / untagged)"
        prows = []
        for p in pkgs or []:
            prows.append(
                "<tr>"
                f"<td>{_esc(getattr(p, 'name', ''))}</td>"
                f"<td class='cvc-num'>{_esc(getattr(p, 'version', ''))}</td>"
                f"<td>{_esc(getattr(p, 'platform', ''))}/{_esc(getattr(p, 'arch', ''))}</td>"
                f"<td>{_esc(getattr(p, 'build_type', ''))}/{_esc(getattr(p, 'link', ''))}</td>"
                "</tr>"
            )
        if prows:
            ptable = (
                '<table class="table is-fullwidth is-striped is-narrow">'
                "<thead><tr><th>package</th><th>version</th><th>platform</th>"
                "<th>variant</th></tr></thead>"
                f"<tbody>{''.join(prows)}</tbody></table>"
            )
        else:
            ptable = '<p class="cvc-muted">no variants under this tag</p>'
        detail = f"""
<div class="box">
  <h2 class="subtitle is-6">Variants in {_esc(label)} (first {len(prows)})</h2>
  {ptable}
</div>"""

    body = f"""
{_tabs("releases")}
<div class="box">
  <h2 class="subtitle is-6">Release tags</h2>
  <p class="cvc-muted is-size-7">A release freeze stamps packages with a
  release tag; untagged variants are the live channel. Release creation /
  promotion tooling is tracked in the roadmap (Phase 3 follow-up).</p>
  {tag_table}
</div>
{detail}
"""
    return _PAGE_SHELL.format(title="Releases", nav_right=_NAV_SIGNOUT, body=body)
