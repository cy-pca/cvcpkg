"""`cvcpkg install --no-deps` installs the named bundles without their runtime deps.

install resolved every bundle's required_deps closure, with no way to opt out.
libcvc-examples-web 3.7.8+cvc.1 lists its 23 build deps as required, two of them
data bundles with no wasm32 match, so `cvcpkg install libcvc-examples-web
--platform wasm-mt` failed outright although the gallery needs none of them, and
no bundle could be installed alone to try it.  --no-deps (the switch `cvcpkg
build` already has) installs just the named prebuilt bundles and lists the deps
it leaves out.  The picks must still accept one another's versions, and it
builds nothing from source: a source build needs its deps.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.cli import main
from cvcpkg.manifest import CatalogEntry, ComponentReq, Dependency
from cvcpkg.resolver import resolve


def _bundle(name, *, version="1.0+cvc.1", platform="wasm-mt", arch="wasm32", deps=()):
    return {
        "name": name,
        "version": version,
        "upstream_version": version.split("+")[0],
        "cvc_revision": 1,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "static",
        "sha256": f"{name}-{version}-{platform}",
        "size_bytes": 1,
        "archive_url": "",
        "source_release": "",
        "required_deps": [
            {"name": d[0], "version": d[1]} if isinstance(d, tuple) else {"name": d} for d in deps
        ],
    }


def _catalog(tmp_path: Path, *bundles) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": list(bundles)}))
    return p


def _install(tmp_path, cat, *args):
    """Run `cvcpkg install` for wasm-mt against *cat*; return (exit code, installed names)."""
    with mock.patch("cvcpkg.installer.install_entry") as inst:
        ret = main(
            [
                "install",
                *args,
                "--catalog",
                str(cat),
                "--prefix",
                str(tmp_path / "prefix"),
                "--platform",
                "wasm-mt",
                "--arch",
                "wasm32",
            ]
        )
    return ret, sorted(c.args[0].name for c in inst.call_args_list)


def _gallery_catalog(tmp_path):
    # The motivating shape: the page's deps include a data bundle published for
    # x86_64 only, which no wasm32 request matches.
    return _catalog(
        tmp_path,
        _bundle("gallery", deps=["vtk", "scene"]),
        _bundle("vtk"),
        _bundle("scene", platform="any", arch="x86_64"),
    )


def test_default_install_still_resolves_the_closure(tmp_path):
    cat = _catalog(tmp_path, _bundle("gallery", deps=["vtk"]), _bundle("vtk"))
    ret, got = _install(tmp_path, cat, "gallery")
    assert not ret
    assert got == ["gallery", "vtk"]


def test_no_deps_installs_only_the_named_bundle(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("gallery", deps=["vtk"]), _bundle("vtk"))
    ret, got = _install(tmp_path, cat, "gallery", "--no-deps")
    assert not ret
    assert got == ["gallery"]
    assert "--no-deps: not installing gallery's runtime deps: vtk" in capsys.readouterr().out


def test_an_unresolvable_dep_fails_the_default_install(tmp_path, capsys):
    ret, got = _install(tmp_path, _gallery_catalog(tmp_path), "gallery")
    assert ret
    assert "no candidate for 'scene'" in capsys.readouterr().err
    assert got == []


def test_no_deps_installs_past_an_unresolvable_dep(tmp_path, capsys):
    ret, got = _install(tmp_path, _gallery_catalog(tmp_path), "gallery", "--no-deps")
    assert not ret
    assert got == ["gallery"]
    assert "not installing gallery's runtime deps: scene, vtk" in capsys.readouterr().out


def test_a_requested_dep_is_installed_and_not_listed(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("gallery", deps=["vtk", "boost"]), _bundle("vtk"))
    ret, got = _install(tmp_path, cat, "gallery", "vtk", "--no-deps")
    assert not ret
    assert got == ["gallery", "vtk"]
    assert "not installing gallery's runtime deps: boost\n" in capsys.readouterr().out


def test_picks_must_still_accept_each_others_versions(tmp_path, capsys):
    cat = _catalog(
        tmp_path,
        _bundle("gallery", deps=[("vtk", ">=9.0")]),
        _bundle("vtk", version="8.2+cvc.1"),
    )
    ret, got = _install(tmp_path, cat, "gallery", "vtk", "--no-deps")
    assert ret
    assert "gallery==1.0+cvc.1 needs vtk >=9.0" in capsys.readouterr().err
    assert got == []


def test_no_deps_applies_to_a_requirements_file(tmp_path):
    cat = _catalog(tmp_path, _bundle("gallery", deps=["vtk"]), _bundle("vtk"))
    reqs = tmp_path / "cvc-requirements.yaml"
    reqs.write_text(
        yaml.safe_dump(
            {
                "platform": "wasm-mt",
                "arch": "wasm32",
                "config": "release",
                "link": "static",
                "components": [{"name": "gallery"}],
            }
        )
    )
    ret, got = _install(tmp_path, cat, "--from", str(reqs), "--no-deps")
    assert not ret
    assert got == ["gallery"]


def test_an_unknown_name_says_no_deps_is_prebuilt_only(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("vtk"))
    ret, got = _install(tmp_path, cat, "vtk", "gallery", "--no-deps")
    assert ret
    err = capsys.readouterr().err
    assert "not found in the catalog for this platform tuple: gallery" in err
    assert "--no-deps installs prebuilt bundles only" in err
    assert "--fallback-to-source" not in err
    assert got == []


def test_no_deps_refuses_to_build_from_source(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("vtk"))
    for flag in ("--fallback-to-source", "--local"):
        ret, got = _install(tmp_path, cat, "vtk", "--no-deps", flag)
        assert ret == 2, flag
        assert "--no-deps installs prebuilt bundles only" in capsys.readouterr().err
        assert got == []


def _entry(name, deps=()):
    return CatalogEntry(
        name=name,
        version="1.0+cvc.1",
        upstream_version="1.0",
        cvc_revision=1,
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256=name,
        size_bytes=1,
        archive_url="",
        source_release="",
        required_deps=[Dependency(name=d) for d in deps],
    )


def test_resolver_follow_deps_false_picks_the_requested_names_alone():
    candidates = {
        "a": [_entry("a", deps=["b"])],
        "b": [_entry("b", deps=["c"])],
        "c": [_entry("c")],
    }
    reqs = [ComponentReq(name="a")]
    assert sorted(resolve(reqs, candidates, capabilities=set()).picked) == ["a", "b", "c"]
    picked = resolve(reqs, candidates, capabilities=set(), follow_deps=False).picked
    assert sorted(picked) == ["a"]
