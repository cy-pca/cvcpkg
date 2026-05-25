"""Multi-backend database integration tests for cvcpkg-server.

Verifies that the server works correctly against multiple database
backends:

    1. SQLite  (in-memory via ``sqlite+aiosqlite://``)
    2. MySQL   (via ``mysql+aiomysql://``, in the Docker test stack)
    3. PostgreSQL (already tested by test_e2e_lifecycle.py)

Each backend is tested through a fresh server process started
programmatically.  The tests exercise the core lifecycle:

    - Health check
    - Admin token bootstrap via CLI
    - Token CRUD via REST
    - Publish a package via REST
    - Catalog query
    - Yank / unyank
    - Audit trail

Run via Docker Compose::

    cd tools/cvcpkg
    docker compose -f docker-compose.test.yml run --rm test \
        pytest tests/integration/test_multi_backend.py -v

Environment:
    CVCPKG_TEST_MYSQL_URL  — MySQL DSN
                             (default: mysql+aiomysql://cvcpkg_test:testpass@mysql:3306/cvcpkg_test)
"""

from __future__ import annotations

import hashlib
import io
import multiprocessing
import os
import socket
import tarfile
import time
from pathlib import Path
from typing import Generator

import pytest
import yaml

httpx = pytest.importorskip("httpx", reason="httpx required for multi-backend tests")

_TOKEN_COUNTER = 0


# ── Helpers ─────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(db_url: str, port: int, state_dir: str) -> None:
    """Start the cvcpkg-server in this process.  Called by multiprocessing."""
    os.environ["CVCPKG_DATABASE_URL"] = db_url
    import uvicorn

    from cvcpkg.server.app import create_app  # noqa: E402

    uvicorn.run(
        create_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        factory=True,
    )


def _wait_for_server(url: str, timeout: float = 30) -> None:
    """Block until the server at *url* responds to /healthz."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/healthz", timeout=3)
            if r.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server at {url} did not start within {timeout}s")


def _bootstrap_admin(state_dir: str, db_url: str) -> str:
    """Bootstrap an admin token via the CLI and return the raw token.

    Runs ``cvcpkg-server token create`` as a subprocess with the correct
    CVCPKG_DATABASE_URL so the token is written to the same database
    that the running server uses.
    """
    global _TOKEN_COUNTER
    _TOKEN_COUNTER += 1
    import subprocess

    env = os.environ.copy()
    env["CVCPKG_DATABASE_URL"] = db_url

    token_name = f"admin-{os.getpid()}-{_TOKEN_COUNTER}"
    result = subprocess.run(
        [
            "cvcpkg-server",
            "token",
            "create",
            "--name",
            token_name,
            "--role",
            "admin",
            "--state-dir",
            state_dir,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Token create failed: {result.stderr}")

    for line in result.stdout.split("\n"):
        line = line.strip()
        if line.startswith("cvctok_"):
            return line

    raise RuntimeError(f"Could not extract admin token from: {result.stdout}")


def _make_dummy_archive(name: str = "testpkg", version: str = "1.0.0") -> bytes:
    """Create a minimal tar.gz bundle with a manifest.yaml."""
    manifest = {
        "name": name,
        "version": version,
        "platform": "linux",
        "arch": "x86_64",
        "config": "release",
        "link": "shared",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # manifest.yaml
        manifest_bytes = yaml.safe_dump(manifest).encode()
        info = tarfile.TarInfo(name="manifest.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # dummy data file
        data = b"hello from testpkg"
        info2 = tarfile.TarInfo(name="lib/libtestpkg.so")
        info2.size = len(data)
        tar.addfile(info2, io.BytesIO(data))
    return buf.getvalue()


# ── Fixtures ────────────────────────────────────────────────────


class ServerInstance:
    """Tracks a running server process for a specific backend."""

    def __init__(
        self, db_url: str, base_url: str, process: multiprocessing.Process, state_dir: str
    ):
        self.db_url = db_url
        self.base_url = base_url
        self.process = process
        self.state_dir = state_dir


@pytest.fixture(params=["sqlite", "mysql"])
def server(request, tmp_path) -> Generator[ServerInstance, None, None]:
    """Start a fresh cvcpkg-server per backend, yield it, then kill."""
    backend = request.param
    port = _free_port()
    state_dir = str(tmp_path / f"state-{backend}")
    os.makedirs(state_dir, exist_ok=True)

    if backend == "sqlite":
        db_path = tmp_path / f"test-{backend}.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
    elif backend == "mysql":
        db_url = os.environ.get(
            "CVCPKG_TEST_MYSQL_URL",
            "mysql+aiomysql://cvcpkg_test:testpass@mysql:3306/cvcpkg_test",
        )
        # Check MySQL reachability early
        try:
            import aiomysql  # noqa: F401
        except ImportError:
            pytest.skip("aiomysql not installed — skipping MySQL backend")
    else:
        pytest.skip(f"Unknown backend: {backend}")

    proc = multiprocessing.Process(
        target=_run_server,
        args=(db_url, port, state_dir),
        daemon=True,
    )
    proc.start()

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(base_url)
    except RuntimeError:
        proc.kill()
        proc.join(timeout=5)
        pytest.fail(f"Server ({backend}) failed to start on port {port}")

    yield ServerInstance(db_url=db_url, base_url=base_url, process=proc, state_dir=state_dir)

    proc.kill()
    proc.join(timeout=5)


# ── Tests ───────────────────────────────────────────────────────


class TestMultiBackendLifecycle:
    """Run core lifecycle against each database backend."""

    def test_health(self, server: ServerInstance) -> None:
        r = httpx.get(f"{server.base_url}/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_token_lifecycle(self, server: ServerInstance) -> None:
        """Admin can create and revoke tokens."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)

        headers = {"Authorization": f"Bearer {admin_token}"}

        # Create publisher token
        r = httpx.post(
            f"{server.base_url}/v1/tokens",
            json={"name": "test-publisher", "role": "publisher"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        publisher_token = r.json()["token"]
        assert publisher_token.startswith("cvctok_")

        # Create reader token
        r = httpx.post(
            f"{server.base_url}/v1/tokens",
            json={"name": "test-reader", "role": "reader"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        # List tokens
        r = httpx.get(f"{server.base_url}/v1/tokens", headers=headers)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "test-admin" in names
        assert "test-publisher" in names
        assert "test-reader" in names

        # Revoke reader
        r = httpx.delete(f"{server.base_url}/v1/tokens/test-reader", headers=headers)
        assert r.status_code == 200

    def test_publish_and_catalog(self, server: ServerInstance) -> None:
        """Publisher token can upload a package and it appears in catalog."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # Grant publisher
        r = httpx.post(
            f"{server.base_url}/v1/tokens",
            json={"name": "pub", "role": "publisher"},
            headers=headers_admin,
        )
        assert r.status_code == 200
        pub_token = r.json()["token"]

        # Publish
        archive = _make_dummy_archive("mypkg", "2.0.0")
        sha = hashlib.sha256(archive).hexdigest()

        r = httpx.post(
            f"{server.base_url}/v1/publish",
            headers={"Authorization": f"Bearer {pub_token}"},
            params={
                "name": "mypkg",
                "version": "2.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": sha,
            },
            files={"file": ("mypkg-2.0.0-linux-x86_64.tar.gz", archive)},
        )
        assert r.status_code == 200, r.text

        # Catalog
        r = httpx.get(f"{server.base_url}/v1/catalog")
        assert r.status_code == 200
        catalog = r.json()
        pkg_names = [p["name"] for p in catalog]
        assert "mypkg" in pkg_names

        # Packages endpoint
        r = httpx.get(f"{server.base_url}/v1/packages/mypkg")
        assert r.status_code == 200
        versions = [p["version"] for p in r.json()]
        assert "2.0.0" in versions

    def test_yank_unyank(self, server: ServerInstance) -> None:
        """Admin can yank and unyank packages."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Publish first
        archive = _make_dummy_archive("yankpkg", "1.0.0")
        sha = hashlib.sha256(archive).hexdigest()
        r = httpx.post(
            f"{server.base_url}/v1/publish",
            headers=headers,
            params={
                "name": "yankpkg",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": sha,
            },
            files={"file": ("yankpkg-1.0.0.tar.gz", archive)},
        )
        assert r.status_code == 200

        # Yank
        r = httpx.post(f"{server.base_url}/v1/packages/yankpkg/1.0.0/yank", headers=headers)
        assert r.status_code == 200

        # Verify yanked (not in catalog)
        r = httpx.get(f"{server.base_url}/v1/catalog")
        pkg_names = [p["name"] for p in r.json()]
        assert "yankpkg" not in pkg_names

        # Unyank
        r = httpx.post(f"{server.base_url}/v1/packages/yankpkg/1.0.0/unyank", headers=headers)
        assert r.status_code == 200

        # Back in catalog
        r = httpx.get(f"{server.base_url}/v1/catalog")
        pkg_names = [p["name"] for p in r.json()]
        assert "yankpkg" in pkg_names

    def test_rbac_enforcement(self, server: ServerInstance) -> None:
        """Reader tokens cannot publish; unauthenticated gets 401 on admin routes."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # Create reader
        r = httpx.post(
            f"{server.base_url}/v1/tokens",
            json={"name": "only-reader", "role": "reader"},
            headers=headers_admin,
        )
        assert r.status_code == 200
        reader_token = r.json()["token"]

        # Reader cannot publish
        archive = _make_dummy_archive("forbidden", "0.0.1")
        sha = hashlib.sha256(archive).hexdigest()
        r = httpx.post(
            f"{server.base_url}/v1/publish",
            headers={"Authorization": f"Bearer {reader_token}"},
            params={
                "name": "forbidden",
                "version": "0.0.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": sha,
            },
            files={"file": ("forbidden.tar.gz", archive)},
        )
        assert r.status_code == 403

        # No auth on admin endpoint
        r = httpx.get(f"{server.base_url}/v1/tokens")
        assert r.status_code == 401

    def test_audit_trail(self, server: ServerInstance) -> None:
        """Audit log records actions and the chain is valid."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Perform some actions to generate audit entries
        archive = _make_dummy_archive("auditpkg", "3.0.0")
        sha = hashlib.sha256(archive).hexdigest()
        httpx.post(
            f"{server.base_url}/v1/publish",
            headers=headers,
            params={
                "name": "auditpkg",
                "version": "3.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": sha,
            },
            files={"file": ("auditpkg.tar.gz", archive)},
        )

        # Query audit log
        r = httpx.get(f"{server.base_url}/v1/audit", headers=headers)
        assert r.status_code == 200
        entries = r.json()
        assert len(entries) > 0

        # Verify chain integrity
        r = httpx.get(f"{server.base_url}/v1/audit/verify", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") in ("ok", "valid")

    def test_duplicate_publish_rejected(self, server: ServerInstance) -> None:
        """Publishing the same version/variant twice returns 409."""
        admin_token = _bootstrap_admin(server.state_dir, server.db_url)
        headers = {"Authorization": f"Bearer {admin_token}"}

        archive = _make_dummy_archive("duppkg", "1.0.0")
        sha = hashlib.sha256(archive).hexdigest()
        params = {
            "name": "duppkg",
            "version": "1.0.0",
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
            "sha256": sha,
        }

        # First publish succeeds
        r = httpx.post(
            f"{server.base_url}/v1/publish",
            headers=headers,
            params=params,
            files={"file": ("duppkg.tar.gz", archive)},
        )
        assert r.status_code == 200

        # Second publish — same variant — 409
        r = httpx.post(
            f"{server.base_url}/v1/publish",
            headers=headers,
            params=params,
            files={"file": ("duppkg.tar.gz", archive)},
        )
        assert r.status_code == 409
