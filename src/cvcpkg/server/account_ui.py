# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The signed-in surface for ordinary users: ``/login`` and ``/account``.

Until now cvcpkg had no non-admin authenticated surface at all.  The public
site had no login, and the OIDC callback refused anything that was not an
admin — so ``publisher`` and ``reader`` were real on the server and inert in a
browser, and the only way to get a publish token was for an admin to mint one
by hand and courier the secret out of band.

These pages close that.  They use ``landing.py``'s shell so ``/account`` reads
as part of the public site, and ``admin_ui.py``'s idioms (``str.format``
templates, one escaper, real form POSTs) so there is one way to do things.

Two conventions that matter, both easy to get silently wrong:

* This module is ``str.format``-based, so literal CSS/JS braces are doubled.
  Do not mix in f-strings.
* ``_esc`` is for text nodes and quoted attributes; anything interpolated into
  a ``<script>`` block uses ``landing._js_string_literal``.  They are not
  interchangeable, and confusing them is the live XSS risk in this codebase.
"""

from __future__ import annotations

import datetime
import threading

from cvcpkg.server import csrf as _csrf
from cvcpkg.server.admin_ui import _esc

# ── One-shot flash for a freshly minted secret ──────────────────
#
# The raw secret exists for exactly one render.  It is deliberately NOT put in
# the response body of the POST (a refresh or a bfcache restore re-shows it)
# and deliberately NOT persisted — the whole premise of the token store is that
# the secret is never stored.  So: hand it across one redirect in memory, keyed
# on the session, and delete it on read.
_FLASH_TTL_SECONDS = 120.0
_FLASH_MAX = 512
_flashes: dict[str, tuple[float, str, str]] = {}
_flash_lock = threading.Lock()


def put_flash(sid: str, kind: str, body: str, *, now: float | None = None) -> None:
    clock = now if now is not None else datetime.datetime.now().timestamp()
    with _flash_lock:
        if len(_flashes) >= _FLASH_MAX:
            for key in [k for k, (exp, _, _) in _flashes.items() if exp <= clock]:
                _flashes.pop(key, None)
            if len(_flashes) >= _FLASH_MAX:
                _flashes.clear()
        _flashes[sid] = (clock + _FLASH_TTL_SECONDS, kind, body)


def take_flash(sid: str, *, now: float | None = None) -> tuple[str, str] | None:
    clock = now if now is not None else datetime.datetime.now().timestamp()
    with _flash_lock:
        entry = _flashes.pop(sid, None)
    if entry is None:
        return None
    expires, kind, body = entry
    if clock >= expires:
        return None
    return kind, body


# ── Page shell ──────────────────────────────────────────────────

_CSS = """
  .acct-card {{ background:#111; border:1px solid #2b2b2b; border-radius:8px; padding:1.25rem 1.5rem; margin-bottom:1.25rem; }}
  .acct-handle {{ font-size:2rem; font-weight:700; letter-spacing:-0.02em; }}
  .acct-mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85rem; color:#9aa0a6; word-break:break-all; }}
  .acct-secret {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; background:#000; border:1px solid #3a3a3a; border-radius:6px; padding:.75rem; word-break:break-all; }}
"""


def _page(title: str, body: str) -> str:
    from cvcpkg.server.landing import _footer_html, _head_html, _navbar_html

    return (
        '<!DOCTYPE html>\n<html lang="en" data-theme="dark" class="has-background-black-bis">\n'
        + _head_html(title)
        + "<style>"
        + _CSS.format()
        + "</style>\n"
        + '<body class="has-background-black-bis has-text-light">\n'
        + _navbar_html()
        + '\n<section class="section has-background-black-bis"><div class="container">\n'
        + body
        + "\n</div></section>\n"
        + _footer_html()
        + "\n</body>\n</html>"
    )


def _notification(kind: str, body: str) -> str:
    return f'<div class="notification {_esc(kind)}">{body}</div>'


# ── /login ──────────────────────────────────────────────────────


def login_html(*, oidc_enabled: bool, next_url: str = "/account", error: str = "") -> str:
    """The public sign-in page (distinct from the admin break-glass form)."""
    err = _notification("is-danger is-light", _esc(error)) if error else ""
    sso = ""
    if oidc_enabled:
        sso = f"""
      <a class="button is-primary is-fullwidth mb-4"
         href="/auth/oidc/login?next={_esc(next_url)}">
        <span class="icon mr-1"><i class="fas fa-right-to-bracket"></i></span>
        Sign in with tx.wtf
      </a>
      <p class="has-text-centered has-text-grey is-size-7 mb-4">or use an API token</p>
"""
    return _page(
        "Sign in &mdash; cvcpkg",
        f"""
  <div class="columns is-centered">
    <div class="column is-5">
      <h1 class="title is-4 has-text-white">Sign in</h1>
      {err}
      <div class="acct-card">
        {sso}
        <form method="post" action="/login">
          <input type="hidden" name="next" value="{_esc(next_url)}">
          <div class="field">
            <label class="label has-text-grey-lighter is-size-7">API token</label>
            <div class="control">
              <input class="input" type="password" name="token" placeholder="cvctok_&hellip;"
                     autocomplete="off" required>
            </div>
          </div>
          <button class="button is-link is-fullwidth" type="submit">Sign in with a token</button>
        </form>
        <p class="help has-text-grey mt-3">
          Signing in with a token gives you a browser session for an identity you
          already have. Self-service tokens require signing in with tx.wtf.
        </p>
      </div>
    </div>
  </div>
""",
    )


# ── /account ────────────────────────────────────────────────────


def _role_tag(role: str) -> str:
    colour = {"admin": "is-danger", "publisher": "is-info", "reader": "is-grey"}.get(
        role, "is-grey"
    )
    return f'<span class="tag {colour} is-medium">{_esc(role)}</span>'


def _fmt(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return _esc(dt)


def account_html(
    *,
    principal,
    role: str,
    role_reason: str,
    session_row,
    sessions: list,
    tokens: list,
    orgs: list[str],
    csrf_for,
    flash: tuple[str, str] | None = None,
    error: str = "",
    can_rename: bool = False,
    can_mint: bool = True,
    mint_disabled_reason: str = "",
) -> str:
    """The account page.  ``csrf_for(ref)`` returns the token for one form."""
    blocks: list[str] = []

    if flash is not None:
        blocks.append(_notification(flash[0], flash[1]))
    if error:
        blocks.append(_notification("is-danger is-light", _esc(error)))

    # ── Identity ────────────────────────────────────────────
    rename = ""
    if can_rename:
        rename = f"""
      <form method="post" action="/account/rename" class="field has-addons mt-3"
            onsubmit="return confirm('Rename to the value entered? This is only possible until you mint a token, join an org or publish.');">
        <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_ACCOUNT_RENAME))}">
        <div class="control"><input class="input is-small" name="name" placeholder="new handle" required></div>
        <div class="control"><button class="button is-small" type="submit">Rename</button></div>
      </form>
      <p class="help has-text-grey">
        Available only until this handle is used for something. After that a rename
        would have to rewrite published-by and audit records, which would falsify history.
      </p>"""

    blocks.append(
        f"""
  <div class="acct-card">
    <p class="has-text-grey is-size-7 mb-1">Your handle</p>
    <p class="acct-handle has-text-white">{_esc(principal.name)}</p>
    <p class="has-text-grey-lighter is-size-7 mt-2">
      This is the name to give an organization owner for
      <code>cvcpkg org add-member &lt;org&gt; {_esc(principal.name)}</code>.
    </p>
    <div class="mt-4">
      <p class="acct-mono">{_esc(principal.display_name or "—")} &middot; {_esc(principal.email or "no email")}</p>
      <p class="acct-mono">{_esc(principal.issuer)} &middot; {_esc(principal.subject)}</p>
      <p class="acct-mono">session expires {_fmt(getattr(session_row, "expires_at", None))}</p>
    </div>
    {rename}
    <form method="post" action="/logout" class="mt-4">
      <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_LOGOUT))}">
      <button class="button is-small is-dark" type="submit">Sign out</button>
    </form>
  </div>
"""
    )

    # ── Role ────────────────────────────────────────────────
    upsell = ""
    if role == "reader":
        upsell = """
    <p class="has-text-grey-lighter is-size-7 mt-3">
      Need to publish? Ask an administrator to add you to the
      <code>cvcpkg-publisher</code> group on tx.wtf, then sign in again.
    </p>"""
    blocks.append(
        f"""
  <div class="acct-card">
    <p class="has-text-grey is-size-7 mb-2">Your role</p>
    {_role_tag(role)}
    <p class="has-text-grey-lighter is-size-7 mt-3">{_esc(role_reason)}</p>
    {upsell}
  </div>
"""
    )

    # ── Organizations ───────────────────────────────────────
    if orgs:
        org_html = " ".join(
            f'<a class="tag is-dark" href="/org/{_esc(o)}">{_esc(o)}</a>' for o in orgs
        )
    else:
        org_html = '<p class="has-text-grey is-size-7">Not a member of any organization.</p>'
    blocks.append(
        f"""
  <div class="acct-card">
    <p class="has-text-grey is-size-7 mb-2">Organizations</p>
    {org_html}
  </div>
"""
    )

    # ── Tokens ──────────────────────────────────────────────
    if tokens:
        rows = []
        for t in tokens:
            grace = ""
            if getattr(t, "previous_hash_expires_at", None):
                grace = (
                    f'<span class="tag is-warning is-light ml-2">'
                    f"old secret valid until {_fmt(t.previous_hash_expires_at)}</span>"
                )
            rows.append(
                f"""
        <tr>
          <td><code>{_esc(t.name)}</code>{grace}</td>
          <td>{_esc(t.role.value if hasattr(t.role, "value") else t.role)}</td>
          <td>{_fmt(getattr(t, "created_at", None))}</td>
          <td>{_fmt(getattr(t, "expires_at", None))}</td>
          <td>
            <form method="post" action="/account/tokens/revoke"
                  onsubmit="return confirm('Revoke {_esc(t.name)}? Anything using this secret stops working immediately.');">
              <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_ACCOUNT_REVOKE))}">
              <input type="hidden" name="name" value="{_esc(t.name)}">
              <button class="button is-small is-danger is-outlined" type="submit">Revoke</button>
            </form>
          </td>
        </tr>"""
            )
        token_table = f"""
    <table class="table is-fullwidth is-narrow has-background-black-bis has-text-light">
      <thead><tr class="has-text-grey">
        <th>Name</th><th>Role</th><th>Created</th><th>Expires</th><th></th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""
    else:
        token_table = '<p class="has-text-grey is-size-7">No tokens yet.</p>'

    if can_mint:
        mint = f"""
    <form method="post" action="/account/tokens" class="mt-4">
      <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_ACCOUNT_MINT))}">
      <div class="field is-grouped">
        <div class="control is-expanded">
          <input class="input is-small" name="label" placeholder="label (e.g. laptop, ci)" required>
        </div>
        <div class="control">
          <div class="select is-small">
            <select name="role">
              <option value="reader">reader</option>
              <option value="publisher" selected>publisher</option>
            </select>
          </div>
        </div>
        <div class="control">
          <input class="input is-small" name="expires_in_days" type="number" min="1" max="365"
                 value="90" style="width:7rem">
        </div>
        <div class="control">
          <button class="button is-small is-primary" type="submit">Create token</button>
        </div>
      </div>
      <p class="help has-text-grey">
        Named <code>{_esc(principal.name)}.&lt;label&gt;</code>. It acts as you everywhere,
        and can never exceed your own role. The secret is shown once.
      </p>
    </form>"""
    else:
        mint = f"""
    <div class="notification is-dark mt-4">
      <p class="is-size-7">{_esc(mint_disabled_reason)}</p>
    </div>"""

    blocks.append(
        f"""
  <div class="acct-card">
    <p class="has-text-grey is-size-7 mb-2">API tokens</p>
    {token_table}
    {mint}
  </div>
"""
    )

    # ── Devices ─────────────────────────────────────────────
    current_id = getattr(session_row, "id", None)
    sess_rows = []
    for s in sessions:
        is_current = s.id == current_id
        label = _esc(s.device_label or "browser")
        marker = ' <span class="tag is-success is-light">this device</span>' if is_current else ""
        action = (
            ""
            if is_current
            else f"""
            <form method="post" action="/account/sessions/{s.id}/revoke">
              <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_SESSION_REVOKE))}">
              <button class="button is-small is-danger is-outlined" type="submit">Sign out</button>
            </form>"""
        )
        sess_rows.append(
            f"""
        <tr>
          <td>{label}{marker}</td>
          <td class="acct-mono">{_esc(s.ip_at_issue or "—")}</td>
          <td>{_fmt(s.issued_at)}</td>
          <td>{_fmt(s.expires_at)}</td>
          <td>{action}</td>
        </tr>"""
        )
    blocks.append(
        f"""
  <div class="acct-card">
    <p class="has-text-grey is-size-7 mb-2">Signed-in devices</p>
    <table class="table is-fullwidth is-narrow has-background-black-bis has-text-light">
      <thead><tr class="has-text-grey">
        <th>Device</th><th>IP at sign-in</th><th>Started</th><th>Expires</th><th></th>
      </tr></thead>
      <tbody>{"".join(sess_rows)}</tbody>
    </table>
    <form method="post" action="/account/sessions/revoke-all"
          onsubmit="return confirm('Sign out every other device?');">
      <input type="hidden" name="{_csrf.FIELD}" value="{_esc(csrf_for(_csrf.REF_SESSION_REVOKE_ALL))}">
      <button class="button is-small is-dark" type="submit">Sign out all other devices</button>
    </form>
  </div>
"""
    )

    # ── Stored-token cleanup (the only JS on the page) ──────
    blocks.append(
        """
  <div class="acct-card" id="ls-panel" style="display:none">
    <p class="has-text-grey is-size-7 mb-2">Stored token in this browser</p>
    <p class="is-size-7 has-text-grey-lighter">
      This browser has an API token saved in local storage from before sign-in existed.
      It is no longer needed for the pages that used it.
    </p>
    <button class="button is-small is-dark mt-3" id="ls-clear">Remove stored token</button>
  </div>
  <script>
  (function () {
    try {
      if (localStorage.getItem('cvcpkg_token')) {
        document.getElementById('ls-panel').style.display = '';
        document.getElementById('ls-clear').addEventListener('click', function () {
          try { localStorage.removeItem('cvcpkg_token'); } catch (e) {}
          document.getElementById('ls-panel').style.display = 'none';
        });
      }
    } catch (e) {}
  })();
  </script>
"""
    )

    return _page(
        "Account &mdash; cvcpkg",
        '<h1 class="title is-3 has-text-white">Account</h1>\n' + "\n".join(blocks),
    )


def minted_flash_html(token_name: str, secret: str) -> str:
    """The one-shot banner body carrying a freshly minted secret."""
    return f"""
    <p class="has-text-weight-semibold mb-2">Token <code>{_esc(token_name)}</code> created.</p>
    <p class="acct-secret">{_esc(secret)}</p>
    <p class="is-size-7 mt-2">
      Copy it now &mdash; it is shown once and only a hash is stored.
      Use it as <code>CVCPKG_TOKEN</code>.
    </p>"""
