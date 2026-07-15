"""``cvcpkg telemetry`` — opt-in, anonymous environment telemetry.

Phase 2 roadmap.  Strictly opt-in: nothing is ever sent unless the
``CVCPKG_TELEMETRY=1`` environment variable is set (automatic ping after
an install) or the user runs ``cvcpkg telemetry send`` explicitly.

The payload is anonymous by construction — platform, arch, Python and
cvcpkg versions, build-tool availability, and a CI flag.  No hostname,
no username, no paths, and the server stores nothing about the
connection (not even a hashed address).  Inspect exactly what would be
sent with ``cvcpkg telemetry status``.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import subprocess

import click

from cvcpkg.cli import cli

_ENV_FLAG = "CVCPKG_TELEMETRY"
_SEND_TIMEOUT = 5  # seconds; best-effort, never blocks an install for long


def telemetry_enabled() -> bool:
    """True when the user has opted in via CVCPKG_TELEMETRY=1."""
    return os.environ.get(_ENV_FLAG, "") in ("1", "true", "yes", "on")


def _tool_version(exe: str) -> str:
    """First line of ``exe --version``, or '' when unavailable."""
    path = shutil.which(exe)
    if not path:
        return ""
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return out.splitlines()[0][:64] if out else ""
    except Exception:
        return ""


def build_payload() -> dict:
    """Assemble the anonymous telemetry payload."""
    from cvcpkg import __version__
    from cvcpkg.platform import detect_arch, detect_platform

    tools: dict[str, str] = {}
    for exe in ("cmake", "ninja", "git", "cc", "cl"):
        ver = _tool_version(exe)
        if ver:
            tools[exe] = ver

    return {
        "platform": detect_platform(),
        "arch": detect_arch(),
        "python_version": _platform.python_version(),
        "cvcpkg_version": __version__,
        "ci": bool(os.environ.get("CI")),
        "tools": tools,
    }


def send_payload(server: str, payload: dict, *, timeout: int = _SEND_TIMEOUT) -> bool:
    """POST *payload* to ``{server}/v1/telemetry``.  Returns success."""
    import urllib.request

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{server.rstrip('/')}/v1/telemetry",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status in (200, 204)
    except Exception:
        return False


def maybe_send_telemetry(server: str) -> None:
    """Fire-and-forget post-install ping — only when opted in.

    Never raises and never adds more than ``_SEND_TIMEOUT`` seconds; an
    install must not fail (or noticeably slow down) because of telemetry.
    """
    if not telemetry_enabled() or not server:
        return
    try:
        send_payload(server, build_payload())
    except Exception:
        pass


# ── CLI ─────────────────────────────────────────────────────────


@cli.group("telemetry")
def telemetry_group() -> None:
    """Opt-in anonymous environment telemetry (off by default)."""


@telemetry_group.command("status")
def telemetry_status() -> None:
    """Show whether telemetry is enabled and exactly what would be sent."""
    enabled = telemetry_enabled()
    click.echo(f"Telemetry: {'ENABLED' if enabled else 'disabled'} ({_ENV_FLAG}=1 to enable)")
    click.echo("Payload that would be sent:")
    click.echo(json.dumps(build_payload(), indent=2, sort_keys=True))
    click.echo(
        "\nPrivacy: anonymous by construction — no hostname, no username, no"
        "\npaths; the server derives nothing from the connection."
    )


@telemetry_group.command("send")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
def telemetry_send(server: str) -> None:
    """Send one telemetry ping now (explicit consent by invocation)."""
    payload = build_payload()
    click.echo(json.dumps(payload, indent=2, sort_keys=True))
    if send_payload(server, payload):
        click.echo(f"Sent to {server}.")
    else:
        raise click.ClickException(f"failed to send telemetry to {server}")
