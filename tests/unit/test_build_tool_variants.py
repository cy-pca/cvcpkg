"""Build tools resolve from another config/link when theirs is not published.

A build tool (pkg-config, cmake, msys2, ...) runs during a build and is never
linked, so any variant built for the platform/arch serves.  Several are published
for only some tuples: on Windows pkg-config has only release/shared bundles, so
`install-deps --link static` stopped with "not found in the catalog for this
platform tuple: pkg-config" (windows-recipe-check pcre2 link=static, run
38957169767).  install-deps marks a recipe's build-only deps whose recipe is
build_type_independent as --build-tool; install takes the nearest published
variant of those, and refuses when that variant's closure would put two variants
of one package in the prefix.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.cli import main


def _bundle(name, link, *, build_type="release", deps=(), sha=None):
    return {
        "name": name,
        "version": "1.0+cvc.1",
        "upstream_version": "1.0",
        "cvc_revision": 1,
        "platform": "windows",
        "arch": "x86_64",
        "build_type": build_type,
        "link": link,
        "sha256": sha or f"{name}-{build_type}-{link}",
        "size_bytes": 1,
        "archive_url": "",
        "source_release": "",
        "required_deps": [{"name": d, "version": ">=1.0"} for d in deps],
    }


def _catalog(tmp_path: Path, *bundles) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": list(bundles)}))
    return p


def _install(tmp_path, cat, *args):
    """Run `cvcpkg install` against *cat*; return (exit code, {name: (build_type, link)})."""
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
                "windows",
                "--arch",
                "x86_64",
            ]
        )
    got = {c.args[0].name: (c.args[0].build_type, c.args[0].link) for c in inst.call_args_list}
    return ret, got


def test_static_install_takes_a_shared_only_build_tool(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("zlib", "static"),
        _bundle("zlib", "shared"),
        _bundle("pkg-config", "shared"),
    )
    ret, got = _install(
        tmp_path, cat, "zlib", "pkg-config", "--link", "static", "--build-tool", "pkg-config"
    )
    assert not ret
    assert got == {"zlib": ("release", "static"), "pkg-config": ("release", "shared")}


def test_without_build_tool_the_tuple_stays_strict(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("zlib", "static"), _bundle("pkg-config", "shared"))
    ret, got = _install(tmp_path, cat, "zlib", "pkg-config", "--link", "static")
    assert ret
    assert "not found in the catalog for this platform tuple: pkg-config" in capsys.readouterr().err
    assert got == {}


def test_the_exact_variant_still_wins(tmp_path):
    cat = _catalog(tmp_path, _bundle("pkg-config", "static"), _bundle("pkg-config", "shared"))
    ret, got = _install(
        tmp_path, cat, "pkg-config", "--link", "static", "--build-tool", "pkg-config"
    )
    assert not ret
    assert got == {"pkg-config": ("release", "static")}


def test_debug_install_takes_a_release_only_build_tool(tmp_path):
    # build_type_independent recipes publish Release only.
    cat = _catalog(
        tmp_path, _bundle("zlib", "shared", build_type="debug"), _bundle("pkg-config", "shared")
    )
    ret, got = _install(
        tmp_path, cat, "zlib", "pkg-config", "--config", "debug", "--build-tool", "pkg-config"
    )
    assert not ret
    assert got == {"zlib": ("debug", "shared"), "pkg-config": ("release", "shared")}


def test_a_build_tool_brings_its_own_closure(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("zlib", "static"),
        _bundle("cmake", "shared", deps=["curl"]),
        _bundle("curl", "shared"),
    )
    ret, got = _install(tmp_path, cat, "zlib", "cmake", "--link", "static", "--build-tool", "cmake")
    assert not ret
    assert got == {
        "zlib": ("release", "static"),
        "cmake": ("release", "shared"),
        "curl": ("release", "shared"),
    }


def test_two_variants_of_one_package_are_refused(tmp_path, capsys):
    # The target links curl statically; the only cmake needs the shared curl.
    cat = _catalog(
        tmp_path,
        _bundle("curl", "static"),
        _bundle("curl", "shared"),
        _bundle("cmake", "shared", deps=["curl"]),
    )
    ret, got = _install(tmp_path, cat, "curl", "cmake", "--link", "static", "--build-tool", "cmake")
    assert ret
    err = capsys.readouterr().err
    assert "cmake (release/shared)" in err and "needs curl" in err
    assert "One prefix cannot hold both variants" in err
    assert got == {}


def test_a_shared_dependency_that_is_the_same_bundle_is_not_a_clash(tmp_path):
    # A noarch-like dep published once serves both variants: same sha256.
    cat = _catalog(
        tmp_path,
        _bundle("zlib", "static"),
        _bundle("ca-bundle", "static", sha="one-bundle"),
        _bundle("ca-bundle", "shared", sha="one-bundle"),
        _bundle("cmake", "shared", deps=["ca-bundle"]),
    )
    ret, got = _install(
        tmp_path, cat, "zlib", "ca-bundle", "cmake", "--link", "static", "--build-tool", "cmake"
    )
    assert not ret
    assert got["cmake"] == ("release", "shared")
    assert got["ca-bundle"] == ("release", "static")


# ── install-deps: which deps are build tools ────────────────────────────────


def _recipes(tmp_path):
    rdir = tmp_path / "recipes"
    rdir.mkdir()

    def _recipe(name, *, tool=False, depends=None):
        d = rdir / name
        d.mkdir()
        build = {"matrix": [{"platform": "windows", "script": "build.ps1"}]}
        if tool:
            build["build_type_independent"] = True
        body = {
            "schema_version": 1,
            "recipe": {"name": name, "upstream_version": "1.0", "cvc_revision": 1},
            "source": {"type": "vendored", "path": "."},
            "build": build,
        }
        if depends is not None:
            body["depends"] = depends
        (d / "recipe.yaml").write_text(yaml.safe_dump(body))

    for tool in ("pkg-config", "cmake", "ninja", "perl"):
        _recipe(tool, tool=True)
    _recipe("zlib")
    _recipe("staticlib")
    _recipe(
        "mylib",
        depends={
            "build": [
                {"name": "pkg-config"},
                {"name": "cmake"},
                {"name": "staticlib"},  # a library linked in: never a build tool
                {"name": "perl"},  # also a runtime dep below: not build-only
            ],
            "runtime": [{"name": "zlib"}, {"name": "perl"}],
            "host_tools": [{"name": "ninja"}],
        },
    )
    return rdir


def _install_deps_calls(tmp_path, *extra):
    rdir = _recipes(tmp_path)
    with mock.patch("cvcpkg.cli._install.install") as inst:
        main(
            [
                "install-deps",
                "mylib",
                "--recipes-dir",
                str(rdir),
                "--no-default-recipes",
                "--platform",
                "windows",
                "--link",
                "static",
                *extra,
            ]
        )
    return [c.kwargs for c in inst.call_args_list]


def test_install_deps_marks_build_only_tools(tmp_path):
    (call,) = _install_deps_calls(tmp_path)
    assert set(call["components"]) == {"pkg-config", "cmake", "staticlib", "perl", "zlib"}
    assert set(call["build_tools"]) == {"pkg-config", "cmake"}


def test_install_deps_marks_included_host_tools(tmp_path):
    (call,) = _install_deps_calls(tmp_path, "--include-host-tools")
    assert "ninja" in call["components"]
    assert set(call["build_tools"]) == {"pkg-config", "cmake", "ninja"}
