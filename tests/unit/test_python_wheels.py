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
    BuildContext,
    PythonSpec,
    Recipe,
    RecipeError,
    SourceSpec,
    _build_env,
    _platform_wheel_keys,
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

    def test_any_artifact_resolves_for_concrete_platform(self):
        # A noarch recipe keys its single wheel under `any`; it must resolve on
        # every concrete host (a py3-none-any wheel is valid everywhere).
        s = SourceSpec(
            type="python_wheel",
            artifacts={"any": {"url": "https://e.invalid/pure-any.whl", "sha256": "f" * 64}},
        )
        for plat, arch in (("linux", "x86_64"), ("windows", "x86_64"), ("macos", "arm64")):
            url, sha, name = _resolve_artifact(s, plat, arch)
            assert url == "https://e.invalid/pure-any.whl"
            assert sha == "f" * 64
            assert name == "pure-any.whl"

    def test_any_artifact_resolves_for_noarch_identity(self):
        # The builder packages a noarch recipe under the synthetic any/noarch
        # identity; `_resolve_artifact(..., "any", "noarch")` must find the wheel.
        s = SourceSpec(
            type="python_wheel",
            artifacts={"any": {"url": "https://e.invalid/w.whl", "sha256": "a" * 64}},
        )
        url, _, name = _resolve_artifact(s, "any", "noarch")
        assert name == "w.whl"

    def test_concrete_key_preferred_over_any(self):
        # When both a platform-specific and an `any` entry exist, the specific
        # one wins for that platform; only unmatched platforms fall back to any.
        s = SourceSpec(
            type="python_wheel",
            artifacts={
                "linux-x86_64": {"url": "https://e.invalid/linux.whl", "sha256": "1" * 64},
                "any": {"url": "https://e.invalid/any.whl", "sha256": "2" * 64},
            },
        )
        url, _, _ = _resolve_artifact(s, "linux", "x86_64")
        assert url == "https://e.invalid/linux.whl"
        url2, _, _ = _resolve_artifact(s, "macos", "arm64")
        assert url2 == "https://e.invalid/any.whl"


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
    def test_every_matrix_recipe_is_pinned(self, abi):
        # numpy is migrating from prebuilt wheels to from-source (tarball) builds
        # one interpreter column at a time; either way the source must be PINNED.
        r = self._load(abi)
        if r.source.type == "python_wheel":
            assert r.source.artifacts, f"numpy-{abi} pins no artifacts"
            for pkey, entry in r.source.artifacts.items():
                assert len(entry["sha256"]) == 64, f"{abi}/{pkey} sha256 malformed"
                assert entry["url"].startswith("https://"), f"{abi}/{pkey} not https"
                # The wheel's own ABI tag must match the column it sits in.
                assert f"-{abi}-" in entry["url"], f"{abi}/{pkey} url is not a {abi} wheel"
        else:
            # From-source sdist: a single pinned tarball, compiled per platform via
            # the build matrix (see the from-source python-packages plan).
            assert r.source.type == "tarball", f"numpy-{abi}: unexpected source {r.source.type}"
            src = r.raw["source"]
            assert src.get("url", "").startswith("https://"), f"numpy-{abi} sdist not https"
            assert len(src.get("sha256", "")) == 64, f"numpy-{abi} sdist sha256 malformed"
            assert r.build_matrix, f"numpy-{abi} from-source has no build matrix"

    def test_free_threaded_column_exists_and_is_marked(self):
        r = self._load("cp313t")
        assert r.python.free_threaded is True
        # Sibling columns must not claim to be free-threaded.
        for abi in ("cp311", "cp312", "cp313"):
            assert self._load(abi).python.free_threaded is False

    def test_matrix_covers_same_platforms(self):
        # A prebuilt-wheel matrix with ragged platform coverage would silently
        # drop an interpreter on some builder. Compare within the wheel columns
        # only: from-source columns use a different platform vocabulary
        # (build.matrix "linux" vs artifacts "linux-x86_64") and are validated by
        # their build matrix in test_every_matrix_recipe_is_pinned above.
        wheel_keys = {
            abi: frozenset(self._load(abi).source.artifacts or ())
            for abi in self.ABIS
            if self._load(abi).source.type == "python_wheel"
        }
        if wheel_keys:
            assert len(set(wheel_keys.values())) == 1, wheel_keys


class TestPlatformWheelKeys:
    """A per-version fan-out recipe carries several wheels for one platform,
    keyed ``{plat}-{arch}`` plus ``{plat}-{arch}-cpNN`` siblings; all must be
    collected for the build, and never bleed across platforms."""

    def _src(self, *keys):
        return SourceSpec(
            type="python_wheel",
            artifacts={k: {"url": f"https://e.invalid/{k}.whl", "sha256": "a" * 64} for k in keys},
        )

    def test_single_wheel_yields_exact_key(self):
        s = self._src("linux-x86_64", "macos-arm64")
        assert _platform_wheel_keys(s, "linux", "x86_64") == ["linux-x86_64"]

    def test_sibling_keys_still_collected_for_rejection(self):
        # The retired per-version fan-out shape; the fetch layer rejects it.
        s = self._src("linux-x86_64", "linux-x86_64-cp311", "linux-x86_64-cp313", "linux-arm64")
        assert _platform_wheel_keys(s, "linux", "x86_64") == [
            "linux-x86_64",
            "linux-x86_64-cp311",
            "linux-x86_64-cp313",
        ]

    def test_no_cross_platform_bleed(self):
        # `linux-x86_64` must not swallow `linux-x86_64-...` when resolving a
        # different platform, nor match `linux-arm64` / `macos-x86_64`.
        s = self._src("linux-x86_64", "linux-x86_64-cp311", "macos-x86_64", "linux-arm64")
        assert _platform_wheel_keys(s, "macos", "x86_64") == ["macos-x86_64"]
        assert _platform_wheel_keys(s, "linux", "arm64") == ["linux-arm64"]

    def test_noarch_recipe_has_no_platform_keys(self):
        # A pure/noarch recipe keys only `any`; there is no platform wheel set,
        # so the fetch falls back to the single-wheel path.
        assert _platform_wheel_keys(self._src("any"), "linux", "x86_64") == []


class TestWheelFetch:
    """_fetch_python_wheel downloads exactly ONE wheel per platform; the
    retired per-version sibling-key shape is a hard error."""

    def _recipe(self, tmp_path, arts):
        d = {
            "schema_version": 1,
            "recipe": {"name": "asyncpg", "upstream_version": "0.31.0", "cvc_revision": 1},
            "source": {"type": "python_wheel", "artifacts": arts},
            "python": {"interpreter": "python312", "abi": "cp312"},
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/"]},
        }
        _write_recipe(tmp_path / "r", d)
        return Recipe.load(tmp_path / "r")

    def test_downloads_exactly_the_platform_wheel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_SOURCE_CACHE_DIR", "")
        names = {
            "linux-x86_64": "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl",
            # a different platform's wheel must NOT be fetched
            "macos-arm64": "asyncpg-0.31.0-cp312-cp312-macosx_11_0_arm64.whl",
        }
        bodies = {n: n.encode() for n in names.values()}
        arts = {
            key: {
                "url": f"https://e.invalid/{n}",
                "sha256": hashlib.sha256(bodies[n]).hexdigest(),
            }
            for key, n in names.items()
        }
        r = self._recipe(tmp_path, arts)
        work = tmp_path / "w"
        work.mkdir()

        import urllib.request

        def _r(url, dest):
            Path(dest).write_bytes(bodies[url.rsplit("/", 1)[-1]])

        monkeypatch.setattr(urllib.request, "urlretrieve", _r)
        src = fetch_source(r, work, platform="linux", arch="x86_64")
        got = sorted(p.name for p in src.glob("*.whl"))
        assert got == [names["linux-x86_64"]]

    def test_sibling_keys_are_rejected(self, tmp_path):
        arts = {
            "linux-x86_64": {"url": "https://e.invalid/a.whl", "sha256": "a" * 64},
            "linux-x86_64-cp311": {"url": "https://e.invalid/b.whl", "sha256": "b" * 64},
        }
        r = self._recipe(tmp_path, arts)
        work = tmp_path / "w"
        work.mkdir()
        with pytest.raises(RecipeError, match="sibling keys"):
            fetch_source(r, work, platform="linux", arch="x86_64")


class TestBuildEnvPython:
    """_build_env exports the python block verbatim — and NEVER a fan-out.

    Every python package is a per-interpreter column recipe installing only
    into its own interpreter's site-packages; the cross-interpreter copy
    fan-out (CVC_PYTHON_NOARCH_FANOUT) is retired."""

    @pytest.fixture(autouse=True)
    def _clean_python_gil(self, monkeypatch):
        # PYTHON_GIL is a real CPython env var a developer may have exported;
        # _build_env copies os.environ, so scrub it for deterministic asserts.
        monkeypatch.delenv("PYTHON_GIL", raising=False)

    def _env(self, tmp_path, *, python, matrix, artifacts):
        d = {
            "schema_version": 1,
            "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
            "source": {"type": "python_wheel", "artifacts": artifacts},
            "python": python,
            "build": {"matrix": matrix},
            "package": {"files": ["lib/"]},
        }
        rd = tmp_path / "recipes" / "tp"
        rd.mkdir(parents=True)
        (rd / "recipe.yaml").write_text(yaml.safe_dump(d))
        r = Recipe.load(rd)
        work = tmp_path / "work"
        ctx = BuildContext(
            recipe=r,
            platform=r.build_matrix[0].platform,
            config="release",
            link="shared",
            prefix=tmp_path / "prefix",
            source_dir=work / "src",
            build_dir=work / "build",
            install_dir=work / "install",
            work_dir=work,
        )
        return _build_env(ctx, r.build_matrix[0])

    def test_pure_column_recipe_exports_python_env_without_fanout(self, tmp_path):
        env = self._env(
            tmp_path,
            python={"interpreter": "python313t", "abi": "cp313t"},
            matrix=[{"platform": "any", "script": "build.sh"}],
            artifacts={"any": {"url": "https://e.invalid/a.whl", "sha256": "a" * 64}},
        )
        assert env["CVC_PYTHON_INTERPRETER"] == "python313t"
        assert env["CVC_PYTHON_ABI"] == "cp313t"
        assert env["PYTHON_GIL"] == "0"  # free-threaded column pins the GIL off
        assert "CVC_PYTHON_NOARCH_FANOUT" not in env

    def test_abi3_column_recipe_never_fans_out(self, tmp_path):
        env = self._env(
            tmp_path,
            python={"interpreter": "python312", "abi": "abi3"},
            matrix=[{"platform": "linux", "script": "build.sh"}],
            artifacts={"linux-x86_64": {"url": "https://e.invalid/a.whl", "sha256": "a" * 64}},
        )
        assert env["CVC_PYTHON_INTERPRETER"] == "python312"
        assert env["CVC_PYTHON_ABI"] == "abi3"
        assert "PYTHON_GIL" not in env
        assert "CVC_PYTHON_NOARCH_FANOUT" not in env

    def test_cext_column_recipe_never_fans_out(self, tmp_path):
        env = self._env(
            tmp_path,
            python={"interpreter": "python312", "abi": "cp312"},
            matrix=[{"platform": "linux", "script": "build.sh"}],
            artifacts={"linux-x86_64": {"url": "https://e.invalid/a.whl", "sha256": "a" * 64}},
        )
        assert env["CVC_PYTHON_ABI"] == "cp312"
        assert "CVC_PYTHON_NOARCH_FANOUT" not in env
