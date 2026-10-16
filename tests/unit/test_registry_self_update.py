"""Self-update of the single-file cvcpkg binary from the registry's cvcpkg package.

The binary cannot pip-install itself, so it used to ignore ``builder.update``
altogether: every single-file builder had to be replaced by hand.  It now
replaces itself with the registry's own ``cvcpkg`` package (recipes/cvcpkg) for
its platform.  What these tests pin down:

* the registry's ``+cvc.N`` is the recipe revision, not cvcpkg's: compared on
  the full spelling, ``2.5.0+cvc.1`` is "newer" than a running ``2.5.0`` and the
  builder would replace itself with the same release on every update request;
* nothing touches the running binary until the new one has been downloaded,
  matched against the registry's sha256, extracted, and has answered
  ``--version`` with the release it claims to be;
* builders sharing one binary (a fleet's workers) do not replace it twice, and
  never with an older one;
* the restart is a re-exec of the same path (POSIX) with the PyInstaller
  bootloader's variables dropped, or the supervisor's exit code (Windows).
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import zipfile

import pytest

import cvcpkg.cli._builder as builder_mod
from cvcpkg import registry_update as ru

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell-script binaries")


def _is_newer(a: str, b: str) -> bool:
    return builder_mod._is_newer_version(a, b)


def _row(version, platform="linux", arch="x86_64", **kw):
    row = {
        "name": "cvcpkg",
        "version": version,
        "platform": platform,
        "arch": arch,
        "archive_url": f"/v1/download/cvcpkg-{version}-{platform}-{arch}.tar.zst",
        "sha256": "ab" * 32,
        "yanked": False,
    }
    row.update(kw)
    return row


# -- pick_candidate ---------------------------------------------------


def test_a_rebuild_of_the_running_release_is_not_an_update():
    rows = [_row("2.5.0+cvc.1"), _row("2.5.0+cvc.2")]
    assert (
        ru.pick_candidate(
            rows, platform="linux", arch="x86_64", running="2.5.0", is_newer=_is_newer
        )
        is None
    )


def test_picks_the_newest_release_for_this_platform_and_makes_its_url_absolute():
    rows = [
        _row("2.5.1+cvc.1"),
        _row("2.6.0+cvc.1", platform="macos"),
        _row("2.6.0+cvc.1", arch="arm64"),
        _row("2.5.2+cvc.1"),
        _row("2.5.2+cvc.2"),
    ]
    cand = ru.pick_candidate(
        rows,
        platform="linux",
        arch="x86_64",
        running="2.5.0",
        is_newer=_is_newer,
        base_url="https://reg.example/",
    )
    assert cand is not None
    assert cand.version == "2.5.2+cvc.2"
    assert cand.upstream == "2.5.2"
    assert (
        cand.archive_url
        == "https://reg.example/v1/download/cvcpkg-2.5.2+cvc.2-linux-x86_64.tar.zst"
    )


def test_never_picks_a_yanked_or_unverifiable_row():
    rows = [
        _row("2.7.0+cvc.1", yanked=True),
        _row("2.6.0+cvc.1", sha256=""),
        _row("2.5.9+cvc.1", archive_url=""),
        _row("2.5.1+cvc.1"),
    ]
    cand = ru.pick_candidate(
        rows, platform="linux", arch="x86_64", running="2.5.0", is_newer=_is_newer
    )
    assert cand is not None and cand.version == "2.5.1+cvc.1"


def test_never_an_older_release():
    rows = [_row("2.4.0+cvc.1")]
    assert (
        ru.pick_candidate(
            rows, platform="linux", arch="x86_64", running="2.5.0", is_newer=_is_newer
        )
        is None
    )


# -- archive handling ---------------------------------------------------


def _script(version: str) -> bytes:
    return f"#!/bin/sh\necho 'cvcpkg, version {version}'\n".encode()


def _tar_gz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_binary_reads_only_bin_cvcpkg_from_a_tar(tmp_path):
    archive = tmp_path / "a.tar.zst"  # the suffix lies, as the registry's does
    archive.write_bytes(_tar_gz({"bin/cvcpkg": b"BINARY", "lib/other": b"x"}))
    out = tmp_path / "out"
    ru.extract_binary(archive, "bin/cvcpkg", out)
    assert out.read_bytes() == b"BINARY"
    assert os.access(out, os.X_OK) or sys.platform == "win32"
    assert not (tmp_path / "lib").exists()


def test_extract_binary_reads_a_windows_zip(tmp_path):
    archive = tmp_path / "a.tar.zst"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bin/cvcpkg.exe", b"EXE")
    archive.write_bytes(buf.getvalue())
    out = tmp_path / "out.exe"
    ru.extract_binary(archive, "bin/cvcpkg.exe", out)
    assert out.read_bytes() == b"EXE"


def test_extract_binary_rejects_an_archive_without_it(tmp_path):
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(_tar_gz({"bin/other": b"x"}))
    with pytest.raises(ru.RegistryUpdateError, match="has no bin/cvcpkg"):
        ru.extract_binary(archive, "bin/cvcpkg", tmp_path / "out")


# -- a fake registry --------------------------------------------------


class _Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def iter_bytes(self):
        for i in range(0, len(self.content), 7):
            yield self.content[i : i + 7]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Registry:
    """``/v1/packages/cvcpkg`` and one downloadable archive."""

    def __init__(self, archive: bytes, version: str, *, sha256: str | None = None):
        self.archive = archive
        self.rows = [
            _row(version, sha256=sha256 or hashlib.sha256(archive).hexdigest()),
        ]
        self.downloads = 0

    def client_cls(self):
        reg = self

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, **k):
                if url.endswith("/v1/packages/cvcpkg"):
                    return _Resp(200, {"total": len(reg.rows), "packages": reg.rows})
                return _Resp(404)

            def stream(self, method, url, **k):
                reg.downloads += 1
                if "/v1/download/" in url:
                    return _Resp(200, content=reg.archive)
                return _Resp(404)

        return _Client


@pytest.fixture
def frozen_binary(monkeypatch, tmp_path):
    """A 'single-file binary' at 2.5.0: a shell script, run as sys.executable."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "cvcpkg"
    binary.write_bytes(_script("2.5.0"))
    binary.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setattr("cvcpkg.__version__", "2.5.0")
    monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)
    execs: list[dict] = []

    def _exec(token, extra_env=None):
        execs.append({"token": token, "binary": binary.read_bytes()})

    monkeypatch.setattr(builder_mod, "_reexec_builder", _exec)
    return binary, execs


def _source():
    return builder_mod._RegistrySource(
        "https://reg.example", {"Authorization": "Bearer t"}, "linux", "x86_64"
    )


@posix_only
def test_registry_update_replaces_the_binary_and_restarts(monkeypatch, frozen_binary):
    binary, execs = frozen_binary
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.5.1")}), "2.5.1+cvc.1")
    monkeypatch.setattr("httpx.Client", reg.client_cls())

    builder_mod._registry_self_update(_source(), token="tok")

    assert b"version 2.5.1" in binary.read_bytes()
    assert os.access(binary, os.X_OK)
    assert len(execs) == 1 and execs[0]["token"] == "tok"
    # Nothing left beside it but the lock file.
    leftovers = {p.name for p in binary.parent.iterdir()} - {"cvcpkg"}
    assert leftovers <= {builder_mod._BINARY_UPDATE_LOCK_NAME}, leftovers


@posix_only
def test_a_sha256_mismatch_leaves_the_running_binary_alone(monkeypatch, frozen_binary, capsys):
    binary, execs = frozen_binary
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.5.1")}), "2.5.1+cvc.1", sha256="00" * 32)
    monkeypatch.setattr("httpx.Client", reg.client_cls())

    builder_mod._registry_self_update(_source(), token="tok")

    assert b"version 2.5.0" in binary.read_bytes()
    assert not execs
    assert "sha256 mismatch" in capsys.readouterr().err
    leftovers = {p.name for p in binary.parent.iterdir()} - {"cvcpkg"}
    assert leftovers <= {builder_mod._BINARY_UPDATE_LOCK_NAME}, leftovers


@posix_only
def test_a_binary_that_reports_another_version_is_not_installed(monkeypatch, frozen_binary, capsys):
    binary, execs = frozen_binary
    # Packaged as 2.5.1, but the binary inside says 2.4.0.
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.4.0")}), "2.5.1+cvc.1")
    monkeypatch.setattr("httpx.Client", reg.client_cls())

    builder_mod._registry_self_update(_source(), token="tok")

    assert b"version 2.5.0" in binary.read_bytes()
    assert not execs
    assert "reports version 2.4.0, not 2.5.1" in capsys.readouterr().err


@posix_only
def test_a_binary_another_builder_already_replaced_is_only_restarted(monkeypatch, frozen_binary):
    binary, execs = frozen_binary
    # A fleet's other worker updated the shared binary while this one waited.
    binary.write_bytes(_script("2.5.1"))
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.5.1")}), "2.5.1+cvc.1")
    monkeypatch.setattr("httpx.Client", reg.client_cls())

    builder_mod._registry_self_update(_source(), token="tok")

    assert reg.downloads == 0
    assert len(execs) == 1


@posix_only
def test_a_stop_before_the_swap_changes_nothing(monkeypatch, frozen_binary):
    binary, execs = frozen_binary
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.5.1")}), "2.5.1+cvc.1")
    monkeypatch.setattr("httpx.Client", reg.client_cls())

    builder_mod._registry_self_update(_source(), token="tok", stop=lambda: True)

    assert b"version 2.5.0" in binary.read_bytes()
    assert not execs


@posix_only
def test_windows_supervised_builder_exits_for_its_supervisor(monkeypatch, frozen_binary):
    binary, execs = frozen_binary
    reg = _Registry(_tar_gz({"bin/cvcpkg": _script("2.5.1")}), "2.5.1+cvc.1")
    monkeypatch.setattr("httpx.Client", reg.client_cls())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ru, "swap_in", lambda new, target: os.replace(new, target))
    monkeypatch.setenv("CVCPKG_BUILDER_SUPERVISED", "1")

    with pytest.raises(SystemExit) as exc:
        builder_mod._registry_self_update(_source(), token="tok")

    assert exc.value.code == builder_mod._SUPERVISOR_RESTART_CODE
    assert b"version 2.5.1" in binary.read_bytes()
    assert not execs


# -- restart environment / leftovers --------------------------------------


def test_the_successor_does_not_inherit_the_bootloader_state(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = ru.probe_env(
        {
            "_PYI_ARCHIVE_FILE": "/old",
            "_MEIPASS2": "/tmp/_MEIold",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "KEEP": "1",
        }
    )
    assert "_PYI_ARCHIVE_FILE" not in env and "_MEIPASS2" not in env
    assert "_PYI_PARENT_PROCESS_LEVEL" not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["CVCPKG_ENTRY"] == "client"
    assert env["KEEP"] == "1"


def test_remove_stale_binaries_keeps_a_download_in_progress(tmp_path):
    target = tmp_path / "cvcpkg.exe"
    target.write_bytes(b"exe")
    moved = tmp_path / "cvcpkg.exe.old-1234"
    moved.write_bytes(b"old")
    fresh = tmp_path / ".cvcpkg-update-abc.archive"
    fresh.write_bytes(b"partial")
    stale = tmp_path / ".cvcpkg-update-def.archive"
    stale.write_bytes(b"abandoned")
    old = fresh.stat().st_mtime - 2 * 3600
    os.utime(stale, (old, old))

    ru.remove_stale_binaries(target, now=fresh.stat().st_mtime + 1)

    assert target.exists() and fresh.exists()
    assert not moved.exists() and not stale.exists()


@posix_only
def test_swap_in_is_a_rename_over_the_target(tmp_path):
    target = tmp_path / "cvcpkg"
    target.write_bytes(b"old")
    new = tmp_path / "new"
    new.write_bytes(b"new")
    assert ru.swap_in(new, target) is None
    assert target.read_bytes() == b"new" and not new.exists()
