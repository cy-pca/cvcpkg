"""Integration test for cvcpkg.catalog.fetch_catalog against a real HTTP server.

Regression coverage for a real-world outage where ``pkg.tx.wtf/v1/catalog``
answered ``200 OK`` on ``GET`` but ``405 Method Not Allowed`` on ``HEAD``,
causing every ``cvcpkg install`` invocation to abort before touching a recipe.

The client now treats the HEAD probe as an optional size hint and falls
through to ``GET`` when HEAD is refused.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from cvcpkg.catalog import fetch_catalog
from cvcpkg.errors import CatalogError

_CATALOG_PAYLOAD = yaml.safe_dump(
    {
        "schema_version": 1,
        "bundles": [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "a" * 64,
                "archive_url": "https://example.invalid/zlib.tar.zst",
            }
        ],
    }
).encode()


def _make_handler(head_status: int, get_status: int = 200, body: bytes = _CATALOG_PAYLOAD):
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            self.send_response(head_status)
            if head_status == 200:
                self.send_header("Content-Type", "application/yaml")
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            self.send_response(get_status)
            self.send_header("Content-Type", "application/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if get_status == 200:
                self.wfile.write(body)

        def log_message(self, *args, **kwargs) -> None:  # silence test output
            pass

    return Handler


def _serve(handler_cls, *, requests: int = 1) -> tuple[HTTPServer, threading.Thread, int]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]

    def _run() -> None:
        for _ in range(requests):
            server.handle_request()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return server, thread, port


class TestFetchCatalogHeadRefused:
    """HEAD returning 4xx/5xx must not prevent the GET from succeeding."""

    @pytest.mark.parametrize("head_status", [403, 405, 501])
    def test_head_refused_falls_through_to_get(self, tmp_path, head_status):
        # HEAD probe + GET → 2 requests to serve.
        server, thread, port = _serve(_make_handler(head_status), requests=2)
        try:
            cat = fetch_catalog(f"http://127.0.0.1:{port}/v1/catalog", cache_dir=tmp_path)
        finally:
            server.server_close()
            thread.join(timeout=2)

        assert cat["schema_version"] == 1
        assert cat["bundles"][0]["name"] == "zlib"

    def test_get_still_authoritative(self, tmp_path):
        """A 500 on GET is still a real failure even if HEAD was refused."""
        server, thread, port = _serve(_make_handler(head_status=405, get_status=500), requests=2)
        try:
            with pytest.raises(CatalogError):
                fetch_catalog(f"http://127.0.0.1:{port}/v1/catalog", cache_dir=tmp_path)
        finally:
            server.server_close()
            thread.join(timeout=2)


class TestFetchCatalogHappyPath:
    """Sanity: when HEAD works normally, everything still works."""

    def test_head_ok_get_ok(self, tmp_path):
        server, thread, port = _serve(_make_handler(head_status=200), requests=2)
        try:
            cat = fetch_catalog(f"http://127.0.0.1:{port}/v1/catalog", cache_dir=tmp_path)
        finally:
            server.server_close()
            thread.join(timeout=2)

        assert cat["bundles"][0]["name"] == "zlib"
