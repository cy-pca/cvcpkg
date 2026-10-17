"""The builder lays out a job's dep prefix as ``cvcpkg install`` would.

The builder unpacks each dependency archive into the job prefix as it is.  A
noarch python bundle is in the Unix layout, ``lib/pythonX.Y/site-packages``,
which a python.org-layout Windows python.exe never searches.  ``cvcpkg
install`` moves it into ``Lib/site-packages``; the builder did not, so prod
job #20190 (recipes/cvcpkg on windows) could not import poetry-core:

    BackendUnavailable: Cannot import 'cvcpkg_build_backend'
"""

from __future__ import annotations

from cvcpkg.cli._builder import _settle_dep_prefix


def _unpack_noarch_and_windows_bundles(prefix):
    # A noarch bundle (poetry-core) and a Windows-built one (pyyaml).
    unix_site = prefix / "lib" / "python3.12" / "site-packages"
    (unix_site / "poetry" / "core").mkdir(parents=True)
    (unix_site / "poetry" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (unix_site / "poetry_core-2.4.1.dist-info").mkdir()
    win_site = prefix / "Lib" / "site-packages"
    (win_site / "yaml").mkdir(parents=True)
    (win_site / "yaml" / "__init__.py").write_text("", encoding="utf-8")
    return win_site


def test_windows_job_moves_noarch_packages_into_lib_site_packages(tmp_path):
    win_site = _unpack_noarch_and_windows_bundles(tmp_path)

    _settle_dep_prefix(tmp_path, "windows")

    assert (win_site / "poetry" / "core" / "__init__.py").is_file()
    assert (win_site / "poetry_core-2.4.1.dist-info").is_dir()
    assert (win_site / "yaml" / "__init__.py").is_file(), "a Windows bundle stays put"
    assert not (tmp_path / "lib" / "python3.12").exists(), "the emptied Unix site dir is pruned"


def test_other_platforms_keep_the_unix_layout(tmp_path):
    unix_site = tmp_path / "lib" / "python3.12" / "site-packages"
    (unix_site / "click").mkdir(parents=True)

    _settle_dep_prefix(tmp_path, "linux")

    assert (unix_site / "click").is_dir()


def test_still_rewrites_stale_pc_prefixes(tmp_path):
    # The rewrites the builder already ran are still part of it.
    pc = tmp_path / "lib" / "pkgconfig" / "zlib.pc"
    pc.parent.mkdir(parents=True)
    pc.write_text(
        "prefix=/tmp/cvcpkg-zlib-abc123/install\nlibdir=${prefix}/lib\n", encoding="utf-8"
    )

    _settle_dep_prefix(tmp_path, "windows")

    assert "cvcpkg-zlib-abc123" not in pc.read_text(encoding="utf-8")
