"""Every publish records the runtime deps its bundle declares.

On 2026-10-12, 1,352 live cvcpkg.org rows (1,087 of them the latest for their
variant) recorded ``required_deps: []`` although the recipe that built that
version declares runtime deps.  pydantic-core-cp312 on macos/arm64,
macos/x86_64 and linux/arm64 is one: it installs without python312 and
typing-extensions-cp312.  All of those rows were published before #152, by the
GitHub-hosted lanes' ``pack-all --server-cache-push``, which uploaded the build
cache's raw install tree.  That tree has no manifest and the push sent no deps,
so the server defaulted the column to ``[]``.  The lanes' later
``cvcpkg publish --all`` skipped the real bundle as "already on server", and
nothing reported the gap.

Since #152, #154 and #203, every publish path goes through
``_publish_to_server``: the fleet builder, the pack-all and build-all pushes,
and ``cvcpkg publish``.  It reads the deps from the bundle's own manifest.  These
tests pin that down:

* a bundle laid out as ``cvcpkg pack`` lays it out publishes the deps its
  recipe declares for that platform.  The manifest is under
  ``share/libcvc-deps/<name>/``, never the archive root.  macOS, Windows (zip)
  and noarch are covered too;
* a raw install tree, the shape of the affected archives, is refused, not
  published with no deps;
* manifest deps are normalized to the ``{name, org?, version?}`` entries that
  clients read back with ``d["name"]``, and a nameless entry is refused;
* a publish skipped because the variant exists warns when that catalog row lacks
  deps the bundle declares.  That skip is what hid the pre-#152 rows.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import click
import pytest
import yaml

from cvcpkg.builder import pack_from_prefix, runtime_dependencies_from_raw
from cvcpkg.cli._publish import (
    _find_variant,
    _manifest_required_deps,
    _missing_deps,
    _publish_to_server,
    _variant_exists,
)

NAME = "demo-cp312"
RECIPE = {
    "schema_version": 1,
    "recipe": {"name": NAME, "upstream_version": "2.46.4", "cvc_revision": 3},
    "source": {"type": "vendored", "path": "third-party/demo"},
    "patches": [],
    "depends": {
        "build": ["rust"],
        "runtime": [
            "python312",
            {"name": "typing-extensions-cp312"},
            {"name": "libomp", "platforms": ["macos"]},
            "cvc/zlib",
        ],
    },
    "build": {
        "matrix": [
            {"platform": p, "script": "build.sh"} for p in ("linux", "macos", "windows", "any")
        ],
    },
    "package": {"files": ["lib/*"], "cmake_packages": []},
}

COMMON = [
    {"name": "python312"},
    {"name": "typing-extensions-cp312"},
    {"name": "zlib", "org": "cvc"},
]


def _publish(archives: list[Path], row: dict | None = None) -> mock.MagicMock:
    """Run _publish_to_server against a stubbed server; returns the upload mock."""
    with (
        mock.patch("cvcpkg.cli._publish._find_variant", return_value=row),
        mock.patch("cvcpkg.cli._publish._publish_simple", return_value="published") as simple,
    ):
        _publish_to_server(
            "https://pkg.example.com",
            "cvctok_test",
            archives,
            release_tag="",
            chunked_threshold=10 * 1024 * 1024,
            org="",
        )
    return simple


def _sent_deps(simple: mock.MagicMock) -> list[dict]:
    return json.loads(simple.call_args[0][2]["required_deps"])


def _archive_names(archive: Path) -> list[str]:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            return zf.namelist()
    with tarfile.open(archive) as tf:
        return tf.getnames()


def _tar(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _bundle(tmp_path: Path, manifest: dict) -> Path:
    """A minimal bundle whose own manifest is *manifest*."""
    return _tar(
        tmp_path / f"{NAME}-2.46.4+cvc.3-linux-x86_64-release-shared.tar.gz",
        [
            ("lib/libdemo.a", b"x"),
            (f"share/libcvc-deps/{NAME}/manifest.yaml", yaml.dump(manifest).encode()),
        ],
    )


def _manifest(deps: list | None = None, **top) -> dict:
    m = {
        "schema_version": 3,
        "bundle": {
            "name": NAME,
            "version": "2.46.4+cvc.3",
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
        },
        **top,
    }
    if deps is not None:
        m["dependencies"] = {"required": deps}
    return m


# ── a packed bundle publishes its recipe's runtime deps ─────────


class TestPackedBundlePublishesItsDeps:
    @pytest.fixture()
    def packed(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / NAME
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text(yaml.dump(RECIPE))
        prefix = tmp_path / "install"
        (prefix / "lib").mkdir(parents=True)
        (prefix / "lib" / "libdemo.a").write_bytes(b"fake static lib")

        def pack(platform: str) -> Path:
            archive, _, _ = pack_from_prefix(
                recipe_dir, prefix, platform=platform, output_dir=tmp_path / "dist" / platform
            )
            return archive

        return pack

    @pytest.mark.parametrize(
        "platform, expected",
        [
            ("linux", COMMON),
            ("macos", COMMON[:2] + [{"name": "libomp"}] + COMMON[2:]),
            ("windows", COMMON),
            ("any", COMMON),
        ],
    )
    def test_required_deps_follow_the_recipe(self, packed, platform, expected):
        archive = packed(platform)
        simple = _publish([archive])
        simple.assert_called_once()
        params = simple.call_args[0][2]
        assert params["platform"] == platform
        assert _sent_deps(simple) == expected
        assert _sent_deps(simple) == runtime_dependencies_from_raw(RECIPE, platform)

    @pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
    def test_the_manifest_is_the_bundles_own_not_a_root_one(self, packed, platform):
        names = [n.removeprefix("./") for n in _archive_names(packed(platform))]
        assert "manifest.yaml" not in names
        assert f"share/libcvc-deps/{NAME}/manifest.yaml" in names
        assert [n for n in names if n.endswith("manifest.yaml")] == [
            f"share/libcvc-deps/{NAME}/manifest.yaml"
        ]


# ── the affected archives: a raw install tree ───────────────────


def test_raw_install_tree_is_refused_not_published_without_deps(tmp_path):
    # What the pre-#152 server-cache push uploaded, e.g.
    # pydantic-core-cp312-2.46.4+cvc.3-macos-arm64: the build cache's tarball of
    # the install dir, each entry repeated once per directory level above it,
    # no share/libcvc-deps, no manifest anywhere.
    so = "lib/python3.12/site-packages/pydantic_core/_pydantic_core.cpython-312-darwin.so"
    raw = _tar(
        tmp_path / "pydantic-core-cp312-2.46.4+cvc.3-macos-arm64-release-shared.tar.gz",
        [(so, b"\xcf\xfa\xed\xfe")] * 5,
    )
    with (
        mock.patch("cvcpkg.cli._publish._find_variant") as find,
        mock.patch("cvcpkg.cli._publish._publish_simple") as simple,
        pytest.raises(click.ClickException, match="no manifest.yaml found"),
    ):
        _publish_to_server("https://pkg.example.com", "tok", [raw], "", 10 << 20, "")
    find.assert_not_called()
    simple.assert_not_called()


# ── manifest deps → catalog entries ─────────────────────────────


class TestManifestRequiredDeps:
    def test_dependencies_required_as_written_by_generate_manifest(self):
        deps = [{"name": "python312"}, {"name": "zlib", "org": "cvc", "version": ">=1.3"}]
        assert _manifest_required_deps(_manifest(deps), "a.tar.gz") == deps

    def test_legacy_flat_string_list_becomes_entries(self):
        m = _manifest(depends=["zlib", "cvc/foo"])
        assert _manifest_required_deps(m, "a.tar.gz") == [
            {"name": "zlib"},
            {"name": "foo", "org": "cvc"},
        ]

    def test_dependencies_required_wins_over_legacy(self):
        m = _manifest([{"name": "new"}], depends=["old"])
        assert _manifest_required_deps(m, "a.tar.gz") == [{"name": "new"}]

    def test_no_deps_at_all(self):
        assert _manifest_required_deps(_manifest(), "a.tar.gz") == []
        assert _manifest_required_deps(_manifest([]), "a.tar.gz") == []

    def test_keys_other_than_name_org_version_are_dropped(self):
        m = _manifest([{"name": "x", "version": "1", "platforms": ["linux"], "org": ""}])
        assert _manifest_required_deps(m, "a.tar.gz") == [{"name": "x", "version": "1"}]

    @pytest.mark.parametrize("bad", [{"version": "1"}, {"name": ""}, None, "", 3])
    def test_nameless_entry_is_refused(self, bad):
        with pytest.raises(click.ClickException, match=r"a\.tar\.gz: .* has no name"):
            _manifest_required_deps(_manifest([{"name": "ok"}, bad]), "a.tar.gz")

    def test_non_list_is_refused(self):
        with pytest.raises(click.ClickException, match="must be a list"):
            _manifest_required_deps(_manifest({"zlib": "1"}), "a.tar.gz")

    def test_publish_sends_normalized_legacy_deps(self, tmp_path):
        simple = _publish([_bundle(tmp_path, _manifest(depends=["zlib", "cvc/foo"]))])
        assert _sent_deps(simple) == [{"name": "zlib"}, {"name": "foo", "org": "cvc"}]

    def test_publish_refuses_a_nameless_dep_before_uploading(self, tmp_path):
        archive = _bundle(tmp_path, _manifest([{"version": "1"}]))
        with (
            mock.patch("cvcpkg.cli._publish._find_variant") as find,
            mock.patch("cvcpkg.cli._publish._publish_simple") as simple,
            pytest.raises(click.ClickException, match="has no name"),
        ):
            _publish_to_server("https://pkg.example.com", "tok", [archive], "", 10 << 20, "")
        find.assert_not_called()
        simple.assert_not_called()


# ── a skipped publish checks the row it skipped ─────────────────


class TestSkippedVariantRowCheck:
    DEPS = [{"name": "python312"}, {"name": "typing-extensions-cp312"}]

    def test_row_without_the_bundles_deps_warns(self, tmp_path, capsys):
        archive = _bundle(tmp_path, _manifest(self.DEPS))
        simple = _publish([archive], row={"version": "2.46.4+cvc.3", "required_deps": []})
        simple.assert_not_called()
        captured = capsys.readouterr()
        assert "skipping" in captured.out
        assert "lacks runtime deps this bundle declares (python312, typing-extensions-cp312)" in (
            captured.err
        )
        assert f"/v1/admin/packages/{NAME}/2.46.4+cvc.3/backfill-deps" in captured.err
        assert "1 variant(s) already on the server record fewer runtime deps" in captured.err

    def test_row_missing_only_some_deps_names_those(self, tmp_path, capsys):
        archive = _bundle(tmp_path, _manifest(self.DEPS))
        _publish([archive], row={"required_deps": [{"name": "python312"}]})
        err = capsys.readouterr().err
        assert "(typing-extensions-cp312)" in err

    @pytest.mark.parametrize(
        "recorded",
        [
            [{"name": "python312"}, {"name": "typing-extensions-cp312"}],
            # more, or with versions: not this check's business
            [
                {"name": "typing-extensions-cp312", "version": ">=4"},
                {"name": "python312"},
                {"name": "x"},
            ],
        ],
    )
    def test_row_with_the_deps_is_quiet(self, tmp_path, capsys, recorded):
        archive = _bundle(tmp_path, _manifest(self.DEPS))
        _publish([archive], row={"required_deps": recorded})
        assert "WARNING" not in capsys.readouterr().err

    def test_bundle_without_deps_is_quiet(self, tmp_path, capsys):
        _publish([_bundle(tmp_path, _manifest([]))], row={"required_deps": []})
        assert "WARNING" not in capsys.readouterr().err

    def test_org_qualified_deps_match_by_org_and_name(self):
        deps = [{"name": "zlib", "org": "cvc"}]
        assert _missing_deps({"required_deps": [{"name": "zlib", "org": "cvc"}]}, deps) == []
        assert _missing_deps({"required_deps": [{"name": "zlib"}]}, deps) == ["cvc/zlib"]
        assert _missing_deps({}, deps) == ["cvc/zlib"]


# ── _find_variant returns the row; _variant_exists stays a bool ─


def _server_with(packages: list[dict]) -> mock.MagicMock:
    resp = mock.MagicMock(status_code=200)
    resp.json.return_value = {"packages": packages}
    client = mock.MagicMock()
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=False)
    client.get.return_value = resp
    return client


VARIANT = ("2.46.4+cvc.3", "macos", "arm64", "release", "shared", "")


def test_find_variant_returns_the_live_matching_row():
    row = {
        "version": "2.46.4+cvc.3",
        "platform": "macos",
        "arch": "arm64",
        "build_type": "release",
        "link": "shared",
        "org": "",
        "required_deps": [],
    }
    yanked = {**row, "yanked": True, "required_deps": [{"name": "python312"}]}
    other_arch = {**row, "arch": "x86_64"}
    client = _server_with([yanked, other_arch, row])
    with mock.patch("httpx.Client", return_value=client):
        assert _find_variant("https://srv", {}, NAME, *VARIANT) is row
        assert _variant_exists("https://srv", {}, NAME, *VARIANT) is True
    with mock.patch("httpx.Client", return_value=_server_with([yanked, other_arch])):
        assert _find_variant("https://srv", {}, NAME, *VARIANT) is None
        assert _variant_exists("https://srv", {}, NAME, *VARIANT) is False
