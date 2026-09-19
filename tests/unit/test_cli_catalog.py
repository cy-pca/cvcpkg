# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Coverage tests for the catalog / gc / clean / download CLI commands
(cvcpkg.cli._catalog).

Network and download boundaries are mocked: ``cvcpkg.catalog.fetch_catalog`` /
``generate_catalog``, ``cvcpkg.cache.gc``, ``cvcpkg.installer.download_bundle``
and ``urllib.request.urlopen`` never touch a real server.  The resolver runs
for real against a local catalog file so the download happy-path is meaningful.
"""

from __future__ import annotations

import json
from unittest import mock

import urllib.error

import yaml
from click.testing import CliRunner

from cvcpkg.cli import _catalog
from cvcpkg.cli import cli


# ── helpers ─────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _catalog_dict() -> dict:
    return {
        "schema_version": 1,
        "revision": 7,
        "bundles": [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "upstream_version": "1.3.1",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "abc",
                "size_bytes": 1000,
                "archive_url": "https://cvcpkg.org/a/zlib.tar.gz",
                "source_release": "v1.0",
            },
            {
                "name": "yaml",
                "version": "0.2.5+cvc.1",
                "upstream_version": "0.2.5",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "def",
                "size_bytes": 500,
                "archive_url": "https://cvcpkg.org/a/yaml.tar.gz",
                "source_release": "v1.0",
            },
        ],
    }


def _write_catalog(tmp_path) -> str:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.dump(_catalog_dict()))
    return str(p)


# ── catalog ─────────────────────────────────────────────────────


def test_catalog_default_guidance():
    res = CliRunner().invoke(cli, ["catalog"])
    assert res.exit_code == 0
    assert "use 'catalog --show'" in res.output


def test_catalog_refresh():
    with mock.patch("cvcpkg.catalog.fetch_catalog", return_value=_catalog_dict()) as m:
        res = CliRunner().invoke(cli, ["catalog", "--refresh"])
    assert res.exit_code == 0
    assert "catalog refreshed -- revision 7, 2 bundle(s)." in res.output
    m.assert_called_once()


def test_catalog_pin():
    with mock.patch("cvcpkg.catalog.fetch_catalog", return_value=_catalog_dict()) as m:
        res = CliRunner().invoke(cli, ["catalog", "--pin", "42"])
    assert res.exit_code == 0
    assert "pinned to catalog revision 7 (2 bundle(s))." in res.output
    # pin builds the revision URL and passes it as the first positional arg
    assert m.call_args.args and m.call_args.args[0].endswith("/42.yaml")


def test_catalog_show():
    with mock.patch("cvcpkg.catalog.fetch_catalog", return_value=_catalog_dict()):
        res = CliRunner().invoke(cli, ["catalog", "--show"])
    assert res.exit_code == 0
    assert "Catalog revision: 7" in res.output
    assert "Total bundles:    2" in res.output
    assert "Components:" in res.output
    assert "yaml, zlib" in res.output  # sorted, de-duplicated


def test_catalog_show_no_bundles_omits_components():
    empty = {"revision": 1, "bundles": []}
    with mock.patch("cvcpkg.catalog.fetch_catalog", return_value=empty):
        res = CliRunner().invoke(cli, ["catalog", "--show"])
    assert res.exit_code == 0
    assert "Total bundles:    0" in res.output
    assert "Components:" not in res.output


# ── catalog-generate ────────────────────────────────────────────


def test_catalog_generate_happy(tmp_path):
    indexes = tmp_path / "indexes"
    indexes.mkdir()
    (indexes / "linux-index.yaml").write_text("bundles: []\n")
    out = tmp_path / "out"

    fake = {"revision": 12, "bundles": [{"name": "zlib"}]}
    with mock.patch("cvcpkg.catalog.generate_catalog", return_value=fake) as m:
        res = CliRunner().invoke(
            cli,
            [
                "catalog-generate",
                "--indexes-dir",
                str(indexes),
                "--output-dir",
                str(out),
                "--release-tag",
                "v1.2.0",
            ],
        )
    assert res.exit_code == 0
    assert "catalog revision 12 generated -- 1 bundle(s)." in res.output
    assert "output written to" in res.output
    # release_tag threaded through as a keyword arg
    assert m.call_args.kwargs["release_tag"] == "v1.2.0"


def test_catalog_generate_missing_required_option(tmp_path):
    indexes = tmp_path / "indexes"
    indexes.mkdir()
    # --release-tag omitted -> click usage error (exit 2)
    res = CliRunner().invoke(
        cli,
        ["catalog-generate", "--indexes-dir", str(indexes), "--output-dir", str(tmp_path / "o")],
    )
    assert res.exit_code != 0


def test_catalog_generate_nonexistent_indexes_dir(tmp_path):
    res = CliRunner().invoke(
        cli,
        [
            "catalog-generate",
            "--indexes-dir",
            str(tmp_path / "nope"),
            "--output-dir",
            str(tmp_path / "o"),
            "--release-tag",
            "v1",
        ],
    )
    assert res.exit_code != 0


# ── gc ──────────────────────────────────────────────────────────


def test_gc_empty_cache(tmp_path):
    missing = tmp_path / "no-such-cache"
    with mock.patch("cvcpkg.cache.default_cache_dir", return_value=missing):
        res = CliRunner().invoke(cli, ["gc"])
    assert res.exit_code == 0
    assert "cache is empty." in res.output


def test_gc_prunes(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    with (
        mock.patch("cvcpkg.cache.default_cache_dir", return_value=cache_dir),
        mock.patch("cvcpkg.cache.gc", return_value=3) as m,
    ):
        res = CliRunner().invoke(cli, ["gc"])
    assert res.exit_code == 0
    assert "pruned 3 cached archive(s)." in res.output
    m.assert_called_once()


# ── clean ───────────────────────────────────────────────────────


def test_clean_nonexistent_work_dir(tmp_path):
    # click's exists=True on --work-dir would reject a missing dir, so exercise
    # the "directory does not exist" branch via the default temp dir instead.
    missing = tmp_path / "gone"
    with mock.patch("tempfile.gettempdir", return_value=str(missing)):
        res = CliRunner().invoke(cli, ["clean"])
    assert res.exit_code == 0
    assert "directory does not exist" in res.output


def test_clean_removes_old(tmp_path):
    import os
    import time

    d = tmp_path / "cvcpkg-zlib-abc"
    d.mkdir()
    (d / "f.txt").write_text("x" * 50)
    old = time.time() - 200 * 60
    os.utime(d, (old, old))
    # a non-cvcpkg dir must be ignored
    (tmp_path / "keepme").mkdir()

    res = CliRunner().invoke(cli, ["clean", "--work-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "removed" in res.output
    assert not d.exists()
    assert (tmp_path / "keepme").exists()


def test_clean_dry_run(tmp_path):
    import os
    import time

    d = tmp_path / "cvcpkg-x"
    d.mkdir()
    (d / "f").write_text("y")
    old = time.time() - 200 * 60
    os.utime(d, (old, old))
    res = CliRunner().invoke(cli, ["clean", "--work-dir", str(tmp_path), "--dry-run"])
    assert res.exit_code == 0
    assert "dry-run" in res.output
    assert d.exists()


def test_clean_recent_dir_is_skipped(tmp_path):
    """A freshly-created cvcpkg-* dir is newer than the cutoff and left alone,
    yielding the 'no stale work directories' message."""
    d = tmp_path / "cvcpkg-fresh"
    d.mkdir()
    (d / "f").write_text("y")
    res = CliRunner().invoke(cli, ["clean", "--work-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "no stale work directories" in res.output
    assert d.exists()


def test_clean_rglob_oserror_treats_size_as_zero(tmp_path):
    """If measuring a dir's size raises OSError, size is reported as 0.0 B."""
    import os
    import time

    d = tmp_path / "cvcpkg-oserr"
    d.mkdir()
    (d / "f").write_text("data")
    old = time.time() - 200 * 60
    os.utime(d, (old, old))

    from pathlib import Path

    with mock.patch.object(Path, "rglob", side_effect=OSError("denied")):
        res = CliRunner().invoke(cli, ["clean", "--work-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "removed cvcpkg-oserr" in res.output
    assert "0.0 B" in res.output
    assert not d.exists()


def test_clean_all_single_dir_message(tmp_path):
    """--all removes regardless of age; message uses singular 'directory'."""
    d = tmp_path / "cvcpkg-fresh"
    d.mkdir()
    (d / "f").write_text("z")
    res = CliRunner().invoke(cli, ["clean", "--work-dir", str(tmp_path), "--all"])
    assert res.exit_code == 0
    assert "removed 1 directory" in res.output
    assert not d.exists()


# ── download ────────────────────────────────────────────────────


def test_download_happy(tmp_path):
    cat = _write_catalog(tmp_path)
    out = tmp_path / "out"
    fake_archive = tmp_path / "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz"
    fake_archive.write_bytes(b"payload-bytes")

    with (
        mock.patch("cvcpkg.installer.download_bundle", return_value=fake_archive) as m,
        mock.patch("cvcpkg.cli._catalog._fetch_mirror_urls", return_value=[]),
    ):
        res = CliRunner().invoke(
            cli,
            [
                "download",
                "zlib",
                "--catalog",
                cat,
                "-o",
                str(out),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
            ],
        )
    assert res.exit_code == 0, res.output
    assert "downloading zlib" in res.output
    assert "downloaded 1 archive(s)" in res.output
    assert (out / fake_archive.name).exists()
    m.assert_called_once()


def test_download_org_qualified_skips_other_org(tmp_path):
    """`download cvc/libcvc` must ignore a same-named bundle from another org."""
    catalog = {
        "schema_version": 1,
        "revision": 1,
        "bundles": [
            {
                "name": "libcvc",
                "org": "cvc",
                "version": "3.2.4+cvc.5",
                "upstream_version": "3.2.4",
                "cvc_revision": 5,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "cvcabc",
                "size_bytes": 1000,
                "archive_url": "https://cvcpkg.org/a/libcvc.tar.gz",
                "source_release": "v1",
            },
            {
                "name": "libcvc",
                "org": "someone-else",
                "version": "9.9.9+cvc.1",
                "upstream_version": "9.9.9",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "otherabc",
                "size_bytes": 2000,
                "archive_url": "https://cvcpkg.org/a/other.tar.gz",
                "source_release": "v1",
            },
        ],
    }
    cat = tmp_path / "org.yaml"
    cat.write_text(yaml.dump(catalog))
    out = tmp_path / "out"
    fake = tmp_path / "libcvc.tar.gz"
    fake.write_bytes(b"cvc")

    captured = {}

    def _dl(entry, cache_dir):
        captured["version"] = entry.version
        return fake

    with (
        mock.patch("cvcpkg.installer.download_bundle", side_effect=_dl),
        mock.patch("cvcpkg.cli._catalog._fetch_mirror_urls", return_value=[]),
    ):
        res = CliRunner().invoke(
            cli,
            [
                "download",
                "cvc/libcvc",
                "--catalog",
                str(cat),
                "-o",
                str(out),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
            ],
        )
    assert res.exit_code == 0, res.output
    # The cvc org's 3.2.4 was picked, not someone-else's 9.9.9.
    assert captured["version"] == "3.2.4+cvc.5"


def test_download_no_bundles_for_platform(tmp_path):
    cat = _write_catalog(tmp_path)  # only linux bundles
    res = CliRunner().invoke(
        cli,
        [
            "download",
            "zlib",
            "--catalog",
            cat,
            "-o",
            str(tmp_path / "o"),
            "--platform",
            "windows",
            "--arch",
            "x86_64",
        ],
    )
    assert res.exit_code != 0
    assert "no bundles found in catalog" in res.output


def test_download_unsatisfiable_component(tmp_path):
    """A component with no candidate in the catalog fails the resolve step."""
    cat = _write_catalog(tmp_path)  # has zlib/yaml, not "ghost"
    res = CliRunner().invoke(
        cli,
        [
            "download",
            "ghost",
            "--catalog",
            cat,
            "-o",
            str(tmp_path / "o"),
            "--platform",
            "linux",
            "--arch",
            "x86_64",
        ],
    )
    assert res.exit_code != 0
    # entries exist for this platform, so we reach and fail at the resolver
    assert "resolving for linux/x86_64" in res.output


def test_download_resolver_empty_pick(tmp_path):
    """When the resolver returns no picks, the command errors clearly."""
    cat = _write_catalog(tmp_path)
    empty_result = mock.MagicMock()
    empty_result.picked = {}
    with mock.patch("cvcpkg.resolver.resolve", return_value=empty_result):
        res = CliRunner().invoke(
            cli,
            [
                "download",
                "zlib",
                "--catalog",
                cat,
                "-o",
                str(tmp_path / "o"),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
            ],
        )
    assert res.exit_code != 0
    assert "resolver found no matching bundles." in res.output


def test_download_catalog_fetch_failure(tmp_path):
    with mock.patch("cvcpkg.catalog.fetch_catalog", side_effect=RuntimeError("boom")):
        res = CliRunner().invoke(
            cli,
            [
                "download",
                "zlib",
                "-o",
                str(tmp_path / "o"),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
            ],
        )
    assert res.exit_code != 0
    assert "failed to fetch catalog" in res.output


def test_download_with_server_fetches_mirrors(tmp_path):
    cat = _write_catalog(tmp_path)
    out = tmp_path / "out"
    fake_archive = tmp_path / "zlib.tar.gz"
    fake_archive.write_bytes(b"data")

    with (
        mock.patch("cvcpkg.installer.download_bundle", return_value=fake_archive),
        mock.patch(
            "cvcpkg.cli._catalog._fetch_mirror_urls", return_value=["https://m.example"]
        ) as fm,
    ):
        res = CliRunner().invoke(
            cli,
            [
                "download",
                "zlib",
                "--catalog",
                cat,
                "-o",
                str(out),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://srv.example",
                "--token",
                "tok",
            ],
        )
    assert res.exit_code == 0, res.output
    fm.assert_called_once_with("https://srv.example", "tok")


# ── _fetch_mirror_urls ──────────────────────────────────────────


def test_fetch_mirror_urls_filters_unhealthy():
    payload = {
        "mirrors": [
            {"url": "https://m1", "healthy": True},
            {"url": "https://m2", "healthy": False},
            {"url": "https://m3", "healthy": True},
        ]
    }
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        urls = _catalog._fetch_mirror_urls("https://srv.example/", "tok")
    assert urls == ["https://m1", "https://m3"]


def test_fetch_mirror_urls_no_token():
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp({"mirrors": []})):
        urls = _catalog._fetch_mirror_urls("https://srv.example", None)
    assert urls == []


def test_fetch_mirror_urls_swallows_errors():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        urls = _catalog._fetch_mirror_urls("https://srv.example", "tok")
    assert urls == []
