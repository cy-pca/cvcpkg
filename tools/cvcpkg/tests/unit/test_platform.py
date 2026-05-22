"""Tests for cvcpkg.platform — auto-detection of platform, arch, libc."""

from __future__ import annotations

import pytest

from cvcpkg.platform import (
    default_tuple,
    detect_arch,
    detect_libc,
    detect_platform,
    detect_pointer_size,
)


class TestDetectPlatform:
    def test_linux(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        assert detect_platform() == "linux"

    def test_linux2(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux2")
        assert detect_platform() == "linux"

    def test_darwin(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        assert detect_platform() == "macos"

    def test_win32(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        assert detect_platform() == "windows"

    def test_cygwin(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "cygwin")
        assert detect_platform() == "windows"

    def test_unsupported(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "freebsd14")
        with pytest.raises(RuntimeError, match="unsupported platform"):
            detect_platform()


class TestDetectArch:
    def test_x86_64(self, monkeypatch):
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        assert detect_arch() == "x86_64"

    def test_amd64(self, monkeypatch):
        monkeypatch.setattr("platform.machine", lambda: "AMD64")
        assert detect_arch() == "x86_64"

    def test_arm64(self, monkeypatch):
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        assert detect_arch() == "arm64"

    def test_aarch64(self, monkeypatch):
        monkeypatch.setattr("platform.machine", lambda: "aarch64")
        assert detect_arch() == "arm64"

    def test_unsupported(self, monkeypatch):
        monkeypatch.setattr("platform.machine", lambda: "mips64")
        with pytest.raises(RuntimeError, match="unsupported architecture"):
            detect_arch()


class TestDetectPointerSize:
    def test_returns_int(self):
        result = detect_pointer_size()
        assert result in (32, 64)


class TestDetectLibc:
    def test_returns_string(self):
        # On Linux this should return glibc-X.Y or musl
        result = detect_libc()
        assert isinstance(result, str)
        assert len(result) > 0


class TestDefaultTuple:
    def test_returns_dict(self):
        t = default_tuple()
        assert "platform" in t
        assert "arch" in t
        assert t["platform"] in ("linux", "macos", "windows")
        assert t["arch"] in ("x86_64", "arm64")
