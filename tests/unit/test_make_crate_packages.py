"""tools/make_crate_packages.py: Rust crates as cvcpkg packages (docs/rust-crates.md).

Each crate a Cargo.lock pins from crates.io becomes ``crate-<name>--<version>``,
a noarch package staging one entry of a cargo directory source; a crate set
recipe gathers one consumer's crates and ships the cargo source replacement.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from cvcpkg.validation import validate_recipe_dir

try:  # pragma: no cover - version shim
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the backport (in the dev environment)
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]

LOCK = textwrap.dedent(
    """\
    version = 3

    [[package]]
    name = "app"
    version = "0.1.0"
    dependencies = ["serde_json", "zstd", "xcb-imdkit"]

    [[package]]
    name = "serde_json"
    version = "1.0.113"
    source = "registry+https://github.com/rust-lang/crates.io-index"
    checksum = "69801b70b1c3dac963ecb03a364ba0ceda9cf60c71cfe475e99864759c8b8a79"

    [[package]]
    name = "zstd"
    version = "0.11.2+zstd.1.5.2"
    source = "registry+https://github.com/rust-lang/crates.io-index"
    checksum = "20cc960326ece64f010d2d2107537f26dc589a6573a316bd5b1dba685fa5fde4"

    [[package]]
    name = "xcb-imdkit"
    version = "0.3.0"
    source = "git+https://github.com/wez/xcb-imdkit-rs.git?rev=215ce4b08ac9c4822e541efd4f4ffb1062806051#215ce4b08ac9c4822e541efd4f4ffb1062806051"
    """
)


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location(
        "make_crate_packages", ROOT / "tools" / "make_crate_packages.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass looks its module up there
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path: Path, with_git_recipe: bool = True) -> tuple[Path, Path, Path]:
    lock = tmp_path / "Cargo.lock"
    lock.write_text(LOCK)
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    if with_git_recipe:
        g = recipes / "crate-git-xcb-imdkit--215ce4b0"
        g.mkdir()
        (g / "recipe.yaml").write_text("recipe: {name: crate-git-xcb-imdkit--215ce4b0}\n")
    sources = tmp_path / "vendor"
    for d, lic in (("serde_json-1.0.113", "MIT OR Apache-2.0"), ("zstd-0.11.2+zstd.1.5.2", "MIT")):
        (sources / d).mkdir(parents=True)
        (sources / d / "Cargo.toml").write_text(
            f'[package]\nname = "x"\nversion = "1.0.0"\nlicense = "{lic}"\n'
            'description = """A “quoted”\n  description."""\n',
            encoding="utf-8",
        )
    return lock, recipes, sources


def _run(tool, lock: Path, recipes: Path, sources: Path) -> int:
    return tool.main(
        [
            "--lockfile",
            str(lock),
            "--set",
            "app-crates",
            "--set-version",
            "0.1.0",
            "--recipes-dir",
            str(recipes),
            "--crate-sources",
            str(sources),
            "--consumer",
            "app",
        ]
    )


class TestNames:
    @pytest.mark.parametrize(
        ("name", "version", "recipe"),
        [
            ("serde_json", "1.0.113", "crate-serde-json--1-0-113"),
            ("windows-sys", "0.48.0", "crate-windows-sys--0-48-0"),
            ("zstd", "0.11.2+zstd.1.5.2", "crate-zstd--0-11-2"),
            ("Inflector", "0.1.0-alpha.3", "crate-inflector--0-1-0-alpha-3"),
        ],
    )
    def test_recipe_names(self, tool, name, version, recipe):
        assert tool.crate_recipe_name(name, version) == recipe

    def test_upstream_version_drops_build_metadata(self, tool):
        assert tool.upstream_version("0.11.2+zstd.1.5.2") == "0.11.2"
        assert tool.upstream_version("1.0.0-alpha.3") == "1.0.0-alpha.3"


class TestGenerate:
    def test_writes_valid_crate_and_set_recipes(self, tool, tmp_path):
        lock, recipes, sources = _setup(tmp_path)
        assert _run(tool, lock, recipes, sources) == 0

        zstd = recipes / "crate-zstd--0-11-2"
        doc = yaml.safe_load((zstd / "recipe.yaml").read_text(encoding="utf-8"))
        assert doc["recipe"]["upstream_version"] == "0.11.2"
        assert doc["recipe"]["license"] == "MIT"
        # Escaped to ASCII: a Windows builder's cvcpkg may read the file in its
        # code page, where a curly quote fails to decode.
        assert "(A “quoted” description)" in doc["recipe"]["description"]
        assert (zstd / "recipe.yaml").read_bytes().isascii()
        assert doc["source"] == {
            "type": "tarball",
            "url": "https://static.crates.io/crates/zstd/zstd-0.11.2+zstd.1.5.2.crate",
            "sha256": "20cc960326ece64f010d2d2107537f26dc589a6573a316bd5b1dba685fa5fde4",
            "strip_components": 1,
        }
        assert doc["build"]["matrix"] == [{"platform": "any", "script": "build.sh"}]
        assert doc["package"]["files"] == ["share/cargo/crates/zstd-0.11.2+zstd.1.5.2/"]
        build = (zstd / "build.sh").read_text(encoding="utf-8")
        assert "share/cargo/crates/zstd-0.11.2+zstd.1.5.2" in build
        assert (
            '{"files":{},"package":"20cc960326ece64f010d2d2107537f26dc589a6573a316bd5b1dba685fa5fde4"}'
            in build
        )

        sset = recipes / "app-crates"
        sdoc = yaml.safe_load((sset / "recipe.yaml").read_text(encoding="utf-8"))
        assert sdoc["source"] == {"type": "none"}
        assert [d["name"] for d in sdoc["depends"]["runtime"]] == [
            "crate-git-xcb-imdkit--215ce4b0",
            "crate-serde-json--1-0-113",
            "crate-zstd--0-11-2",
        ]
        assert (sset / "Cargo.lock").read_text(encoding="utf-8") == LOCK
        assert (sset / "build.sh").is_file()

        for rd in (zstd, recipes / "crate-serde-json--1-0-113", sset):
            assert validate_recipe_dir(rd) == [], rd.name

    def test_cargo_config_replaces_crates_io_and_the_git_source(self, tool, tmp_path):
        lock, recipes, sources = _setup(tmp_path)
        _run(tool, lock, recipes, sources)
        text = (recipes / "app-crates" / "cargo-config.toml.in").read_text(encoding="utf-8")
        cfg = tomllib.loads(text.replace("@CRATES_DIR@", "/p/share/cargo/crates"))
        src = cfg["source"]
        assert src["crates-io"] == {"replace-with": "cvcpkg-crates"}
        assert src["cvcpkg-crates"] == {"directory": "/p/share/cargo/crates"}
        key = "git+https://github.com/wez/xcb-imdkit-rs.git?rev=215ce4b08ac9c4822e541efd4f4ffb1062806051"
        assert src[key] == {
            "git": "https://github.com/wez/xcb-imdkit-rs.git",
            "rev": "215ce4b08ac9c4822e541efd4f4ffb1062806051",
            "replace-with": "cvcpkg-crates",
        }

    def test_reuses_an_existing_crate_recipe(self, tool, tmp_path, capsys):
        lock, recipes, sources = _setup(tmp_path)
        _run(tool, lock, recipes, sources)
        before = (recipes / "crate-zstd--0-11-2" / "recipe.yaml").read_text(encoding="utf-8")
        assert _run(tool, lock, recipes, sources) == 0
        assert "0 new recipes, 2 reused" in capsys.readouterr().out
        assert (recipes / "crate-zstd--0-11-2" / "recipe.yaml").read_text(
            encoding="utf-8"
        ) == before

    def test_refuses_an_existing_recipe_with_another_checksum(self, tool, tmp_path):
        lock, recipes, sources = _setup(tmp_path)
        _run(tool, lock, recipes, sources)
        rf = recipes / "crate-zstd--0-11-2" / "recipe.yaml"
        rf.write_text(
            rf.read_text(encoding="utf-8").replace("20cc9603", "00000000"), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="crate-zstd--0-11-2"):
            _run(tool, lock, recipes, sources)

    def test_stops_on_a_git_dependency_without_a_recipe(self, tool, tmp_path, capsys):
        lock, recipes, sources = _setup(tmp_path, with_git_recipe=False)
        assert _run(tool, lock, recipes, sources) == 1
        assert "crate-git-xcb-imdkit--215ce4b0" in capsys.readouterr().err
        assert not (recipes / "app-crates").exists()


def test_the_shipped_wezterm_set_matches_its_crate_recipes(tool):
    """Every member of recipes/wezterm-crates exists, each crate recipe's sha256
    is the checksum its Cargo.lock records, its recipe.yaml is ASCII, and its
    build.sh is what the generator writes now."""
    sset = ROOT / "recipes" / "wezterm-crates"
    lock = tomllib.loads((sset / "Cargo.lock").read_text(encoding="utf-8"))
    checksums = {
        (p["name"], p["version"]): p["checksum"] for p in lock["package"] if "checksum" in p
    }
    doc = yaml.safe_load((sset / "recipe.yaml").read_text(encoding="utf-8"))
    members = [d["name"] for d in doc["depends"]["runtime"]]
    assert len(members) == len(checksums) + 1  # + the one git dependency
    for (name, version), checksum in checksums.items():
        rd = ROOT / "recipes" / tool.crate_recipe_name(name, version)
        assert rd.name in members, rd.name
        doc = yaml.safe_load((rd / "recipe.yaml").read_text(encoding="utf-8"))
        assert doc["source"]["sha256"] == checksum, rd.name
        assert (rd / "recipe.yaml").read_bytes().isascii(), rd.name
        build = (rd / "build.sh").read_bytes().decode("utf-8")
        assert build == tool.crate_build_sh(tool.Crate(name, version, checksum)), rd.name
