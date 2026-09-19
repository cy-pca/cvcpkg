# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Coverage tests for the ``cache`` CLI group (cvcpkg.cli._cache).

Every network and disk boundary is mocked: the remote ``cache list`` /
``purge`` / ``server-stats`` / ``server-gc`` commands go through
``urllib.request.urlopen`` (patched), and the local commands go through
``cvcpkg.build_cache.BuildCache`` (patched to a MagicMock).  No real server,
socket, or cache directory is ever touched.
"""

from __future__ import annotations

import urllib.error
from unittest import mock

from click.testing import CliRunner

from cvcpkg.build_cache import CacheEntryMeta
from cvcpkg.cli import _cache
from cvcpkg.cli import cli


# ── helpers ─────────────────────────────────────────────────────


class _Resp:
    """Minimal urlopen context-manager stand-in whose read() yields JSON."""

    def __init__(self, payload: object) -> None:
        import json

        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _http_error(code: int = 500, reason: str = "Server Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://srv.example/x", code, reason, {}, None)


def _entry(**over) -> CacheEntryMeta:
    base = dict(
        name="zlib",
        version="1.3.1+cvc.1",
        chain_hash="deadbeefcafef00d1234",
        platform="linux",
        arch="x86_64",
        config="release",
        link="shared",
        archive_sha256="a" * 64,
        archive_size_bytes=2048,
        stored_at="2026-09-01T12:00:00+00:00",
        last_used_at="2026-09-02T12:00:00+00:00",
        org="",
    )
    base.update(over)
    return CacheEntryMeta(**base)


def _patch_cache(**attrs):
    """Patch BuildCache to a MagicMock instance with the given attrs preset."""
    m = mock.patch("cvcpkg.build_cache.BuildCache")
    cls = m.start()
    inst = cls.return_value
    for k, v in attrs.items():
        getattr(inst, k).return_value = v
    return m, inst


# ── cache list (local) ──────────────────────────────────────────


def test_cache_list_local_empty():
    m, _inst = _patch_cache(list_entries=[])
    try:
        res = CliRunner().invoke(cli, ["cache", "list"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Build cache is empty." in res.output


def test_cache_list_local_with_entries():
    entries = [_entry(), _entry(name="hdf5", org="acme", chain_hash="0011223344556677")]
    m, inst = _patch_cache(list_entries=entries, total_size_bytes=4096)
    try:
        res = CliRunner().invoke(cli, ["cache", "list"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "2 cached builds" in res.output
    assert "zlib 1.3.1+cvc.1" in res.output
    # org-qualified name for the second entry
    assert "acme/hdf5" in res.output
    assert "linux/x86_64/release/shared" in res.output
    inst.list_entries.assert_called_once()


# ── cache list (server) ─────────────────────────────────────────


def test_cache_list_server_empty():
    with mock.patch("urllib.request.urlopen", return_value=_Resp({"packages": []})):
        res = CliRunner().invoke(cli, ["cache", "list", "--server", "https://srv.example/"])
    assert res.exit_code == 0
    assert "Server cache is empty." in res.output


def test_cache_list_server_with_packages_and_filters():
    payload = {
        "total": 2,
        "packages": [
            {
                "name": "zlib",
                "version": "1.3.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "size_bytes": 1024,
                "recipe_version": "abcdef0123456789",
            },
            {
                "org": "acme",
                "name": "hdf5",
                "version": "1.14.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "static",
                "size_bytes": 2048,
                "recipe_version": "0011223344556677",
            },
        ],
    }
    captured = {}

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _Resp(payload)

    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        res = CliRunner().invoke(
            cli,
            [
                "cache", "list", "--server", "https://srv.example",
                "--token", "tok", "--name", "zlib", "--platform-filter", "linux",
            ],
        )
    assert res.exit_code == 0
    assert "2 cached builds" in res.output
    assert "zlib 1.3.1" in res.output
    assert "acme/hdf5" in res.output
    # filters made it into the query string, token into the header
    assert "name=zlib" in captured["url"]
    assert "platform=linux" in captured["url"]
    assert captured["auth"] == "Bearer tok"


def test_cache_list_server_http_error():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(503, "Unavailable")):
        res = CliRunner().invoke(cli, ["cache", "list", "--server", "https://srv.example"])
    assert res.exit_code == 1
    assert "Server error: 503 Unavailable" in res.output


def test_cache_list_server_connection_error():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        res = CliRunner().invoke(cli, ["cache", "list", "--server", "https://srv.example"])
    assert res.exit_code == 1
    assert "Connection error:" in res.output


# ── cache info ──────────────────────────────────────────────────


def test_cache_info_direct_hit_with_org():
    entry = _entry(org="acme")
    m, inst = _patch_cache()
    inst.info.return_value = entry
    try:
        res = CliRunner().invoke(
            cli, ["cache", "info", "deadbeef", "--platform", "linux"]
        )
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Name:        zlib" in res.output
    assert "Chain hash:  deadbeefcafef00d1234" in res.output
    assert "SHA-256:" in res.output
    assert "Org:         acme" in res.output


def test_cache_info_prefix_match():
    # Direct info() misses, but a list scan finds a chain_hash with the prefix.
    m, inst = _patch_cache(info=None, list_entries=[_entry(chain_hash="deadbeefcafef00d1234")])
    try:
        res = CliRunner().invoke(
            cli, ["cache", "info", "deadbeef", "--platform", "linux"]
        )
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Name:        zlib" in res.output
    # no org line for the default entry
    assert "Org:" not in res.output


def test_cache_info_not_found():
    # A non-matching entry forces the prefix scan to iterate without a break.
    m, _inst = _patch_cache(info=None, list_entries=[_entry(chain_hash="ffffffffffff")])
    try:
        res = CliRunner().invoke(
            cli, ["cache", "info", "nope", "--platform", "linux"]
        )
    finally:
        m.stop()
    assert res.exit_code == 1
    assert "No cache entry matching 'nope'." in res.output


# ── cache remove ────────────────────────────────────────────────


def test_cache_remove_success():
    m, inst = _patch_cache(evict=True)
    try:
        res = CliRunner().invoke(
            cli, ["cache", "remove", "deadbeef", "--platform", "linux"]
        )
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Removed." in res.output
    inst.evict.assert_called_once()


def test_cache_remove_not_found():
    m, _inst = _patch_cache(evict=False)
    try:
        res = CliRunner().invoke(
            cli, ["cache", "remove", "deadbeef", "--platform", "linux"]
        )
    finally:
        m.stop()
    assert res.exit_code == 1
    assert "Entry not found." in res.output


# ── cache purge (local) ─────────────────────────────────────────


def test_cache_purge_all_local():
    m, inst = _patch_cache(purge=5)
    try:
        res = CliRunner().invoke(cli, ["cache", "purge", "--all"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Removed 5 entries." in res.output
    inst.purge.assert_called_once_with(max_size_bytes=0)


def test_cache_purge_stale_local():
    m, inst = _patch_cache(purge_stale=3)
    try:
        with mock.patch(
            "cvcpkg.cli._cache._compute_current_chain_hashes", return_value={"h1", "h2"}
        ):
            res = CliRunner().invoke(cli, ["cache", "purge", "--stale"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Removed 3 stale entries." in res.output
    inst.purge_stale.assert_called_once_with({"h1", "h2"})


def test_cache_purge_no_filter_errors():
    m, _inst = _patch_cache()
    try:
        res = CliRunner().invoke(cli, ["cache", "purge"])
    finally:
        m.stop()
    assert res.exit_code == 1
    assert "Specify --max-size, --max-age-days, --stale, or --all." in res.output


def test_cache_purge_max_size_local():
    m, inst = _patch_cache(purge=2)
    try:
        res = CliRunner().invoke(cli, ["cache", "purge", "--max-size", "10G"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Removed 2 entries." in res.output
    kwargs = inst.purge.call_args.kwargs
    assert kwargs["max_size_bytes"] == 10 * 1024**3
    assert kwargs["max_age_seconds"] is None


def test_cache_purge_max_age_local():
    m, inst = _patch_cache(purge=1)
    try:
        res = CliRunner().invoke(cli, ["cache", "purge", "--max-age-days", "7"])
    finally:
        m.stop()
    assert res.exit_code == 0
    assert "Removed 1 entries." in res.output
    kwargs = inst.purge.call_args.kwargs
    assert kwargs["max_age_seconds"] == 7 * 86400.0
    assert kwargs["max_size_bytes"] is None


# ── cache purge (server) ────────────────────────────────────────


def test_cache_purge_server_all():
    payload = {
        "deleted_count": 2,
        "deleted": [
            {"name": "zlib", "version": "1.3.1", "size_bytes": 100},
            {"name": "hdf5", "version": "1.14.0", "size_bytes": 200},
        ],
    }
    captured = {}

    def _fake(req, timeout=0):
        captured["method"] = req.get_method()
        return _Resp(payload)

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        res = CliRunner().invoke(
            cli, ["cache", "purge", "--all", "--server", "https://srv.example", "--token", "t"]
        )
    assert res.exit_code == 0
    assert "Removed 2 server cache entries." in res.output
    assert "zlib==1.3.1 (100 bytes)" in res.output
    assert captured["method"] == "DELETE"


def test_cache_purge_server_stale():
    captured = {}

    def _fake(req, timeout=0):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        return _Resp({"deleted_count": 0})

    with mock.patch("urllib.request.urlopen", side_effect=_fake), mock.patch(
        "cvcpkg.cli._cache._compute_current_chain_hashes", return_value={"h"}
    ):
        res = CliRunner().invoke(
            cli, ["cache", "purge", "--stale", "--server", "https://srv.example"]
        )
    assert res.exit_code == 0
    assert "Removed 0 server cache entries." in res.output
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/cache/gc")


def test_cache_purge_server_max_age():
    captured = {}

    def _fake(req, timeout=0):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _Resp({"deleted_count": 4})

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        res = CliRunner().invoke(
            cli,
            ["cache", "purge", "--max-age-days", "30", "--server", "https://srv.example"],
        )
    assert res.exit_code == 0
    assert "Removed 4 server cache entries." in res.output
    assert "older_than=30d" in captured["url"]
    assert captured["method"] == "DELETE"


def test_cache_purge_server_max_size():
    captured = {}

    def _fake(req, timeout=0):
        captured["url"] = req.full_url
        return _Resp({"deleted_count": 1})

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        res = CliRunner().invoke(
            cli, ["cache", "purge", "--max-size", "500M", "--server", "https://srv.example"]
        )
    assert res.exit_code == 0
    assert captured["url"].endswith("/v1/cache/gc")


def test_cache_purge_server_no_filter_errors():
    # No urlopen should be reached.
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
        res = CliRunner().invoke(cli, ["cache", "purge", "--server", "https://srv.example"])
    assert res.exit_code == 1
    assert "Specify --max-size, --max-age-days, --stale, or --all." in res.output


def test_cache_purge_server_http_error():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(500, "Boom")):
        res = CliRunner().invoke(
            cli, ["cache", "purge", "--all", "--server", "https://srv.example"]
        )
    assert res.exit_code == 1
    assert "Server error: 500 Boom" in res.output


def test_cache_purge_server_connection_error():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")):
        res = CliRunner().invoke(
            cli, ["cache", "purge", "--all", "--server", "https://srv.example"]
        )
    assert res.exit_code == 1
    assert "Connection error:" in res.output


# ── cache server-stats ──────────────────────────────────────────


def test_cache_server_stats_with_orgs():
    payload = {
        "total_packages": 3,
        "total_size_bytes": 3 * 1024,
        "orgs": {
            "acme": {"count": 2, "size_bytes": 2048},
            "": {"count": 1, "size_bytes": 1024},
        },
    }
    with mock.patch("urllib.request.urlopen", return_value=_Resp(payload)):
        res = CliRunner().invoke(
            cli, ["cache", "server-stats", "--server", "https://srv.example", "--token", "t"]
        )
    assert res.exit_code == 0
    assert "Total packages: 3" in res.output
    assert "Per-organization:" in res.output
    assert "acme: 2 packages" in res.output
    assert "(no org): 1 packages" in res.output


def test_cache_server_stats_no_orgs():
    payload = {"total_packages": 0, "total_size_bytes": 0, "orgs": {}}
    with mock.patch("urllib.request.urlopen", return_value=_Resp(payload)):
        res = CliRunner().invoke(
            cli, ["cache", "server-stats", "--server", "https://srv.example"]
        )
    assert res.exit_code == 0
    assert "Total packages: 0" in res.output
    assert "Per-organization:" not in res.output


def test_cache_server_stats_http_error():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
        res = CliRunner().invoke(
            cli, ["cache", "server-stats", "--server", "https://srv.example"]
        )
    assert res.exit_code == 1
    assert "Server error: 401 Unauthorized" in res.output


def test_cache_server_stats_connection_error():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
        res = CliRunner().invoke(
            cli, ["cache", "server-stats", "--server", "https://srv.example"]
        )
    assert res.exit_code == 1
    assert "Connection error:" in res.output


def test_cache_server_stats_requires_server():
    # --server is required=True -> click usage error (exit 2), no network.
    res = CliRunner().invoke(cli, ["cache", "server-stats"])
    assert res.exit_code == 2


# ── cache server-gc ─────────────────────────────────────────────


def test_cache_server_gc_max_age():
    payload = {
        "deleted_count": 2,
        "deleted": [{"name": "zlib", "version": "1.3.1", "size_bytes": 10}],
    }
    captured = {}

    def _fake(req, timeout=0):
        captured["body"] = req.data
        return _Resp(payload)

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        res = CliRunner().invoke(
            cli,
            [
                "cache", "server-gc", "--server", "https://srv.example",
                "--token", "t", "--max-age-days", "10",
            ],
        )
    assert res.exit_code == 0
    assert "GC removed 2 package(s)." in res.output
    assert "zlib==1.3.1 (10 bytes)" in res.output
    assert b"max_age_seconds" in captured["body"]


def test_cache_server_gc_max_size():
    captured = {}

    def _fake(req, timeout=0):
        captured["body"] = req.data
        return _Resp({"deleted_count": 0})

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        res = CliRunner().invoke(
            cli,
            ["cache", "server-gc", "--server", "https://srv.example", "--max-size", "1G"],
        )
    assert res.exit_code == 0
    assert "GC removed 0 package(s)." in res.output
    assert b"max_storage_bytes" in captured["body"]


def test_cache_server_gc_no_options_errors():
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
        res = CliRunner().invoke(
            cli, ["cache", "server-gc", "--server", "https://srv.example"]
        )
    assert res.exit_code == 1
    assert "Specify --max-age-days and/or --max-size." in res.output


def test_cache_server_gc_http_error():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(403, "Forbidden")):
        res = CliRunner().invoke(
            cli,
            ["cache", "server-gc", "--server", "https://srv.example", "--max-age-days", "1"],
        )
    assert res.exit_code == 1
    assert "Server error: 403 Forbidden" in res.output


def test_cache_server_gc_connection_error():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
        res = CliRunner().invoke(
            cli,
            ["cache", "server-gc", "--server", "https://srv.example", "--max-size", "1G"],
        )
    assert res.exit_code == 1
    assert "Connection error:" in res.output


def test_cache_server_gc_requires_server():
    res = CliRunner().invoke(cli, ["cache", "server-gc", "--max-age-days", "1"])
    assert res.exit_code == 2


# ── helpers ─────────────────────────────────────────────────────


def test_parse_size_units():
    assert _cache._parse_size("1024") == 1024
    assert _cache._parse_size("1B") == 1
    assert _cache._parse_size("2K") == 2 * 1024
    assert _cache._parse_size("2KB") == 2 * 1024
    assert _cache._parse_size("3M") == 3 * 1024**2
    assert _cache._parse_size("1.5G") == int(1.5 * 1024**3)
    assert _cache._parse_size("1T") == 1024**4
    # case/space-insensitive
    assert _cache._parse_size(" 4gb ") == 4 * 1024**3


def test_auto_arch_wasm_is_static():
    # wasm short-circuits without touching platform detection
    assert _cache._auto_arch("wasm") == "wasm32"
    assert _cache._auto_arch("wasm-mt") == "wasm32"


def test_auto_arch_delegates_to_detect():
    with mock.patch("cvcpkg.platform.detect_arch", return_value="aarch64"):
        assert _cache._auto_arch("linux") == "aarch64"


def test_compute_current_chain_hashes():
    class _R:
        def __init__(self, name, mats):
            self.name = name
            self.build_matrix = mats

    class _ME:
        def __init__(self, platform):
            self.platform = platform

    recipes = [_R("zlib", [_ME("linux"), _ME("windows")])]

    def _chain_hash(r, by_name, plat):
        return f"{r.name}-{plat}"

    with mock.patch("cvcpkg.builder.find_recipes_dir", return_value="/recipes"), mock.patch(
        "cvcpkg.builder.list_recipes", return_value=recipes
    ), mock.patch("cvcpkg.builder.chain_hash", side_effect=_chain_hash):
        hashes = _cache._compute_current_chain_hashes()
    assert hashes == {"zlib-linux", "zlib-windows"}


def test_compute_current_chain_hashes_empty_matrix_fallback():
    # A recipe with no build_matrix -> the default platform set is used.
    class _R:
        name = "zlib"
        build_matrix: list = []

    seen_platforms = set()

    def _chain_hash(r, by_name, plat):
        seen_platforms.add(plat)
        return ""  # empty hashes are dropped

    with mock.patch("cvcpkg.builder.find_recipes_dir", return_value="/recipes"), mock.patch(
        "cvcpkg.builder.list_recipes", return_value=[_R()]
    ), mock.patch("cvcpkg.builder.chain_hash", side_effect=_chain_hash):
        hashes = _cache._compute_current_chain_hashes()
    assert hashes == set()
    # fallback default platform set was exercised
    assert {"linux", "darwin", "windows", "freebsd", "wasm", "wasm-mt"} <= seen_platforms
