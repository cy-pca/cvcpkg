# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the subprocess-shim backends: ``s3_cli`` (aws), ``rclone`` and
``rsync``.

Each shells out via ``subprocess.run`` after locating its binary with
``shutil.which``.  Every test patches ``<mod>.shutil.which`` and
``<mod>.subprocess.run`` — no real binary is ever invoked.  Three paths are
exercised per backend: success, ``CalledProcessError`` -> ``OSError``, and
missing binary -> ``FileNotFoundError``.
"""

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cvcpkg.backends import rclone, rsync, s3_cli
from cvcpkg.storage import ObjectInfo


def _cpe(cmd, stderr=b"boom"):
    """A ``CalledProcessError`` whose ``stderr`` is bytes (backends decode it)."""
    return subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr=stderr)


def _have(monkeypatch, mod, exe_path):
    monkeypatch.setattr(mod.shutil, "which", lambda name: exe_path)


def _missing(monkeypatch, mod):
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)


# ════════════════════════════════════════════════════════════════
#  aws s3 CLI  (s3_cli.py)
# ════════════════════════════════════════════════════════════════


class TestS3CliHelpers:
    def test_require_aws_found(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        assert s3_cli._require_aws() == "/usr/bin/aws"

    def test_require_aws_missing(self, monkeypatch):
        _missing(monkeypatch, s3_cli)
        with pytest.raises(FileNotFoundError, match="aws CLI not found"):
            s3_cli._require_aws()

    def test_to_s3_uri_rewrites_scheme(self):
        assert s3_cli._to_s3_uri("s3-cli://bucket/key") == "s3://bucket/key"

    def test_to_s3_uri_passthrough(self):
        assert s3_cli._to_s3_uri("s3://bucket/key") == "s3://bucket/key"


class TestS3CliBackend:
    def test_schemes_and_supports_range(self):
        assert s3_cli.S3CliBackend.schemes == ("s3-cli",)
        assert s3_cli.S3CliBackend().supports_range("s3-cli://b/k") is False

    def test_head_returns_unknown_size(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        run = MagicMock()
        monkeypatch.setattr(s3_cli.subprocess, "run", run)
        info = s3_cli.S3CliBackend().head("s3-cli://b/k")
        assert isinstance(info, ObjectInfo)
        assert info.size == -1
        run.assert_called_once()

    def test_head_swallows_subprocess_error(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        monkeypatch.setattr(
            s3_cli.subprocess, "run", MagicMock(side_effect=OSError("kaboom"))
        )
        # The broad except returns an unknown-size ObjectInfo instead of raising.
        assert s3_cli.S3CliBackend().head("s3-cli://b/k").size == -1

    def test_head_missing_binary(self, monkeypatch):
        _missing(monkeypatch, s3_cli)
        with pytest.raises(FileNotFoundError):
            s3_cli.S3CliBackend().head("s3-cli://b/k")

    def test_open_success(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        run = MagicMock(return_value=SimpleNamespace(stdout=b"payload"))
        monkeypatch.setattr(s3_cli.subprocess, "run", run)
        result = s3_cli.S3CliBackend().open("s3-cli://bucket/key")
        assert result.read() == b"payload"
        cmd = run.call_args[0][0]
        assert cmd == ["/usr/bin/aws", "s3", "cp", "s3://bucket/key", "-"]

    def test_open_called_process_error(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        monkeypatch.setattr(
            s3_cli.subprocess,
            "run",
            MagicMock(side_effect=_cpe(["aws"], stderr=b"denied")),
        )
        with pytest.raises(OSError, match="aws s3 cp failed.*denied"):
            s3_cli.S3CliBackend().open("s3-cli://b/k")

    def test_open_missing_binary(self, monkeypatch):
        _missing(monkeypatch, s3_cli)
        with pytest.raises(FileNotFoundError):
            s3_cli.S3CliBackend().open("s3-cli://b/k")

    def test_put_success_cleans_tempfile(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            # Temp file must still exist while the CLI "runs".
            assert Path(cmd[3]).is_file()
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(s3_cli.subprocess, "run", fake_run)
        s3_cli.S3CliBackend().put("s3-cli://bucket/key", io.BytesIO(b"data"))

        cmd = captured["cmd"]
        assert cmd[:3] == ["/usr/bin/aws", "s3", "cp"]
        assert cmd[4] == "s3://bucket/key"
        # Temp file is removed in the finally block.
        assert not Path(cmd[3]).exists()

    def test_put_called_process_error_cleans_tempfile(self, monkeypatch):
        _have(monkeypatch, s3_cli, "/usr/bin/aws")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["tmp"] = cmd[3]
            raise _cpe(cmd, stderr=b"nope")

        monkeypatch.setattr(s3_cli.subprocess, "run", fake_run)
        with pytest.raises(OSError, match="aws s3 cp put failed.*nope"):
            s3_cli.S3CliBackend().put("s3-cli://b/k", io.BytesIO(b"data"))
        assert not Path(seen["tmp"]).exists()

    def test_put_missing_binary(self, monkeypatch):
        _missing(monkeypatch, s3_cli)
        with pytest.raises(FileNotFoundError):
            s3_cli.S3CliBackend().put("s3-cli://b/k", io.BytesIO(b"data"))


# ════════════════════════════════════════════════════════════════
#  rclone  (rclone.py)
# ════════════════════════════════════════════════════════════════


class TestRcloneHelpers:
    def test_require_rclone_found(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        assert rclone._require_rclone() == "/usr/bin/rclone"

    def test_require_rclone_missing(self, monkeypatch):
        _missing(monkeypatch, rclone)
        with pytest.raises(FileNotFoundError, match="rclone not found"):
            rclone._require_rclone()

    def test_rclone_path_strips_scheme(self):
        assert rclone._rclone_path("rclone://remote:bucket/key") == "remote:bucket/key"

    def test_rclone_path_passthrough(self):
        assert rclone._rclone_path("remote:bucket/key") == "remote:bucket/key"


class TestRcloneBackend:
    def test_schemes_and_supports_range(self):
        assert rclone.RcloneBackend.schemes == ("rclone",)
        assert rclone.RcloneBackend().supports_range("rclone://r:p") is False

    def test_head_parses_json_size(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        run = MagicMock(return_value=SimpleNamespace(stdout='{"count":1,"bytes":9001}'))
        monkeypatch.setattr(rclone.subprocess, "run", run)
        info = rclone.RcloneBackend().head("rclone://r:bucket/key")
        assert info.size == 9001
        cmd = run.call_args[0][0]
        assert cmd == ["/usr/bin/rclone", "size", "--json", "r:bucket/key"]

    def test_head_bad_json_returns_unknown(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        monkeypatch.setattr(
            rclone.subprocess, "run", MagicMock(return_value=SimpleNamespace(stdout="not-json"))
        )
        assert rclone.RcloneBackend().head("rclone://r:p").size == -1

    def test_head_subprocess_error_returns_unknown(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        monkeypatch.setattr(
            rclone.subprocess, "run", MagicMock(side_effect=_cpe(["rclone"]))
        )
        assert rclone.RcloneBackend().head("rclone://r:p").size == -1

    def test_head_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rclone)
        with pytest.raises(FileNotFoundError):
            rclone.RcloneBackend().head("rclone://r:p")

    def test_open_success(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        run = MagicMock(return_value=SimpleNamespace(stdout=b"catbytes"))
        monkeypatch.setattr(rclone.subprocess, "run", run)
        result = rclone.RcloneBackend().open("rclone://r:bucket/key")
        assert result.read() == b"catbytes"
        assert run.call_args[0][0] == ["/usr/bin/rclone", "cat", "r:bucket/key"]

    def test_open_called_process_error(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        monkeypatch.setattr(
            rclone.subprocess, "run", MagicMock(side_effect=_cpe(["rclone"], stderr=b"gone"))
        )
        with pytest.raises(OSError, match="rclone cat failed.*gone"):
            rclone.RcloneBackend().open("rclone://r:p")

    def test_open_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rclone)
        with pytest.raises(FileNotFoundError):
            rclone.RcloneBackend().open("rclone://r:p")

    def test_put_success_cleans_tempfile(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            assert Path(cmd[2]).is_file()
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(rclone.subprocess, "run", fake_run)
        rclone.RcloneBackend().put("rclone://r:bucket/key", io.BytesIO(b"data"))
        cmd = seen["cmd"]
        assert cmd[:2] == ["/usr/bin/rclone", "copyto"]
        assert cmd[3] == "r:bucket/key"
        assert not Path(cmd[2]).exists()

    def test_put_called_process_error(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        monkeypatch.setattr(
            rclone.subprocess, "run", MagicMock(side_effect=_cpe(["rclone"], stderr=b"fail"))
        )
        with pytest.raises(OSError, match="rclone copyto failed.*fail"):
            rclone.RcloneBackend().put("rclone://r:p", io.BytesIO(b"data"))

    def test_list_success(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        run = MagicMock(return_value=SimpleNamespace(stdout="a.txt\nb.txt\n\nc.txt\n"))
        monkeypatch.setattr(rclone.subprocess, "run", run)
        result = rclone.RcloneBackend().list("rclone://r:bucket")
        assert result == ["a.txt", "b.txt", "c.txt"]  # blank line dropped
        assert run.call_args[0][0] == ["/usr/bin/rclone", "lsf", "r:bucket"]

    def test_list_called_process_error(self, monkeypatch):
        _have(monkeypatch, rclone, "/usr/bin/rclone")
        monkeypatch.setattr(
            rclone.subprocess, "run", MagicMock(side_effect=_cpe(["rclone"], stderr=b"nolist"))
        )
        with pytest.raises(OSError, match="rclone lsf failed.*nolist"):
            rclone.RcloneBackend().list("rclone://r:p")

    def test_list_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rclone)
        with pytest.raises(FileNotFoundError):
            rclone.RcloneBackend().list("rclone://r:p")


# ════════════════════════════════════════════════════════════════
#  rsync  (rsync.py)
# ════════════════════════════════════════════════════════════════


class TestRsyncHelpers:
    def test_require_rsync_found(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        assert rsync._require_rsync() == "/usr/bin/rsync"

    def test_require_rsync_missing(self, monkeypatch):
        _missing(monkeypatch, rsync)
        with pytest.raises(FileNotFoundError, match="rsync not found"):
            rsync._require_rsync()


class TestRsyncBackend:
    def test_schemes_and_supports_range(self):
        assert rsync.RsyncBackend.schemes == ("rsync",)
        assert rsync.RsyncBackend().supports_range("rsync://h/p") is False

    def test_head_is_always_unknown(self):
        # rsync has no HEAD; it never even needs the binary.
        info = rsync.RsyncBackend().head("rsync://host/path")
        assert info.size == -1

    def test_open_success_reads_downloaded_file(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")

        def fake_run(cmd, **kwargs):
            # rsync writes to the temp path (last arg); emulate that.
            Path(cmd[-1]).write_bytes(b"rsynced")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(rsync.subprocess, "run", fake_run)
        result = rsync.RsyncBackend().open("rsync://host/path")
        assert result.read() == b"rsynced"

    def test_open_called_process_error_cleans_tempfile(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["tmp"] = cmd[-1]
            raise _cpe(cmd, stderr=b"xfer-fail")

        monkeypatch.setattr(rsync.subprocess, "run", fake_run)
        with pytest.raises(OSError, match="rsync failed.*xfer-fail"):
            rsync.RsyncBackend().open("rsync://host/path")
        assert not Path(seen["tmp"]).exists()

    def test_open_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rsync)
        with pytest.raises(FileNotFoundError):
            rsync.RsyncBackend().open("rsync://host/path")

    def test_put_success_cleans_tempfile(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            assert Path(cmd[-2]).is_file()  # local temp source
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(rsync.subprocess, "run", fake_run)
        rsync.RsyncBackend().put("rsync://host/dest", io.BytesIO(b"data"))
        cmd = seen["cmd"]
        assert cmd[-1] == "rsync://host/dest"
        assert not Path(cmd[-2]).exists()

    def test_put_called_process_error(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        monkeypatch.setattr(
            rsync.subprocess, "run", MagicMock(side_effect=_cpe(["rsync"], stderr=b"up-fail"))
        )
        with pytest.raises(OSError, match="rsync put failed.*up-fail"):
            rsync.RsyncBackend().put("rsync://host/dest", io.BytesIO(b"data"))

    def test_put_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rsync)
        with pytest.raises(FileNotFoundError):
            rsync.RsyncBackend().put("rsync://host/dest", io.BytesIO(b"data"))

    def test_list_parses_long_format(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        stdout = (
            "drwxr-xr-x          4,096 2024/01/01 12:00:00 subdir\n"
            "-rw-r--r--        1,234 2024/01/02 09:30:00 file.tar.gz\n"
            "garbage-short-line\n"  # < 5 fields -> skipped
        )
        run = MagicMock(return_value=SimpleNamespace(stdout=stdout))
        monkeypatch.setattr(rsync.subprocess, "run", run)
        result = rsync.RsyncBackend().list("rsync://host/path")
        assert result == ["subdir", "file.tar.gz"]
        assert run.call_args[0][0] == ["/usr/bin/rsync", "--list-only", "rsync://host/path"]

    def test_list_called_process_error(self, monkeypatch):
        _have(monkeypatch, rsync, "/usr/bin/rsync")
        monkeypatch.setattr(
            rsync.subprocess, "run", MagicMock(side_effect=_cpe(["rsync"], stderr=b"lsfail"))
        )
        with pytest.raises(OSError, match="rsync list failed.*lsfail"):
            rsync.RsyncBackend().list("rsync://host/path")

    def test_list_missing_binary(self, monkeypatch):
        _missing(monkeypatch, rsync)
        with pytest.raises(FileNotFoundError):
            rsync.RsyncBackend().list("rsync://host/path")


# ════════════════════════════════════════════════════════════════
#  Registry dispatch
# ════════════════════════════════════════════════════════════════


class TestDispatch:
    def test_get_backend_rclone(self):
        from cvcpkg.storage import get_backend

        assert "rclone" in get_backend("rclone://r:p").schemes

    def test_get_backend_rsync(self):
        from cvcpkg.storage import get_backend

        assert "rsync" in get_backend("rsync://host/path").schemes

    def test_get_backend_s3_cli(self):
        from cvcpkg.storage import get_backend

        assert "s3-cli" in get_backend("s3-cli://bucket/key").schemes
