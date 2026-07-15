"""Playwright coverage for the organization SPA pages.

Drives the server-rendered org list + org detail pages in a real browser
against a freshly-seeded server (public org 'acme' + private org 'shell'), and
verifies the user-facing behaviour that backs the API visibility guarantees:

  * the org list shows public orgs, hides private orgs from anonymous users,
    and does NOT render storage figures (member/admin-only);
  * the org detail page renders name/description/members/packages, hides the
    storage card for anonymous users, and shows a private badge only on a
    private org the caller may see;
  * a private org is not reachable anonymously (the page reports load failure).

Skipped unless both the server extras and Playwright (+ chromium) are present;
in CI this runs via docker-compose.test.yml / Dockerfile.playwright.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("uvicorn", reason="server extras required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")
pytest.importorskip("httpx", reason="httpx required")
pytest.importorskip("playwright.sync_api", reason="playwright not installed")

import httpx

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _env(state_dir: Path, db: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["CVCPKG_DATABASE_URL"] = db
    env["CVCPKG_SERVER_STATE_DIR"] = str(state_dir)
    env.pop("CVCPKG_POPULATE_UPSTREAM", None)
    env.pop("CVCPKG_MIRROR_MODE", None)
    return env


@pytest.fixture(scope="module")
def seeded_server(tmp_path_factory):
    """A real cvcpkg-server with a public org 'acme' + a private org 'shell'."""
    tmp = tmp_path_factory.mktemp("browser-orgs")
    state = tmp / "srv"
    state.mkdir()
    db = f"sqlite+aiosqlite:///{state / 'srv.db'}"
    env = _env(state, db)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-c", "from cvcpkg.server.cli import server_cli; server_cli()", *args],
            env=env, capture_output=True, text=True,
        )

    boot = cli("bootstrap", "--state-dir", str(state))
    m = re.search(r"Token:\s*(\S+)", boot.stdout) or re.search(r"(cvcp[_a-zA-Z0-9]{16,})", boot.stdout)
    assert m, f"bootstrap gave no token:\n{boot.stdout}\n{boot.stderr}"
    admin = m.group(1)

    proc = subprocess.Popen(
        [sys.executable, "-c", "from cvcpkg.server.cli import server_cli; server_cli()",
         "run", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            with contextlib.suppress(Exception):
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("server did not become healthy")

        h = {"Authorization": f"Bearer {admin}"}
        for slug, priv, desc in (("acme", False, "Acme public packages"),
                                 ("shell", True, "Shell private packages")):
            r = httpx.post(f"{url}/v1/orgs", headers=h, timeout=10,
                           json={"slug": slug, "display_name": slug.title(),
                                 "description": desc, "is_private": priv})
            r.raise_for_status()

        def publish(name, org):
            httpx.post(f"{url}/v1/publish", headers=h, timeout=15,
                       params={"name": name, "version": "1.0.0", "platform": "linux",
                               "arch": "x86_64", "org": org, "required_deps": json.dumps([])},
                       files={"file": (f"{name}.tar.zst", b"x" * 64, "application/octet-stream")}
                       ).raise_for_status()

        publish("acme-lib", "acme")
        publish("shell-lib", "shell")
        yield url
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


# ── Org list page (anonymous) ───────────────────────────────────


class TestOrgListPage:
    def test_lists_public_org_hides_private(self, page, seeded_server):
        page.goto(f"{seeded_server}/orgs")
        page.wait_for_selector("#orgs-list a", timeout=10_000)
        body = page.inner_text("#orgs-list")
        assert "Acme" in body           # public org shown
        assert "Shell" not in body      # private org hidden from anonymous

    def test_no_storage_or_private_badge_for_anonymous(self, page, seeded_server):
        page.goto(f"{seeded_server}/orgs")
        page.wait_for_selector("#orgs-list a", timeout=10_000)
        html = page.inner_html("#orgs-list")
        # Storage figures are member/admin-only; must not render for anonymous.
        assert "Storage:" not in html
        # Only public org visible -> no private badge either.
        assert "private" not in html.lower()

    def test_no_console_errors(self, page, seeded_server):
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"{seeded_server}/orgs")
        page.wait_for_selector("#orgs-list a", timeout=10_000)
        assert errors == []


# ── Org detail page (anonymous) ─────────────────────────────────


class TestOrgDetailPage:
    def test_public_org_renders_info_and_packages(self, page, seeded_server):
        page.goto(f"{seeded_server}/org/acme")
        page.wait_for_selector("#org-packages table", timeout=10_000)
        assert page.inner_text("#org-name").strip() == "Acme"
        assert "Acme public packages" in page.inner_text("#org-desc")
        assert "acme-lib" in page.inner_text("#org-packages")
        assert page.inner_text("#org-pkg-count").strip() == "1"

    def test_public_org_storage_card_hidden_for_anonymous(self, page, seeded_server):
        page.goto(f"{seeded_server}/org/acme")
        page.wait_for_selector("#org-packages table", timeout=10_000)
        # Storage column is present in the shell but hidden once the API nulls it.
        assert not page.is_visible("#org-storage-col")
        assert not page.is_visible("#org-private-badge")

    def test_private_org_not_reachable_anonymously(self, page, seeded_server):
        page.goto(f"{seeded_server}/org/shell")
        # The API 404s the private org; the page reports a load failure and
        # never renders the private package.
        page.wait_for_selector("#org-packages", timeout=10_000)
        assert "shell-lib" not in page.inner_text("#org-packages")


# ── Landing page + package detail (anonymous, visibility) ───────


class TestLandingPackageList:
    def test_public_package_listed_private_hidden(self, page, seeded_server):
        page.goto(seeded_server)
        page.wait_for_function(
            "() => { const b = document.querySelector('#pkg-body');"
            " return b && b.textContent.includes('acme-lib'); }",
            timeout=10_000,
        )
        body = page.inner_text("#pkg-body")
        assert "acme-lib" in body            # public-org package visible
        assert "shell-lib" not in body       # private package hidden from anonymous

    def test_search_box_filters_to_public_package(self, page, seeded_server):
        page.goto(seeded_server)
        page.fill("#search", "acme-lib")
        page.wait_for_function(
            "() => { const b = document.querySelector('#pkg-body');"
            " return b && b.textContent.includes('acme-lib'); }",
            timeout=10_000,
        )
        assert "shell-lib" not in page.inner_text("#pkg-body")


class TestPackageDetail:
    def test_public_package_detail_renders(self, page, seeded_server):
        page.goto(f"{seeded_server}/package/acme-lib?org=acme")
        page.wait_for_function(
            "() => { const v = document.querySelector('#pkg-version');"
            " return v && v.textContent.includes('1.0.0'); }",
            timeout=10_000,
        )
        assert "acme-lib" in page.inner_text("#pkg-title")
