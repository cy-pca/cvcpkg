"""materialize_symlinks: on Windows, archive symlinks end up as copies.

An elevated Windows process extracts a tar symlink as a real link whose
forward-slash relative target does not resolve (wcwidth-src's README.rst ->
docs/intro.rst on builder #28), while an unelevated one gets tarfile's copy
fallback, and for a directory link only an empty directory.  The helper gives
both the same result: regular files and directories.  These run anywhere: on
POSIX the extraction makes real links and ``windows=True`` forces the
conversion; on Windows CI tarfile may already have copied, and the outcome is
asserted the same way.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile

import pytest

from cvcpkg._archive import materialize_symlinks, members_to_extract, safe_tar_extractall


def _tar(entries) -> io.BytesIO:
    """entries: (name, kind, payload) with kind 'dir', 'file' (bytes) or 'sym' (target)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            elif kind == "file":
                info.size = len(payload)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(payload))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tf.addfile(info)
    buf.seek(0)
    return buf


_TREE = [
    ("pkg", "dir", None),
    ("pkg/docs", "dir", None),
    ("pkg/docs/intro.rst", "file", b"intro\n"),
    ("pkg/README.rst", "sym", "docs/intro.rst"),
    ("pkg/README2.rst", "sym", "README.rst"),  # a link to a link
    ("pkg/alias", "sym", "docs"),  # a directory link
]


def _extract(buf, dest):
    with tarfile.open(fileobj=buf, mode="r") as tf:
        if sys.version_info >= (3, 12):
            tf.extractall(dest, filter="fully_trusted")
        else:  # pragma: no cover - version-dependent
            tf.extractall(dest)
        return tf, materialize_symlinks(tf, dest, windows=True)


def test_links_become_regular_files_and_dirs(tmp_path):
    _extract(_tar(_TREE), tmp_path)
    pkg = tmp_path / "pkg"
    for name in ("README.rst", "README2.rst"):
        assert not (pkg / name).is_symlink(), name
        assert (pkg / name).is_file(), name
        assert (pkg / name).read_bytes() == b"intro\n", name
    alias = pkg / "alias"
    assert not alias.is_symlink()
    assert alias.is_dir()
    assert (alias / "intro.rst").read_bytes() == b"intro\n"
    # The targets themselves are untouched.
    assert (pkg / "docs" / "intro.rst").read_bytes() == b"intro\n"


def test_noop_off_windows(tmp_path):
    buf = _tar(_TREE)
    with tarfile.open(fileobj=buf, mode="r") as tf:
        tf.extractall(tmp_path)
        assert materialize_symlinks(tf, tmp_path, windows=False) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="needs real symlinks")
def test_escaping_and_dangling_links_are_left_alone(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    dest = tmp_path / "dest"
    dest.mkdir()
    buf = _tar(
        [
            ("pkg", "dir", None),
            ("pkg/out", "sym", "../../outside.txt"),
            ("pkg/gone", "sym", "missing.txt"),
        ]
    )
    with tarfile.open(fileobj=buf, mode="r") as tf:
        tf.extractall(dest)  # unfiltered on purpose: the helper must not follow it out
        assert materialize_symlinks(tf, dest, windows=True) == 0
    assert os.path.islink(dest / "pkg" / "out")
    assert os.path.islink(dest / "pkg" / "gone")


def test_stream_mode_archive(tmp_path):
    """The builder's .tar.zst path reads a stream ("r|"), as cli/_builder.py does.

    Without members_to_extract an unelevated Windows extraction dies here:
    tarfile's link fallback seeks back for the target's data (StreamError).
    """
    raw = _tar(_TREE).getvalue()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as tf:
        tf.extractall(tmp_path, members=members_to_extract(tf, windows=True))
        materialize_symlinks(tf, tmp_path, windows=True)
    pkg = tmp_path / "pkg"
    for name in ("README.rst", "README2.rst"):
        assert (pkg / name).is_file() and not (pkg / name).is_symlink(), name
        assert (pkg / name).read_bytes() == b"intro\n", name
    assert not (pkg / "alias").is_symlink()
    assert (pkg / "alias" / "intro.rst").read_bytes() == b"intro\n"


def test_members_to_extract_skips_links_only_on_windows():
    names = lambda tf, win: [m.name for m in members_to_extract(tf, windows=win)]  # noqa: E731
    with tarfile.open(fileobj=_tar(_TREE), mode="r") as tf:
        assert "pkg/README.rst" not in names(tf, True)
        assert "pkg/docs/intro.rst" in names(tf, True)
    with tarfile.open(fileobj=_tar(_TREE), mode="r") as tf:
        assert "pkg/README.rst" in names(tf, False)


def test_safe_tar_extractall_calls_it(tmp_path, monkeypatch):
    seen = {}

    def spy(tar, dest, *, windows=None):
        seen["dest"] = dest
        return 0

    monkeypatch.setattr("cvcpkg._archive.materialize_symlinks", spy)
    with tarfile.open(
        fileobj=_tar([("pkg", "dir", None), ("pkg/f", "file", b"x")]), mode="r"
    ) as tf:
        safe_tar_extractall(tf, tmp_path)
    assert "dest" in seen
