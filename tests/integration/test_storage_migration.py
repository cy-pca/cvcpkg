"""End-to-end storage migration against a running cvcpkg server.

Proves the pieces the offline unit tests can't:

  * a *running* FastAPI server, after a ``file://`` → new-backend migration and
    a restart, serves package archives out of the MIGRATED backend over real
    HTTP (the source archives are removed first, so a server still reading the
    old location would 404);
  * the ``cvcpkg-server storage doctor`` CLI detects a botched migration and
    ``--heal`` repairs it, exiting non-zero only while problems remain.

Uses the local ``file://`` backend for the destination so the spawned server
process and the test share it through the filesystem; the ``s3://`` code path
is covered by ``test_storage_migration_s3.py``.
"""

from __future__ import annotations

import hashlib
import io
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
import yaml

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")


# ── server spawn helpers (YAML-index mode: no DB) ───────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(port: int, state_dir: str) -> None:
    os.environ["CVCPKG_SERVER_STATE_DIR"] = state_dir
    os.environ.pop("CVCPKG_DATABASE_URL", None)  # YAML index mode
    import uvicorn

    from cvcpkg.server.app import create_app

    uvicorn.run(create_app, host="127.0.0.1", port=port, log_level="warning", factory=True)


def _wait(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=3).status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server at {url} did not start")


class _Server:
    def __init__(self, state_dir: Path):
        self.state_dir = str(state_dir)
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        ctx = multiprocessing.get_context("spawn")
        self.proc = ctx.Process(target=_run_server, args=(self.port, self.state_dir), daemon=True)

    def __enter__(self) -> _Server:
        self.proc.start()
        try:
            _wait(self.base_url)
        except RuntimeError:
            self.proc.kill()
            self.proc.join(timeout=5)
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.proc.kill()
        self.proc.join(timeout=5)


def _clean_env() -> dict[str, str]:
    """Env forcing YAML-index mode, independent of the ambient environment.

    The Docker test stack exports CVCPKG_DATABASE_URL (and may export
    CVCPKG_SERVER_STATE_DIR).  Both the CLI and the spawned server read those,
    so an inherited value would put the CLI in DB mode while these tests drive
    the YAML index — the token would land in the DB the server never reads
    (401 on publish) and `storage migrate` would enumerate an empty DB catalog
    instead of the seeded index.  Every subprocess here gets them scrubbed;
    state dirs are always passed explicitly via --state-dir.
    """
    env = os.environ.copy()
    env.pop("CVCPKG_DATABASE_URL", None)
    env.pop("CVCPKG_SERVER_STATE_DIR", None)
    return env


def _cli(*args: str) -> subprocess.CompletedProcess:
    """Run `cvcpkg-server <args>` via the current interpreter."""
    return subprocess.run(
        [sys.executable, "-c", "from cvcpkg.server.cli import server_cli; server_cli()", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=_clean_env(),
    )


def _bootstrap_admin(state_dir: str) -> str:
    r = _cli(
        "token",
        "create",
        "--name",
        f"admin-{os.getpid()}",
        "--role",
        "admin",
        "--state-dir",
        state_dir,
    )
    if r.returncode != 0:
        raise RuntimeError(f"token create failed: {r.stderr}")
    for line in r.stdout.splitlines():
        if line.strip().startswith("cvctok_"):
            return line.strip()
    raise RuntimeError(f"no token in output: {r.stdout}")


def _dummy_archive(name: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = f"payload-of-{name}".encode() * 500
        info = tarfile.TarInfo(name=f"lib/lib{name}.so")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _publish(base_url: str, token: str, name: str, data: bytes) -> None:
    sha = hashlib.sha256(data).hexdigest()
    r = httpx.post(
        f"{base_url}/v1/publish",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "name": name,
            "version": "1.0.0",
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
            "sha256": sha,
        },
        files={"file": (f"{name}.tar.gz", data)},
        timeout=30,
    )
    assert r.status_code == 200, r.text


def _download(base_url: str, filename: str) -> httpx.Response:
    return httpx.get(f"{base_url}/v1/download/{filename}", timeout=30)


def _archive_name(name: str) -> str:
    return f"{name}-1.0.0-linux-x86_64-release-shared.tar.zst"


# ── Tests ───────────────────────────────────────────────────────


def test_server_serves_archives_from_migrated_backend(tmp_path):
    from cvcpkg.server import archive_store
    from cvcpkg.server.storage_migration import run_migration

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pkgs = {"alpha": _dummy_archive("alpha"), "beta": _dummy_archive("beta")}

    # Create the admin token BEFORE the server starts: in YAML-index mode the
    # token store is read into memory at startup (unlike the shared DB mode),
    # so a token added afterwards wouldn't be seen live.
    token = _bootstrap_admin(str(state_dir))

    # 1. Publish against the default file:// backend, confirm downloads work.
    with _Server(state_dir) as srv:
        for name, data in pkgs.items():
            _publish(srv.base_url, token, name, data)
        for name, data in pkgs.items():
            resp = _download(srv.base_url, _archive_name(name))
            assert resp.status_code == 200 and resp.content == data

    # 2. Migrate to a *separate* local backend directory.
    dest_dir = tmp_path / "migrated"
    dest_uri = f"file://{dest_dir}"
    result = run_migration(state_dir, dest_uri, deep_verify=True)
    assert result.ok and result.flipped

    # 3. Remove the ORIGINAL archives so a server still reading the old
    #    location would 404 — only a correctly-migrated server can serve now.
    shutil.rmtree(state_dir / archive_store.ARCHIVES_SUBDIR)

    # 4. Restart (picks up the persisted storage.yaml) and serve from dest.
    with _Server(state_dir) as srv:
        assert httpx.get(f"{srv.base_url}/healthz").status_code == 200
        for name, data in pkgs.items():
            resp = _download(srv.base_url, _archive_name(name))
            assert resp.status_code == 200, f"{name}: {resp.status_code}"
            assert resp.content == data, f"{name} bytes differ — not served from migrated backend"


def test_storage_doctor_cli_detects_and_heals(tmp_path):
    from cvcpkg.server import archive_store

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Seed a populated file:// server directly (no server process needed).
    archives = {
        _archive_name("one"): b"one-" * 4096,
        _archive_name("two"): b"two-" * 4096,
    }
    adir = state_dir / archive_store.ARCHIVES_SUBDIR
    adir.mkdir(parents=True)
    bundles = []
    for fn, data in archives.items():
        (adir / fn).write_bytes(data)
        bundles.append(
            {
                "name": fn.split("-")[0],
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "archive_url": f"/v1/download/{fn}",
            }
        )
    (state_dir / "index.yaml").write_text(yaml.safe_dump({"bundles": bundles}))

    dest = tmp_path / "dest"
    dest_uri = f"file://{dest}"
    source_uri = f"file://{state_dir}"

    # Migrate via the CLI.
    r = _cli("storage", "migrate", "--to", dest_uri, "--state-dir", str(state_dir))
    assert r.returncode == 0, r.stderr
    assert "active storage backend is now" in r.stdout

    # A clean doctor pass exits 0.
    r = _cli("storage", "doctor", "--state-dir", str(state_dir), "--deep")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout

    # Botch the destination: corrupt one migrated archive.
    victim = _archive_name("two")
    (dest / archive_store.ARCHIVES_SUBDIR / victim).write_bytes(b"corrupted")

    # doctor --deep now finds the problem and exits non-zero.
    r = _cli("storage", "doctor", "--state-dir", str(state_dir), "--deep")
    assert r.returncode == 1
    assert "CORRUPT" in r.stdout and victim in r.stdout

    # doctor --heal restores it from the still-intact source and exits 0.
    r = _cli(
        "storage",
        "doctor",
        "--state-dir",
        str(state_dir),
        "--deep",
        "--heal",
        "--source",
        source_uri,
    )
    assert r.returncode == 0, r.stderr
    assert "heal complete" in r.stdout

    # Verify on disk: destination bytes match the catalog again.
    assert (dest / archive_store.ARCHIVES_SUBDIR / victim).read_bytes() == archives[victim]
