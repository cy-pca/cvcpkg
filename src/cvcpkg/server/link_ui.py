# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Server-rendered pages for ``/link`` — device-pairing approval.

The human reads an 8-character code off a terminal (``cvcpkg login`` on a
headless box) and approves it here.  RFC 8628 §5.4 documents that this channel
is socially engineerable, so the confirmation screen is **never** skipped, even
when the code arrives via ``verification_uri_complete``: it always shows who is
asking (IP, hostname, platform, CLI version, elapsed time) with explicit "if
you did not just run cvcpkg login, click Deny" copy, and the role defaults to
the lowest the user is entitled to.

No SPA and no client-side data fetching — plain POST forms with a CSRF token,
matching the ``/account`` surface.
"""

from __future__ import annotations

import html as _html


def _esc(s: object) -> str:
    return _html.escape(str(s), quote=True)


def _shell(title: str, body: str) -> str:
    from cvcpkg.server.landing import _footer_html, _head_html, _navbar_html

    return (
        '<!DOCTYPE html>\n<html lang="en" data-theme="dark" class="has-background-black-bis">\n'
        + _head_html(title)
        + '<body class="has-background-black-bis has-text-light">\n'
        + _navbar_html()
        + '\n<section class="section has-background-black-bis"><div class="container">'
        + '<div class="columns is-centered"><div class="column is-half">\n'
        + body
        + "\n</div></div></div></section>\n"
        + _footer_html()
        + "\n</body>\n</html>"
    )


def _notification(kind: str, body: str) -> str:
    return f'<div class="notification {_esc(kind)}">{body}</div>' if body else ""


def code_entry_html(*, csrf: str, field: str, prefill: str = "", error: str = "") -> str:
    """The 'enter the code from your terminal' form."""
    body = f"""
<h1 class="title is-4 has-text-white">Link a device</h1>
<p class="subtitle is-6 has-text-grey-lighter">
  Enter the code shown by <code>cvcpkg login</code> on the device you are pairing.
</p>
{_notification("is-danger is-light", _esc(error) if error else "")}
<form method="post" action="/link/submit">
  <input type="hidden" name="{_esc(field)}" value="{_esc(csrf)}">
  <div class="field">
    <div class="control">
      <input class="input is-medium has-text-centered is-family-monospace" type="text"
             name="user_code" value="{_esc(prefill)}" placeholder="XXXX-XXXX"
             autocomplete="off" autocapitalize="characters" spellcheck="false"
             style="letter-spacing:0.3em" required autofocus>
    </div>
  </div>
  <button class="button is-link is-fullwidth" type="submit">Continue</button>
</form>"""
    return _shell("Link a device — cvcpkg", body)


def confirm_html(
    *,
    csrf: str,
    field: str,
    user_code: str,
    device_label: str,
    platform: str,
    client_version: str,
    client_ip: str,
    requested_role: str,
    allowed_roles: list[str],
    elapsed_seconds: int,
) -> str:
    """The mandatory confirmation screen — who is asking, and for what.

    Least privilege by default (roadmap §0/§9): the role selector always
    pre-selects the **lowest** role the approver is entitled to, never the role
    the *device* asked for — otherwise a socially-engineered admin could grant an
    admin session in one click.  The device's request is shown read-only so the
    human sees the ask but must deliberately raise the dropdown to honour it.
    """
    default_role = allowed_roles[0] if allowed_roles else "reader"
    options = "".join(
        f'<option value="{_esc(r)}"{" selected" if r == default_role else ""}>{_esc(r)}</option>'
        for r in allowed_roles
    )
    asked = requested_role.strip() if requested_role else ""
    asked_display = asked or "(unspecified — defaulting to lowest)"
    rows = "".join(
        f"<tr><td class='has-text-grey-light'>{_esc(k)}</td>"
        f"<td class='is-family-monospace'>{_esc(v)}</td></tr>"
        for k, v in (
            ("Device", device_label or "(unnamed)"),
            ("Platform", platform or "?"),
            ("CLI version", client_version or "?"),
            ("Request from", client_ip or "?"),
            ("This device asked for", asked_display),
            ("Requested", f"{elapsed_seconds}s ago"),
            ("Code", user_code),
        )
    )
    body = f"""
<h1 class="title is-4 has-text-white">Approve this device?</h1>
<div class="notification is-warning is-light">
  If you did <strong>not</strong> just run <code>cvcpkg login</code>, click <strong>Deny</strong>.
</div>
<table class="table is-fullwidth is-dark is-striped"><tbody>{rows}</tbody></table>
<form method="post" action="/link/approve" class="mb-3">
  <input type="hidden" name="{_esc(field)}" value="{_esc(csrf)}">
  <input type="hidden" name="user_code" value="{_esc(user_code)}">
  <div class="field">
    <label class="label has-text-grey-light is-small">Grant role</label>
    <div class="control"><div class="select is-fullwidth"><select name="role">{options}</select></div></div>
  </div>
  <button class="button is-link is-fullwidth" type="submit">Approve</button>
</form>
<form method="post" action="/link/deny">
  <input type="hidden" name="{_esc(field)}" value="{_esc(csrf)}">
  <input type="hidden" name="user_code" value="{_esc(user_code)}">
  <button class="button is-danger is-light is-fullwidth" type="submit">Deny</button>
</form>"""
    return _shell("Approve a device — cvcpkg", body)


def result_html(title: str, message: str, *, kind: str = "is-success") -> str:
    body = (
        f'<h1 class="title is-4 has-text-white">{_esc(title)}</h1>'
        + _notification(kind, _esc(message))
        + '<a href="/account" class="button is-dark">Go to your account</a>'
    )
    return _shell(f"{title} — cvcpkg", body)
