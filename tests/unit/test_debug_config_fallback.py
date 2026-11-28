"""A debug install takes each dependency in the config its dependents link.

libcvc publishes debug bundles for linux and macOS. Its publish lanes build
them against the release bundles of its public deps: debug and release share
one ABI there, eigen, ftxui and others publish release only, and several debug
deps rename their libraries (libassimpd.so.6, libprotobufd.so.28.3.0). A
`--config debug` install resolved every runtime dep in debug. So it backtracked
to libcvc 3.4.0, or it would have installed debug assimp, which a debug libcvc
cannot load. Now, everywhere but Windows (whose debug CRT differs), a
dependency in the org of a package asked for stays debug, or comes from release
where it has no debug bundle. A dependency from another org comes from release.
A package asked for by name installs in the config asked for, or not at all.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.catalog import entries_with_config_fallback
from cvcpkg.cli import main
from cvcpkg.platform import runtime_config_fallback


def _bundle(
    name, build_type, *, platform="linux", deps=(), provides=(), caps=(), version="1.0", org=""
):
    return {
        "name": name,
        "version": f"{version}+cvc.1",
        "upstream_version": version,
        "cvc_revision": 1,
        "platform": platform,
        "arch": "x86_64",
        "build_type": build_type,
        "link": "shared",
        "sha256": f"{org}{name}-{build_type}-{version}",
        "org": org,
        "size_bytes": 1,
        "archive_url": "",
        "source_release": "",
        "required_deps": [{"name": d, "version": ">=1.0"} for d in deps],
        "provides": list(provides),
        "requires_capabilities": list(caps),
    }


def _catalog(tmp_path: Path, *bundles) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": list(bundles)}))
    return p


def _install(tmp_path, cat, *args, platform="linux"):
    """Run `cvcpkg install` against *cat*; return (exit code, {name: build_type})."""
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
                platform,
                "--arch",
                "x86_64",
            ]
        )
    got = {c.args[0].name: c.args[0].build_type for c in inst.call_args_list}
    return ret, got


def test_only_debug_falls_back_and_never_on_windows():
    assert runtime_config_fallback("linux", "debug") == "release"
    assert runtime_config_fallback("macos", "debug") == "release"
    assert runtime_config_fallback("freebsd", "debug") == "release"
    assert runtime_config_fallback("windows", "debug") is None
    assert runtime_config_fallback("linux", "release") is None


def test_a_debug_install_takes_a_release_only_dep(tmp_path, capsys):
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["eigen", "zlib"]),
        _bundle("libcvc", "release", deps=["eigen", "zlib"]),
        _bundle("eigen", "release"),
        _bundle("zlib", "debug"),
        _bundle("zlib", "release"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug")
    assert not ret
    # A dep published in debug stays debug; only eigen falls back.
    assert got == {"libcvc": "debug", "eigen": "release", "zlib": "debug"}
    out = capsys.readouterr().out
    assert "1 dep(s) from release, as debug bundles link them" in out
    assert "no linux/x86_64/debug/shared bundle" in out
    assert "on linux): eigen" in out


def test_the_fallback_follows_the_release_bundles_own_deps(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["ftxui"]),
        _bundle("ftxui", "release", deps=["zlib"]),
        _bundle("zlib", "debug"),
        _bundle("zlib", "release"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug")
    assert not ret
    assert got == {"libcvc": "debug", "ftxui": "release", "zlib": "debug"}


def test_windows_debug_stays_strict(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", platform="windows", deps=["eigen"]),
        _bundle("eigen", "release", platform="windows"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug", platform="windows")
    assert ret
    assert got == {}


def test_a_release_install_never_takes_debug(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "release", deps=["eigen"]),
        _bundle("eigen", "debug"),
    )
    ret, got = _install(tmp_path, cat, "libcvc")
    assert ret
    assert got == {}


def test_a_package_asked_for_by_name_does_not_fall_back(tmp_path, capsys):
    cat = _catalog(tmp_path, _bundle("ari", "release"), _bundle("zlib", "debug"))
    ret, got = _install(tmp_path, cat, "ari", "zlib", "--config", "debug")
    assert ret
    assert got == {}
    err = capsys.readouterr().err
    assert "not found in the catalog for this platform tuple: ari" in err
    assert "ari only publish(es) release bundles for linux/x86_64" in err


def test_install_deps_names_are_deps(tmp_path):
    # install-deps passes --as-deps: what it installs is a recipe's deps, and a
    # build links what it installs, so every org stays debug where published.
    cat = _catalog(
        tmp_path,
        _bundle("eigen", "release"),
        _bundle("zlib", "debug"),
        _bundle("zlib", "release"),
        _bundle("libcvc", "debug", org="cvc"),
        _bundle("libcvc", "release", org="cvc"),
    )
    ret, got = _install(tmp_path, cat, "eigen", "zlib", "libcvc", "--config", "debug", "--as-deps")
    assert not ret
    assert got == {"eigen": "release", "zlib": "debug", "libcvc": "debug"}


def test_other_orgs_deps_come_from_release(tmp_path, capsys):
    # The cvc org's debug libcvc links release public deps; their debug builds
    # rename libraries (libassimpd.so.6), so debug assimp would not load.
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["assimp", "eigen"], org="cvc"),
        _bundle("libcvc", "release", deps=["assimp", "eigen"], org="cvc"),
        _bundle("assimp", "debug", deps=["zlib"]),
        _bundle("assimp", "release", deps=["zlib"]),
        _bundle("zlib", "debug"),
        _bundle("zlib", "release"),
        _bundle("eigen", "release"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug")
    assert not ret
    assert got == {
        "libcvc": "debug",
        "assimp": "release",
        "zlib": "release",
        "eigen": "release",
    }
    out = capsys.readouterr().out
    assert "3 dep(s) from release, as debug bundles link them" in out


def test_the_asked_for_orgs_packages_stay_debug(tmp_path):
    # ari's debug lane links the debug libcvc it builds, and release public deps.
    cat = _catalog(
        tmp_path,
        _bundle("ari", "debug", deps=["libcvc"], org="cvc"),
        _bundle("libcvc", "debug", deps=["zlib"], org="cvc"),
        _bundle("libcvc", "release", deps=["zlib"], org="cvc"),
        _bundle("zlib", "debug"),
        _bundle("zlib", "release"),
    )
    ret, got = _install(tmp_path, cat, "ari", "--config", "debug")
    assert not ret
    assert got == {"ari": "debug", "libcvc": "debug", "zlib": "release"}


def test_a_public_debug_chain_stays_debug(tmp_path):
    # The fleet builds a public debug bundle against debug public deps
    # (grpc debug links libprotobufd), so those stay debug.
    cat = _catalog(
        tmp_path,
        _bundle("grpc", "debug", deps=["protobuf"]),
        _bundle("protobuf", "debug"),
        _bundle("protobuf", "release"),
    )
    ret, got = _install(tmp_path, cat, "grpc", "--config", "debug")
    assert not ret
    assert got == {"grpc": "debug", "protobuf": "debug"}


def test_another_orgs_dep_with_debug_only_keeps_it(tmp_path):
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["odd"], org="cvc"),
        _bundle("odd", "debug"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug")
    assert not ret
    assert got == {"libcvc": "debug", "odd": "debug"}


def test_a_newer_release_does_not_replace_an_older_debug(tmp_path):
    # Per name, a package with any debug bundle resolves among its debug ones.
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["zlib"]),
        _bundle("zlib", "debug", version="1.0"),
        _bundle("zlib", "release", version="1.1"),
    )
    ret, got = _install(tmp_path, cat, "libcvc", "--config", "debug")
    assert not ret
    assert got == {"libcvc": "debug", "zlib": "debug"}


def test_a_release_provider_never_outranks_a_debug_one(tmp_path):
    # libcvc-cuda (release only) provides libcvc and ranks first on a cuda host;
    # it must not displace the debug libcvc a debug install of ari depends on.
    cat = _catalog(
        tmp_path,
        _bundle("ari", "debug", deps=["libcvc"]),
        _bundle("libcvc", "debug"),
        _bundle("libcvc-cuda", "release", provides=["libcvc"], caps=["cuda"]),
    )
    with mock.patch("cvcpkg.platform.host_capabilities", return_value={"cuda"}):
        ret, got = _install(tmp_path, cat, "ari", "--config", "debug")
    assert not ret
    assert got == {"ari": "debug", "libcvc": "debug"}


def test_entries_with_config_fallback_reports_what_fell_back():
    cat = {
        "bundles": [
            _bundle("libcvc", "debug"),
            _bundle("eigen", "release"),
            _bundle("ari", "release"),
            _bundle("zlib", "debug"),
            _bundle("zlib", "release"),
        ]
    }
    entries, fell, held = entries_with_config_fallback(
        cat,
        platform="linux",
        arch="x86_64",
        build_type="debug",
        link="shared",
        asked={"ari", "libcvc"},
    )
    assert sorted((e.name, e.build_type) for e in entries) == [
        ("eigen", "release"),
        ("libcvc", "debug"),
        ("zlib", "debug"),
    ]
    assert fell == {"eigen"}
    assert held == {"ari"}


def test_upgrade_keeps_each_bundle_in_its_own_config(tmp_path):
    # A debug prefix holds release assimp (debug libcvc links it): a newer debug
    # assimp must not replace it, a newer release one does.
    from cvcpkg.lockfile import LockEntry, Lockfile

    prefix = tmp_path / "prefix"
    lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
    Lockfile(
        platform="linux",
        arch="x86_64",
        config="debug",
        link="shared",
        bundles=[LockEntry(name="assimp", version="1.0+cvc.1", sha256="assimp-release-1.0")],
    ).write(lock_path)
    cat = _catalog(
        tmp_path,
        _bundle("assimp", "release", version="1.0"),
        _bundle("assimp", "release", version="1.1"),
        _bundle("assimp", "debug", version="1.2"),
    )
    with mock.patch("cvcpkg.installer.install_entry") as inst:
        ret = main(["upgrade", "--prefix", str(prefix), "--catalog", str(cat)])
    assert not ret
    assert [
        (c.args[0].name, c.args[0].build_type, c.args[0].version) for c in inst.call_args_list
    ] == [("assimp", "release", "1.1+cvc.1")]


def test_a_neutral_bundle_of_the_same_name_decides_nothing(tmp_path):
    # The noarch Python protobuf (none/none, 7.x) shares its name with the C++
    # protobuf (28.x). It must not stop a debug libcvc's release C++ protobuf.
    neutral = _bundle("protobuf", "none", platform="any", version="7.35")
    neutral.update(arch="noarch", link="none")
    cat = _catalog(
        tmp_path,
        _bundle("libcvc", "debug", deps=["protobuf"], org="cvc"),
        neutral,
        _bundle("protobuf", "debug", version="28.3"),
        _bundle("protobuf", "release", version="28.3"),
    )
    with mock.patch("cvcpkg.installer.install_entry") as inst:
        ret = main(
            [
                "install",
                "libcvc",
                "--catalog",
                str(cat),
                "--prefix",
                str(tmp_path / "prefix"),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--config",
                "debug",
            ]
        )
    assert not ret
    got = {c.args[0].name: (c.args[0].build_type, c.args[0].version) for c in inst.call_args_list}
    assert got == {"libcvc": ("debug", "1.0+cvc.1"), "protobuf": ("release", "28.3+cvc.1")}
