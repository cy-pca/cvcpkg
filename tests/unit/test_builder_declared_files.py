"""Tests for the package.files declaration check (cvcpkg pack --strict-globs).

``package.files`` does not select what gets archived — :func:`stage_bundle`
copies the whole install tree — so it is a *declaration* of what the bundle
ships. Nothing verified it, and libcvc shipped twice with a recipe that
declared ``lib/libxmlrpc*`` and exported ``cvc::xmlrpc`` while the build had
``CVC_USING_XMLRPC`` off. The bundle carried ``include/xmlrpc/`` headers for a
library it did not contain, and packing said nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cvcpkg.builder import BuildError, check_declared_files, enforce_declared_files


def _tree(root: Path, files: tuple[str, ...] = (), dirs: tuple[str, ...] = ()) -> Path:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    for rel in dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def test_catches_the_xmlrpc_regression(tmp_path: Path) -> None:
    """The real bug: recipe declares xmlrpc, the build did not produce it."""
    prefix = _tree(tmp_path, ("lib/libcvc.so", "include/xmlrpc/y.h"))
    unmatched = check_declared_files(
        prefix,
        ["lib/libcvc*", "lib/cvc*", "lib/libxmlrpc*", "lib/xmlrpc*", "include/xmlrpc/"],
        "linux",
    )
    # Both spellings of the archive are missing; the headers really did ship,
    # which is exactly what made the bundle look plausible.
    assert sorted(unmatched) == ["lib/libxmlrpc*", "lib/xmlrpc*"]


def test_silent_when_the_build_is_correct(tmp_path: Path) -> None:
    prefix = _tree(tmp_path, ("lib/libcvc.so", "lib/libxmlrpc.a", "include/xmlrpc/y.h"))
    assert (
        check_declared_files(
            prefix,
            ["lib/libcvc*", "lib/cvc*", "lib/libxmlrpc*", "lib/xmlrpc*", "include/xmlrpc/"],
            "linux",
        )
        == []
    )


def test_unix_msvc_spelling_pair_is_one_requirement(tmp_path: Path) -> None:
    """``add_library(foo STATIC)`` is libfoo.a on Unix but foo.lib on MSVC.

    44 recipes declare both spellings, so exactly one matches on any given
    platform. Judging them separately would fail every correct build.
    """
    win = _tree(tmp_path / "win", ("lib/cvc.lib", "lib/xmlrpc.lib"))
    assert (
        check_declared_files(
            win, ["lib/libcvc*", "lib/cvc*", "lib/libxmlrpc*", "lib/xmlrpc*"], "windows"
        )
        == []
    )
    nix = _tree(tmp_path / "nix", ("lib/libLerc.so",))
    assert check_declared_files(nix, ["lib/libLerc*", "lib/Lerc*"], "linux") == []


@pytest.mark.parametrize(
    ("entry", "platform"),
    [
        ("bin/avcodec-*.dll", "linux"),
        ("lib/cairo*.lib", "macos"),
        ("bin/tool.exe", "linux"),
        ("lib/libfoo.dylib", "linux"),
    ],
)
def test_platform_foreign_entries_are_exempt(tmp_path: Path, entry: str, platform: str) -> None:
    """84 declared entries name a platform-locked extension in a matrix that
    also builds other platforms; those must not fire off-platform."""
    prefix = _tree(tmp_path, ("lib/libavcodec.so",))
    assert check_declared_files(prefix, ["lib/libavcodec*", entry], platform) == []


def test_directory_entries_are_globbed_not_resolved_literally(tmp_path: Path) -> None:
    """Directory declarations carry wildcards too ("include/ImageMagick*/")."""
    prefix = _tree(tmp_path, ("include/ImageMagick-7/magick.h",))
    assert check_declared_files(prefix, ["include/ImageMagick*/"], "linux") == []


def test_declared_directory_that_is_empty_ships_nothing(tmp_path: Path) -> None:
    prefix = _tree(tmp_path, dirs=("share/thing",))
    assert check_declared_files(prefix, ["share/thing/"], "linux") == ["share/thing/"]


def test_genuinely_missing_artifact_is_reported(tmp_path: Path) -> None:
    prefix = _tree(tmp_path, ("include/foo.h",))
    assert check_declared_files(prefix, ["lib/libfoo*", "include/foo.h"], "linux") == [
        "lib/libfoo*"
    ]


def test_enforce_raises_in_strict_mode(tmp_path: Path) -> None:
    prefix = _tree(tmp_path, ("include/foo.h",))
    with pytest.raises(BuildError) as excinfo:
        enforce_declared_files(prefix, ["lib/libfoo*"], "linux", "foo", strict=True)
    assert "lib/libfoo*" in str(excinfo.value)


def test_enforce_only_warns_when_not_strict(tmp_path: Path, capsys) -> None:
    prefix = _tree(tmp_path, ("include/foo.h",))
    enforce_declared_files(prefix, ["lib/libfoo*"], "linux", "foo", strict=False)
    assert "lib/libfoo*" in capsys.readouterr().out


def test_env_var_downgrades_strict_to_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The escape hatch: unblock a fleet without a code change or recipe edit."""
    monkeypatch.setenv("CVCPKG_STRICT_GLOBS", "0")
    prefix = _tree(tmp_path, ("include/foo.h",))
    enforce_declared_files(prefix, ["lib/libfoo*"], "linux", "foo", strict=True)
    assert "lib/libfoo*" in capsys.readouterr().out


def test_enforce_is_silent_on_a_clean_tree(tmp_path: Path, capsys) -> None:
    prefix = _tree(tmp_path, ("lib/libomp.so", "include/omp.h", "include/ompt.h"))
    enforce_declared_files(
        prefix, ["lib/libomp.*", "include/omp*.h"], "linux", "openmp", strict=True
    )
    assert capsys.readouterr().out == ""
