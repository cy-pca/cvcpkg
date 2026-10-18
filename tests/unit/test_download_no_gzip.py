"""Archive downloads bypass the gzip middleware; other large responses keep it.

Archives are ``.tar.zst``.  With the app-wide GZipMiddleware, every download
from a client that sent Accept-Encoding (the cvcpkg client always does) was
recompressed on the event loop, which on 2026-10-18 was ~70% of cvcpkg.org's
backend CPU under a CI download burst, and the response lost its Content-Length.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app

_ARCHIVE = "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.zst"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    archives = tmp_path / "archives"
    archives.mkdir()
    payload = os.urandom(200_000)
    (archives / _ARCHIVE).write_bytes(payload)
    with TestClient(create_app(state_dir=tmp_path)) as c:
        yield c, payload


def test_archive_download_is_not_gzipped(client):
    c, payload = client
    r = c.get(f"/v1/download/{_ARCHIVE}", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.headers["content-length"] == str(len(payload))
    assert r.content == payload


def test_other_large_responses_are_still_gzipped(client):
    c, _payload = client
    r = c.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
