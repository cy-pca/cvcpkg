"""A debug install takes a dependency's release bundle when it has no debug one.

libcvc publishes debug bundles for linux and macOS, built against the release
bundles of its deps (a debug libcvc links release eigen, ftxui, joltphysics and
the rest: debug and release share one ABI there).  A `--config debug` install
still resolved every runtime dep in debug, so it failed on the nine linux deps
that publish release only.  Now a dep with no bundle in the requested config
comes from release on every platform but Windows, where the debug CRT differs.
A package asked for by name is installed in the config asked for, or not at all.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.catalog import entries_with_config_fallback
from cvcpkg.cli import main
from cvcpkg.platform import runtime_config_fallback


def _bundle(name, build_type, *, platform="linux", deps=(), provides=(), caps=(), version="1.0"):
    return {
        "name": name,
        "version": f"{version}+cvc.1",
        "upstream_version": version,
        "cvc_revision": 1,
        "platform": platform,
        "arch": "x86_64",
        "build_type": build_type,
        "link": "shared",
        "sha256": f"{name}-{build_type}-{version}",
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
    assert "1 dep(s) have no linux/x86_64/debug/shared bundle; using their release ones" in out
    assert out.rstrip().endswith("eigen") or ": eigen" in out


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
    # install-deps passes --as-deps: what it installs is a recipe's deps.
    cat = _catalog(tmp_path, _bundle("eigen", "release"), _bundle("zlib", "debug"))
    ret, got = _install(tmp_path, cat, "eigen", "zlib", "--config", "debug", "--as-deps")
    assert not ret
    assert got == {"eigen": "release", "zlib": "debug"}


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
        protected={"ari"},
    )
    assert sorted((e.name, e.build_type) for e in entries) == [
        ("eigen", "release"),
        ("libcvc", "debug"),
        ("zlib", "debug"),
    ]
    assert fell == {"eigen"}
    assert held == {"ari"}


def test_upgrade_in_a_debug_prefix_upgrades_a_release_dep(tmp_path):
    from cvcpkg.lockfile import LockEntry, Lockfile

    prefix = tmp_path / "prefix"
    lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
    Lockfile(
        platform="linux",
        arch="x86_64",
        config="debug",
        link="shared",
        bundles=[LockEntry(name="eigen", version="1.0+cvc.1")],
    ).write(lock_path)
    cat = _catalog(tmp_path, _bundle("eigen", "release", version="1.1"))
    with mock.patch("cvcpkg.installer.install_entry") as inst:
        ret = main(["upgrade", "--prefix", str(prefix), "--catalog", str(cat)])
    assert not ret
    assert [(c.args[0].name, c.args[0].version) for c in inst.call_args_list] == [
        ("eigen", "1.1+cvc.1")
    ]
