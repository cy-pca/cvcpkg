"""The one-line installers install the registry's own ``cvcpkg`` package.

``/install.sh`` and ``/install.ps1`` used to fetch the standalone binary from
GitHub releases unconditionally, so a fork's deployment handed its users the
upstream build.  They now ask the registry that served them for its newest
``cvcpkg`` bundle (``GET /v1/packages/cvcpkg/latest``) and fall back to GitHub
only on a 404.  These tests pin the endpoint's selection rules, then run the
real ``install.sh`` against the real app on a real socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
uvicorn = pytest.importorskip("uvicorn", reason="server extras not installed")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.platform import detect_arch, detect_platform  # noqa: E402
from cvcpkg.server.app import create_app  # noqa: E402
from cvcpkg.server.auth import TokenStore  # noqa: E402
from cvcpkg.server.models import TokenRole  # noqa: E402

LATEST = "/v1/packages/cvcpkg/latest"


def _bundle(message: str) -> bytes:
    """A cvcpkg bundle as a recipe would publish it: bin/cvcpkg in a tar.gz."""
    script = f'#!/bin/sh\necho "{message}"\n'.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("bin/cvcpkg")
        info.size = len(script)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(script))
    return buf.getvalue()


def _windows_bundle(message: str) -> bytes:
    """A Windows cvcpkg bundle as the fleet publishes one: a zip holding bin/cvcpkg.exe.

    The "exe" is a shell script with the Unix exec bit recorded in the zip, so
    the closing `& $DestPath --version` of install.ps1 can run it on the
    non-Windows host these tests run on.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("bin/cvcpkg.exe")
        info.create_system = 3  # Unix, so external_attr carries a mode
        info.external_attr = 0o100755 << 16
        z.writestr(info, f'#!/bin/sh\necho "{message}"\n')
    return buf.getvalue()


def _publish(client, token, version, *, platform="linux", arch="x86_64", content=None, **params):
    q = {
        "name": "cvcpkg",
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "shared",
        **params,
    }
    body = content if content is not None else _bundle(f"cvcpkg {version}")
    r = client.post(
        "/v1/publish",
        params=q,
        files={"file": (f"cvcpkg-{version}.tar.zst", io.BytesIO(body))},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 201), r.text
    return r


@contextlib.contextmanager
def _registry(backend, tmp_path, monkeypatch):
    """The app on *backend* ("yaml" or "sqlite"), with admin and publisher tokens."""
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    if backend == "yaml":
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        store = TokenStore(tmp_path)
        admin = store.create("admin-user", TokenRole.admin)
        publisher = store.create("pub-user", TokenRole.publisher)
    else:
        pytest.importorskip("aiosqlite", reason="aiosqlite required for the DB backend")
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'latest.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _seed():
            init_db(db_url)
            await create_tables()
            store = DbTokenStore(tmp_path)
            tokens = (
                await store.create("admin-user", TokenRole.admin),
                await store.create("pub-user", TokenRole.publisher),
            )
            await dispose_engine()
            return tokens

        admin, publisher = asyncio.run(_seed())
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, admin, publisher


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    with _registry("yaml", tmp_path, monkeypatch) as r:
        yield r


@pytest.fixture(params=["yaml", "sqlite"])
def any_registry(request, tmp_path, monkeypatch):
    """Both backends, for what they fetch differently.

    The endpoint picks the winner in Python either way, but gets its
    candidates from ``state.index`` on one and ``get_bundles`` (which does the
    platform / arch / build-type / yanked / org filtering in SQL) on the other.
    A DB app takes ~10s to start, so the pure selection rules run on yaml only.
    """
    with _registry(request.param, tmp_path, monkeypatch) as r:
        yield r


class TestLatestEndpoint:
    def test_newest_by_version_not_publish_order(self, any_registry):
        client, _admin, pub = any_registry
        # Published out of order, and 2.10 must beat 2.9 numerically.
        for v in ("2.10.0+cvc.1", "2.9.0+cvc.1", "2.10.0+cvc.3", "2.10.0+cvc.2"):
            _publish(client, pub, v)
        r = client.get(LATEST, params={"platform": "linux", "arch": "x86_64"})
        assert r.status_code == 200, r.text
        assert r.json()["version"] == "2.10.0+cvc.3"
        assert r.json()["archive_url"].startswith("/v1/download/")
        assert len(r.json()["sha256"]) == 64

    def test_filters_platform_arch_and_build_type(self, any_registry):
        client, _admin, pub = any_registry
        _publish(client, pub, "2.0.0+cvc.1", platform="linux", arch="x86_64")
        _publish(client, pub, "3.0.0+cvc.1", platform="freebsd", arch="x86_64")
        _publish(client, pub, "4.0.0+cvc.1", platform="linux", arch="arm64")
        _publish(client, pub, "5.0.0+cvc.1", platform="linux", arch="x86_64", build_type="debug")
        got = client.get(LATEST, params={"platform": "linux", "arch": "x86_64"}).json()
        assert got["version"] == "2.0.0+cvc.1"
        assert client.get(LATEST, params={"platform": "macos", "arch": "arm64"}).status_code == 404

    def test_skips_yanked(self, any_registry):
        client, admin, pub = any_registry
        _publish(client, pub, "2.0.0+cvc.1")
        _publish(client, pub, "2.1.0+cvc.1")
        r = client.post(
            "/v1/packages/cvcpkg/2.1.0+cvc.1/yank",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200, r.text
        got = client.get(LATEST, params={"platform": "linux", "arch": "x86_64"}).json()
        assert got["version"] == "2.0.0+cvc.1"

    def test_prereleases_only_when_pinned(self, registry):
        client, _admin, pub = registry
        _publish(client, pub, "2.0.0+cvc.1")
        _publish(client, pub, "2.1.0-rc.1+cvc.1")
        q = {"platform": "linux", "arch": "x86_64"}
        assert client.get(LATEST, params=q).json()["version"] == "2.0.0+cvc.1"
        pinned = client.get(LATEST, params={**q, "version": "2.1.0-rc.1"}).json()
        assert pinned["version"] == "2.1.0-rc.1+cvc.1"

    def test_version_pin(self, registry):
        client, _admin, pub = registry
        for v in ("2.0.0+cvc.1", "2.0.0+cvc.2", "2.1.0+cvc.1"):
            _publish(client, pub, v)
        q = {"platform": "linux", "arch": "x86_64"}
        # A bare version takes its newest repackaging; +cvc.N pins exactly.
        assert client.get(LATEST, params={**q, "version": "2.0.0"}).json()["version"] == (
            "2.0.0+cvc.2"
        )
        assert client.get(LATEST, params={**q, "version": "2.0.0+cvc.1"}).json()["version"] == (
            "2.0.0+cvc.1"
        )
        assert client.get(LATEST, params={**q, "version": "<2.1"}).json()["version"] == (
            "2.0.0+cvc.2"
        )
        assert client.get(LATEST, params={**q, "version": "3.0.0"}).status_code == 404
        assert client.get(LATEST, params={**q, "version": "not a version"}).status_code == 422

    def test_org_package_cannot_take_over(self, any_registry):
        # The installers execute what this returns.  An org that publishes its
        # own "cvcpkg" at a higher version must not become the answer.
        client, _admin, pub = any_registry
        # The DB backend only takes org publishes from members of a real org
        # (the creator is one); the YAML backend has no orgs to create.
        client.post(
            "/v1/orgs",
            headers={"Authorization": f"Bearer {pub}"},
            json={"slug": "someorg", "display_name": "Some Org"},
        )
        _publish(client, pub, "2.0.0+cvc.1")
        _publish(client, pub, "99.0.0+cvc.1", org="someorg", content=_bundle("not cvcpkg"))
        q = {"platform": "linux", "arch": "x86_64"}
        got = client.get(LATEST, params=q).json()
        assert got["version"] == "2.0.0+cvc.1"
        assert got["org"] == ""
        assert client.get(LATEST, params={**q, "org": "someorg"}).json()["version"] == (
            "99.0.0+cvc.1"
        )

    def test_404_when_none(self, registry):
        client, _admin, _pub = registry
        r = client.get(LATEST, params={"platform": "linux", "arch": "x86_64"})
        assert r.status_code == 404


# ── install.sh, end to end ──────────────────────────────────────

needs_posix_sh = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("sh") or not shutil.which("curl"),
    reason="install.sh needs a POSIX sh and curl",
)


@contextlib.contextmanager
def _serve(app):
    """Run *app* on a real uvicorn socket in a thread; yield its base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _install_sh(tmp_path, *args, **env):
    from importlib.resources import files

    script = files("cvcpkg.server").joinpath("assets", "install.sh").read_text(encoding="utf-8")
    # A clean environment: the developer's own CVCPKG_SERVER_URL / CVCPKG_TOKEN
    # must not leak into what is being tested.
    run_env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"), **env}
    return subprocess.run(
        ["sh", "-s", "--", *args],
        input=script,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _seed_and_serve(tmp_path, monkeypatch, **app_kwargs):
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    state = tmp_path / "state"
    store = TokenStore(state)
    publisher = store.create("pub-user", TokenRole.publisher)
    reader = store.create("read-user", TokenRole.reader)
    app = create_app(state_dir=state, **app_kwargs)
    host = {"platform": detect_platform(), "arch": detect_arch()}
    with TestClient(app) as client:
        _publish(client, publisher, "2.0.0+cvc.1", content=_bundle("cvcpkg 2.0.0 (old)"), **host)
        _publish(
            client, publisher, "2.1.0+cvc.1", content=_bundle("cvcpkg 2.1.0 (registry)"), **host
        )
        win = {"platform": "windows", "arch": "x86_64"}
        for v, msg in (("2.0.0+cvc.1", "old"), ("2.1.0+cvc.1", "registry")):
            body = _windows_bundle(f"cvcpkg {v.split('+')[0]} ({msg}, windows)")
            _publish(client, publisher, v, content=body, **win)
    return app, reader


@needs_posix_sh
class TestInstallShAgainstRegistry:
    def test_installs_the_registrys_newest_build(self, tmp_path, monkeypatch):
        app, _reader = _seed_and_serve(tmp_path, monkeypatch)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            r = _install_sh(tmp_path, "--server", url, "--install-dir", str(dest))
        assert r.returncode == 0, r.stderr
        assert (
            f"Installing cvcpkg 2.1.0+cvc.1 for {detect_platform()}/{detect_arch()} from {url}"
            in (r.stderr)
        )
        assert "GitHub" not in r.stderr
        assert (dest / "cvcpkg").read_text() == '#!/bin/sh\necho "cvcpkg 2.1.0 (registry)"\n'
        assert "cvcpkg 2.1.0 (registry)" in r.stdout  # the post-install --version

    def test_version_pin_and_server_from_env(self, tmp_path, monkeypatch):
        app, _reader = _seed_and_serve(tmp_path, monkeypatch)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            # The release-tag spelling still works as a pin.
            r = _install_sh(
                tmp_path,
                CVCPKG_SERVER_URL=url + "/",
                CVCPKG_VERSION="cvcpkg-v2.0.0",
                CVCPKG_INSTALL_DIR=str(dest),
            )
        assert r.returncode == 0, r.stderr
        assert "cvcpkg 2.0.0 (old)" in (dest / "cvcpkg").read_text()

    def test_token_for_a_read_gated_registry(self, tmp_path, monkeypatch):
        app, reader = _seed_and_serve(tmp_path, monkeypatch, require_auth_for_reads=True)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            denied = _install_sh(tmp_path, "--server", url, "--install-dir", str(dest))
            allowed = _install_sh(
                tmp_path, "--server", url, "--install-dir", str(dest), CVCPKG_TOKEN=reader
            )
        # No silent fallback to upstream's build: the registry exists, we just
        # may not read it.
        assert denied.returncode != 0
        assert "set CVCPKG_TOKEN" in denied.stderr
        assert "GitHub" not in denied.stderr
        assert allowed.returncode == 0, allowed.stderr
        assert "cvcpkg 2.1.0 (registry)" in (dest / "cvcpkg").read_text()

    def test_unreachable_registry_does_not_fall_back(self, tmp_path):
        # Port 9 (discard) on loopback: nothing listens there.
        r = _install_sh(tmp_path, "--server", "http://127.0.0.1:9", "--install-dir", str(tmp_path))
        assert r.returncode != 0
        assert "could not reach http://127.0.0.1:9" in r.stderr
        assert "GitHub" not in r.stderr
        assert not (tmp_path / "cvcpkg").exists()


# ── install.ps1, end to end (under PowerShell 7 on a non-Windows host) ─────

PWSH = os.environ.get("CVCPKG_TEST_PWSH") or shutil.which("pwsh")

needs_pwsh = pytest.mark.skipif(
    not PWSH or sys.platform == "win32",
    reason="needs pwsh (set CVCPKG_TEST_PWSH or put it on PATH); on Windows the stand-in "
    "cvcpkg.exe shell script cannot run",
)


def _install_ps1(tmp_path, *args, arch="AMD64", **env):
    """Run install.ps1 with the Windows environment it reads simulated."""
    from importlib.resources import files

    script = files("cvcpkg.server").joinpath("assets", "install.ps1")
    home = tmp_path / "pshome"
    (home / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
    (home / "Temp").mkdir(exist_ok=True)
    run_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "TEMP": str(home / "Temp"),
        "PROCESSOR_ARCHITECTURE": arch,
        "NO_COLOR": "1",
        **env,
    }
    r = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return r.returncode, r.stdout + r.stderr


@needs_pwsh
class TestInstallPs1AgainstRegistry:
    def test_installs_the_registrys_newest_build(self, tmp_path, monkeypatch):
        app, _reader = _seed_and_serve(tmp_path, monkeypatch)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            rc, out = _install_ps1(tmp_path, "-Server", url, "-InstallDir", str(dest))
        assert rc == 0, out
        assert f"Installing cvcpkg 2.1.0+cvc.1 for windows/x86_64 from {url}" in out
        assert "GitHub" not in out
        assert "cvcpkg 2.1.0 (registry, windows)" in (dest / "cvcpkg.exe").read_text()

    def test_version_pin_and_server_from_env(self, tmp_path, monkeypatch):
        app, _reader = _seed_and_serve(tmp_path, monkeypatch)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            rc, out = _install_ps1(
                tmp_path,
                CVCPKG_SERVER_URL=url + "/",
                CVCPKG_VERSION="cvcpkg-v2.0.0",
                CVCPKG_INSTALL_DIR=str(dest),
            )
        assert rc == 0, out
        assert "cvcpkg 2.0.0 (old, windows)" in (dest / "cvcpkg.exe").read_text()

    def test_32bit_powershell_on_64bit_windows(self, tmp_path, monkeypatch):
        # WOW64 reports x86 in PROCESSOR_ARCHITECTURE; the machine is in ...W6432.
        app, _reader = _seed_and_serve(tmp_path, monkeypatch)
        with _serve(app) as url:
            rc, out = _install_ps1(
                tmp_path,
                "-Server",
                url,
                "-InstallDir",
                str(tmp_path / "bin"),
                arch="x86",
                PROCESSOR_ARCHITEW6432="AMD64",
            )
        assert rc == 0, out
        assert "for windows/x86_64" in out

    def test_token_for_a_read_gated_registry(self, tmp_path, monkeypatch):
        app, reader = _seed_and_serve(tmp_path, monkeypatch, require_auth_for_reads=True)
        dest = tmp_path / "bin"
        with _serve(app) as url:
            denied = _install_ps1(tmp_path, "-Server", url, "-InstallDir", str(dest))
            allowed = _install_ps1(
                tmp_path, "-Server", url, "-InstallDir", str(dest), CVCPKG_TOKEN=reader
            )
        rc, out = denied
        assert rc != 0
        assert "CVCPKG_TOKEN" in out
        assert "GitHub" not in out
        rc, out = allowed
        assert rc == 0, out
        assert "cvcpkg 2.1.0 (registry, windows)" in (dest / "cvcpkg.exe").read_text()

    def test_unreachable_registry_does_not_fall_back(self, tmp_path):
        rc, out = _install_ps1(
            tmp_path, "-Server", "http://127.0.0.1:9", "-InstallDir", str(tmp_path / "bin")
        )
        assert rc != 0
        assert "could not look up cvcpkg on http://127.0.0.1:9" in out
        assert "GitHub" not in out
        assert not (tmp_path / "bin" / "cvcpkg.exe").exists()
