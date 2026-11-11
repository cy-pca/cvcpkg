"""requires_capabilities must survive the publish -> store -> catalog round trip.

libcvc-cuda declares ``provides: [libcvc]`` and ``requires_capabilities:
[cuda]``, and the client resolver drops a provider whose capabilities the host
lacks.  The bundle manifest carried both, but the server stored only
``provides``, so the catalog never served the capability and the filter never
fired.  On 2026-11-10 libcvc-cuda 3.7.13+cvc.1 was published two hours before
libcvc 3.7.13+cvc.2; in between, a dependency on ``libcvc`` resolved to the
higher-versioned CUDA build on GitHub's ubuntu runners, and CMake configure
failed (``cvc::cvc`` links ``CUDA::cudart_static``).

Covered here, on the database and the JSON-index backends: every publish path
(query parameter, multipart field, chunked-upload JSON body) records the list,
``/v1/catalog`` and ``/v1/packages/{name}`` serve it, malformed lists are
refused, and a host without CUDA that needs ``libcvc`` resolves the catalog to
libcvc rather than the newer libcvc-cuda.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from cvcpkg.catalog import catalog_entries
from cvcpkg.manifest import ComponentReq
from cvcpkg.resolver import resolve

VARIANT = {"platform": "linux", "arch": "x86_64", "build_type": "release", "link": "shared"}


@pytest.fixture(params=["db", "index"])
def server(request, tmp_path, monkeypatch):
    """A TestClient and admin auth header, on either catalog backend."""
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.models import TokenRole

    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    if request.param == "db":
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        db_url = f"sqlite+aiosqlite:///{tmp_path / 'caps.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        async def _seed():
            init_db(db_url)
            await create_tables()
            token = await DbTokenStore(tmp_path).create("test-admin", TokenRole.admin)
            await dispose_engine()
            return token

        token = asyncio.run(_seed())
    else:
        from cvcpkg.server.auth import TokenStore

        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        token = TokenStore(tmp_path).create("test-admin", TokenRole.admin)
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, {"Authorization": f"Bearer {token}"}


def _publish(client, hdr, name, version, *, data=None, **query):
    resp = client.post(
        "/v1/publish",
        params={"name": name, "version": version, **VARIANT, **query},
        data=data,
        files={"file": (f"{name}.tar.zst", io.BytesIO(f"{name}=={version}".encode()))},
        headers=hdr,
    )
    return resp


def _catalog(client) -> dict:
    return client.get("/v1/catalog").json()


def _bundle(client, name) -> dict:
    (b,) = (b for b in _catalog(client)["bundles"] if b["name"] == name)
    return b


# ── publish -> catalog ──────────────────────────────────────────


class TestRoundTrip:
    def test_query_parameter_reaches_the_catalog(self, server):
        client, hdr = server
        resp = _publish(
            client,
            hdr,
            "libcvc-cuda",
            "3.7.13+cvc.1",
            provides=json.dumps(["libcvc"]),
            requires_capabilities=json.dumps(["cuda"]),
        )
        assert resp.status_code == 200, resp.text
        b = _bundle(client, "libcvc-cuda")
        assert b["provides"] == ["libcvc"]
        assert b["requires_capabilities"] == ["cuda"]

    def test_package_listing_serves_it_too(self, server):
        client, hdr = server
        _publish(
            client, hdr, "libcvc-cuda", "3.7.13+cvc.1", requires_capabilities=json.dumps(["cuda"])
        )
        resp = client.get(
            "/v1/packages/libcvc-cuda/latest", params={"platform": "linux", "arch": "x86_64"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requires_capabilities"] == ["cuda"]

    def test_a_publish_without_it_records_none(self, server):
        client, hdr = server
        assert _publish(client, hdr, "libcvc", "3.7.13+cvc.2").status_code == 200
        assert _bundle(client, "libcvc")["requires_capabilities"] == []

    def test_the_multipart_field_wins_over_the_query(self, server):
        client, hdr = server
        resp = _publish(
            client,
            hdr,
            "libcvc-cuda",
            "3.7.13+cvc.1",
            data={"requires_capabilities": json.dumps(["cuda"])},
            requires_capabilities=json.dumps(["from-query"]),
        )
        assert resp.status_code == 200, resp.text
        assert _bundle(client, "libcvc-cuda")["requires_capabilities"] == ["cuda"]

    def test_chunked_upload_takes_it_in_the_query_or_the_body(self, server):
        client, hdr = server
        for name, query, body in (
            ("by-query", {"requires_capabilities": json.dumps(["cuda"])}, None),
            ("by-body", {}, {"requires_capabilities": ["cuda"]}),
        ):
            content = name.encode()
            resp = client.post(
                "/v1/upload/init",
                params={
                    "name": name,
                    "version": "1.0+cvc.1",
                    **VARIANT,
                    "total_size": len(content),
                    **query,
                },
                json=body,
                headers=hdr,
            )
            assert resp.status_code == 201, resp.text
            upload_id = resp.json()["upload_id"]
            resp = client.patch(
                f"/v1/upload/{upload_id}",
                content=content,
                headers={**hdr, "Content-Type": "application/octet-stream"},
            )
            assert resp.status_code == 200, resp.text
            resp = client.post(
                f"/v1/upload/{upload_id}/complete",
                params={"expected_sha256": hashlib.sha256(content).hexdigest()},
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text
            assert _bundle(client, name)["requires_capabilities"] == ["cuda"], name

    @pytest.mark.parametrize("bad", ["cuda", '{"cuda": true}', '["cuda", ""]', "[1]"])
    def test_a_malformed_list_is_a_422(self, server, bad):
        client, hdr = server
        resp = _publish(client, hdr, "bad-caps", "1.0+cvc.1", requires_capabilities=bad)
        assert resp.status_code == 422, resp.text
        assert "requires_capabilities" in resp.json()["detail"]
        assert not [b for b in _catalog(client)["bundles"] if b["name"] == "bad-caps"]


# ── catalog -> resolver ─────────────────────────────────────────


def _resolve_libcvc_consumer(catalog: dict) -> str:
    """What `cvcpkg install-deps` picks for a package that depends on libcvc."""
    candidates: dict[str, list] = {}
    for e in catalog_entries(catalog, platform="linux", arch="x86_64"):
        candidates.setdefault(e.name, []).append(e)
    result = resolve([ComponentReq("f2dock")], candidates)
    return result.picked["libcvc"].name


class TestResolution:
    @pytest.fixture()
    def published(self, server):
        # The 2026-11-10 window: libcvc-cuda 3.7.13 is out, libcvc 3.7.13 is not.
        client, hdr = server
        for name, version, query in (
            ("libcvc", "3.7.12+cvc.2", {}),
            (
                "libcvc-cuda",
                "3.7.13+cvc.1",
                {
                    "provides": json.dumps(["libcvc"]),
                    "requires_capabilities": json.dumps(["cuda"]),
                },
            ),
            ("f2dock", "1.0.0+cvc.1", {"required_deps": json.dumps([{"name": "libcvc"}])}),
        ):
            assert _publish(client, hdr, name, version, **query).status_code == 200
        return _catalog(client)

    def test_a_host_without_cuda_gets_libcvc(self, published, monkeypatch):
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        assert _resolve_libcvc_consumer(published) == "libcvc"

    def test_a_cuda_host_still_gets_the_cuda_provider(self, published, monkeypatch):
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "cuda")
        assert _resolve_libcvc_consumer(published) == "libcvc-cuda"

    def test_without_the_field_the_newer_cuda_build_wins(self, published, monkeypatch):
        # What a catalog from a server that dropped the field resolves to:
        # the bug this file guards against.
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        stripped = {
            **published,
            "bundles": [
                {k: v for k, v in b.items() if k != "requires_capabilities"}
                for b in published["bundles"]
            ],
        }
        assert _resolve_libcvc_consumer(stripped) == "libcvc-cuda"
