"""Tests for the Phase 7 python wheel matrix — python_wheel/python_sdist sources.

The load-bearing guarantee here is the pin: cvcpkg fetches and sha256-verifies
every wheel itself rather than trusting a recipe's build script to do it, so
these tests lean hard on the verification and resolution paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import (
    PythonSpec,
    Recipe,
    RecipeError,
    SourceSpec,
    _resolve_artifact,
    fetch_source,
)

WHEEL_BODY = b"PK\x03\x04 not really a zip, but hashing does not care"
WHEEL_SHA = hashlib.sha256(WHEEL_BODY).hexdigest()
WHEEL_NAME = "numpy-2.4.6-cp313-cp313t-manylinux_2_28_x86_64.whl"


def _write_recipe(recipe_dir: Path, d: dict) -> None:
    recipe_dir.mkdir(parents=True, exist_ok=True)
    with open(recipe_dir / "recipe.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f)


WHEEL_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "numpy-cp313t", "upstream_version": "2.4.6", "cvc_revision": 1},
    "source": {
        "type": "python_wheel",
        "artifacts": {
            "linux-x86_64": {
                "url": f"https://example.invalid/{WHEEL_NAME}",
                "sha256": WHEEL_SHA,
            }
        },
    },
    "python": {"interpreter": "python313t", "abi": "cp313t", "manylinux_min": "manylinux_2_28"},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
    "package": {"files": ["lib/"]},
}


class TestPythonSpec:
    def test_parsed_from_recipe(self, tmp_path):
        _write_recipe(tmp_path / "r", WHEEL_RECIPE)
        r = Recipe.load(tmp_path / "r")
        assert r.python is not None
        assert r.python.interpreter == "python313t"
        assert r.python.abi == "cp313t"
        assert r.python.manylinux_min == "manylinux_2_28"

    def test_absent_python_block_is_none(self, tmp_path):
        d = {k: v for k, v in WHEEL_RECIPE.items() if k != "python"}
        d["source"] = {"type": "vendored", "path": "x"}
        _write_recipe(tmp_path / "r", d)
        r = Recipe.load(tmp_path / "r")
        assert r.python is None

    @pytest.mark.parametrize(
        "abi,free",
        [("cp311", False), ("cp312", False), ("cp313", False), ("cp313t", True)],
    )
    def test_free_threaded_detection(self, abi, free):
        assert PythonSpec(abi=abi).free_threaded is free

    @pytest.mark.parametrize(
        "abi,ver", [("cp311", "3.11"), ("cp312", "3.12"), ("cp313", "3.13"), ("cp313t", "3.13")]
    )
    def test_version_tag(self, abi, ver):
        assert PythonSpec(abi=abi).version_tag == ver

    def test_abi3_is_stable_abi(self):
        # cryptography and friends ship cp311-abi3 wheels: one artifact serves
        # every interpreter from the floor upwards, collapsing the matrix.
        s = PythonSpec(interpreter="python311", abi="abi3")
        assert s.stable_abi is True
        assert s.version_tag == ""  # pins no single version by construction

    def test_abi3_is_never_free_threaded(self):
        # The 3.13 free-threaded build does not implement the stable ABI, so
        # abi3 must never be mistaken for cp313t coverage.
        assert PythonSpec(abi="abi3").free_threaded is False

    @pytest.mark.parametrize("abi", ["cp311", "cp312", "cp313", "cp313t"])
    def test_versioned_abis_are_not_stable_abi(self, abi):
        assert PythonSpec(abi=abi).stable_abi is False

    def test_build_isolation_defaults_off(self):
        # The hermeticity payoff: sdists link the prefix's cvcpkg C libs, so
        # isolation must not silently pull pip's own copies.
        assert PythonSpec(abi="cp313").build_isolation is False


class TestResolveArtifact:
    def test_picks_platform_arch_entry(self):
        s = SourceSpec(
            type="python_wheel",
            artifacts={
                "linux-x86_64": {"url": "https://e.invalid/a.whl", "sha256": "a" * 64},
                "windows-x86_64": {"url": "https://e.invalid/b.whl", "sha256": "b" * 64},
            },
        )
        url, sha, name = _resolve_artifact(s, "windows", "x86_64")
        assert url == "https://e.invalid/b.whl"
        assert sha == "b" * 64
        assert name == "b.whl"

    def test_missing_platform_lists_available(self):
        s = SourceSpec(type="python_wheel", artifacts={"linux-x86_64": {"url": "u", "sha256": "s"}})
        with pytest.raises(RecipeError, match="no artifact for macos-arm64.*linux-x86_64"):
            _resolve_artifact(s, "macos", "arm64")

    def test_bare_filename_joins_base_url(self):
        s = SourceSpec(
            type="prebuilt",
            base_url="https://e.invalid/dl/",
            sha256="c" * 64,
            artifacts={"linux-x86_64": "tool-linux"},
        )
        url, sha, name = _resolve_artifact(s, "linux", "x86_64")
        assert url == "https://e.invalid/dl/tool-linux"
        assert name == "tool-linux"

    def test_file_plus_base_url(self):
        s = SourceSpec(
            type="python_wheel",
            base_url="https://e.invalid/dl",
            artifacts={"linux-x86_64": {"file": "x.whl", "sha256": "d" * 64}},
        )
        url, _, name = _resolve_artifact(s, "linux", "x86_64")
        assert url == "https://e.invalid/dl/x.whl"
        assert name == "x.whl"

    def test_file_without_base_url_raises(self):
        s = SourceSpec(type="python_wheel", artifacts={"linux-x86_64": {"file": "x.whl"}})
        with pytest.raises(RecipeError, match="no base_url"):
            _resolve_artifact(s, "linux", "x86_64")

    def test_no_artifacts_falls_back_to_url(self):
        # The `platform: any` case — a pure-Python wheel is valid everywhere.
        s = SourceSpec(type="python_wheel", url="https://e.invalid/pure-any.whl", sha256="e" * 64)
        url, sha, name = _resolve_artifact(s, "linux", "x86_64")
        assert name == "pure-any.whl"
        assert sha == "e" * 64

    def test_no_artifacts_no_url_raises(self):
        with pytest.raises(RecipeError, match="no artifacts map"):
            _resolve_artifact(SourceSpec(type="python_wheel"), "linux", "x86_64")


class TestFetchPythonWheel:
    def _fake_urlretrieve(self, body=WHEEL_BODY):
        def _r(url, dest):  # noqa: ARG001
            Path(dest).write_bytes(body)

        return _r

    def test_downloads_and_keeps_wheel_filename(self, tmp_path, monkeypatch):
        # pip reads the compatibility tags out of the filename, so the
        # upstream name must survive the fetch.
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", "")
        _write_recipe(tmp_path / "r", WHEEL_RECIPE)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlretrieve", self._fake_urlretrieve())
        src = fetch_source(r, work, platform="linux", arch="x86_64")

        assert (src / WHEEL_NAME).is_file()
        assert (src / WHEEL_NAME).read_bytes() == WHEEL_BODY

    def test_sha256_mismatch_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", "")
        _write_recipe(tmp_path / "r", WHEEL_RECIPE)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlretrieve", self._fake_urlretrieve(b"tampered"))
        with pytest.raises(RecipeError, match="SHA-256 mismatch"):
            fetch_source(r, work, platform="linux", arch="x86_64")

    def test_unpinned_wheel_refused(self, tmp_path, monkeypatch):
        # Phase 7 requires every wheel to be pinned; a missing hash is an
        # error rather than a warning, unlike the older prebuilt recipes.
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", "")
        d = {
            **WHEEL_RECIPE,
            "source": {
                "type": "python_wheel",
                "artifacts": {"linux-x86_64": {"url": f"https://e.invalid/{WHEEL_NAME}"}},
            },
        }
        _write_recipe(tmp_path / "r", d)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()
        with pytest.raises(RecipeError, match="no sha256"):
            fetch_source(r, work, platform="linux", arch="x86_64")

    def test_cache_hit_skips_download(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / f"{WHEEL_SHA}.whl").write_bytes(WHEEL_BODY)
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", str(cache))

        _write_recipe(tmp_path / "r", WHEEL_RECIPE)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()

        import urllib.request

        def _boom(url, dest):  # noqa: ARG001
            raise AssertionError("must not download on a cache hit")

        monkeypatch.setattr(urllib.request, "urlretrieve", _boom)
        src = fetch_source(r, work, platform="linux", arch="x86_64")
        assert (src / WHEEL_NAME).read_bytes() == WHEEL_BODY

    def test_wrong_platform_reports_recipe_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", "")
        _write_recipe(tmp_path / "r", WHEEL_RECIPE)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()
        with pytest.raises(RecipeError, match="no artifact for freebsd-x86_64"):
            fetch_source(r, work, platform="freebsd", arch="x86_64")


class TestPythonSdist:
    def test_sdist_requires_sha256(self, tmp_path):
        d = {
            **WHEEL_RECIPE,
            "source": {
                "type": "python_sdist",
                "artifacts": {"linux-x86_64": {"url": "https://e.invalid/x.tar.gz"}},
            },
        }
        _write_recipe(tmp_path / "r", d)
        r = Recipe.load(tmp_path / "r")
        work = tmp_path / "w"
        work.mkdir()
        with pytest.raises(RecipeError, match="requires a sha256"):
            fetch_source(r, work, platform="linux", arch="x86_64")


class TestWheelMatrixRecipes:
    """The shipped numpy matrix — one recipe per interpreter cvcpkg ships."""

    ABIS = ["cp311", "cp312", "cp313", "cp313t"]
    REPO = Path(__file__).resolve().parents[2]

    def _load(self, abi):
        return Recipe.load(self.REPO / "recipes" / f"numpy-{abi}")

    @pytest.mark.parametrize("abi", ABIS)
    def test_recipe_targets_its_interpreter(self, abi):
        r = self._load(abi)
        assert r.python is not None
        assert r.python.abi == abi
        assert r.python.interpreter == f"python{abi[2:]}"
        # The interpreter must be a real recipe, or the depends graph dangles.
        assert (self.REPO / "recipes" / r.python.interpreter / "recipe.yaml").is_file()

    @pytest.mark.parametrize("abi", ABIS)
    def test_depends_on_its_interpreter(self, abi):
        r = self._load(abi)
        interp = f"python{abi[2:]}"
        for kind in ("build", "runtime"):
            names = [d["name"] for d in r.raw["depends"][kind]]
            assert interp in names, f"{abi} {kind} deps missing {interp}"

    @pytest.mark.parametrize("abi", ABIS)
    def test_every_wheel_is_pinned(self, abi):
        r = self._load(abi)
        assert r.source.type == "python_wheel"
        assert r.source.artifacts, f"numpy-{abi} pins no artifacts"
        for pkey, entry in r.source.artifacts.items():
            assert len(entry["sha256"]) == 64, f"{abi}/{pkey} sha256 malformed"
            assert entry["url"].startswith("https://"), f"{abi}/{pkey} not https"
            # The wheel's own ABI tag must match the column it sits in.
            assert f"-{abi}-" in entry["url"], f"{abi}/{pkey} url is not a {abi} wheel"

    def test_free_threaded_column_exists_and_is_marked(self):
        r = self._load("cp313t")
        assert r.python.free_threaded is True
        # Sibling columns must not claim to be free-threaded.
        for abi in ("cp311", "cp312", "cp313"):
            assert self._load(abi).python.free_threaded is False

    def test_matrix_covers_same_platforms(self):
        # A matrix with ragged platform coverage would silently drop an
        # interpreter on some builder.
        keys = {abi: set(self._load(abi).source.artifacts) for abi in self.ABIS}
        assert len({frozenset(v) for v in keys.values()}) == 1, keys
