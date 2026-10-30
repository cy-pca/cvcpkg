"""A noarch bundle serves every config and link mode, not just every platform.

A noarch recipe (platform: any -- fonts, data sets, pure Python, JavaScript)
compiles nothing and publishes one any/noarch/release/shared bundle.  The
catalog filter matched it to any platform and arch, but still compared config
and link exactly, so a static request -- and wasm, wasm-mt, wasi and cosmo are
always static -- or a debug one found nothing: `cvcpkg install xterm-js
--platform wasm` and `--platform linux --link static` failed with "no bundles
found in catalog for this platform tuple", and no recipe for those targets could
list a noarch dep (libcvc's arigl-web, for xterm-js).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.cli import main


def _bundle(name, platform, arch, *, build_type="release", link="shared", version="1.0+cvc.1"):
    return {
        "name": name,
        "version": version,
        "upstream_version": version.split("+")[0],
        "cvc_revision": 1,
        "platform": platform,
        "arch": arch,
        "build_type": build_type,
        "link": link,
        "sha256": f"{name}-{platform}-{build_type}-{link}-{version}",
        "size_bytes": 1,
        "archive_url": "",
        "source_release": "",
        "required_deps": [],
    }


def _noarch(name, **kw):
    return _bundle(name, "any", "noarch", **kw)


def _catalog(tmp_path: Path, *bundles) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": list(bundles)}))
    return p


def _install(tmp_path, cat, *args):
    """Run `cvcpkg install` against *cat*: (exit code, {name: (platform, build_type, link)})."""
    with mock.patch("cvcpkg.installer.install_entry") as inst:
        ret = main(["install", *args, "--catalog", str(cat), "--prefix", str(tmp_path / "prefix")])
    got = {
        c.args[0].name: (c.args[0].platform, c.args[0].build_type, c.args[0].link)
        for c in inst.call_args_list
    }
    return ret, got


def test_wasm_install_takes_a_noarch_bundle(tmp_path):
    # wasm is static-only, so install coerces the request to static.
    cat = _catalog(tmp_path, _noarch("xterm-js"), _bundle("zlib", "wasm", "wasm32", link="static"))
    ret, got = _install(tmp_path, cat, "xterm-js", "zlib", "--platform", "wasm", "--arch", "wasm32")
    assert not ret
    assert got == {
        "xterm-js": ("any", "release", "shared"),
        "zlib": ("wasm", "release", "static"),
    }


def test_static_install_takes_a_noarch_bundle(tmp_path):
    cat = _catalog(tmp_path, _noarch("carlito-fonts"))
    ret, got = _install(
        tmp_path,
        cat,
        "carlito-fonts",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--link",
        "static",
    )
    assert not ret
    assert got == {"carlito-fonts": ("any", "release", "shared")}


def test_debug_install_takes_a_noarch_bundle(tmp_path):
    # A noarch recipe is build_type_independent: it publishes release only.
    cat = _catalog(tmp_path, _noarch("carlito-fonts"))
    ret, got = _install(
        tmp_path,
        cat,
        "carlito-fonts",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--config",
        "debug",
    )
    assert not ret
    assert got == {"carlito-fonts": ("any", "release", "shared")}


def test_a_concrete_bundle_still_needs_its_exact_link(tmp_path, capsys):
    # Only noarch bundles are link-agnostic: a compiled one stays strict.
    cat = _catalog(tmp_path, _bundle("zlib", "linux", "x86_64", link="shared"))
    ret, got = _install(
        tmp_path, cat, "zlib", "--platform", "linux", "--arch", "x86_64", "--link", "static"
    )
    assert ret
    assert got == {}


def test_a_concrete_bundle_still_beats_noarch_at_the_same_version(tmp_path):
    # A recipe with both an `any` and a platform entry (the pure-Python columns' windows entry)
    # ships them because the noarch one cannot serve that platform: it must stay preferred.
    cat = _catalog(
        tmp_path,
        _noarch("idna"),
        _bundle("idna", "windows", "x86_64", link="static"),
    )
    ret, got = _install(
        tmp_path, cat, "idna", "--platform", "windows", "--arch", "x86_64", "--link", "static"
    )
    assert not ret
    assert got == {"idna": ("windows", "release", "static")}


def test_the_exact_noarch_variant_wins_when_there_are_several(tmp_path):
    cat = _catalog(tmp_path, _noarch("xterm-js"), _noarch("xterm-js", link="static"))
    ret, got = _install(tmp_path, cat, "xterm-js", "--platform", "wasm", "--arch", "wasm32")
    assert not ret
    assert got == {"xterm-js": ("any", "release", "static")}
    ret, got = _install(tmp_path, cat, "xterm-js", "--platform", "linux", "--arch", "x86_64")
    assert not ret
    assert got == {"xterm-js": ("any", "release", "shared")}
