"""Tests for cvcpkg.platform — auto-detection of platform, arch, libc."""

from __future__ import annotations

import pytest

from cvcpkg.platform import (
    arch_matches,
    default_tuple,
    detect_arch,
    detect_libc,
    detect_platform,
    detect_pointer_size,
    noarch_build_target,
    platform_matches,
)


class TestNoarchMatching:
    """platform=any / arch=noarch bundles must resolve on every host."""

    def test_platform_any_matches_every_host(self):
        for host in ("linux", "macos", "windows", "freebsd"):
            assert platform_matches("any", host) is True

    def test_concrete_platform_matches_only_itself(self):
        assert platform_matches("linux", "linux") is True
        assert platform_matches("linux", "macos") is False

    def test_arch_noarch_matches_every_arch(self):
        for host in ("x86_64", "arm64", "wasm32"):
            assert arch_matches("noarch", host) is True

    def test_concrete_arch_matches_only_itself(self):
        assert arch_matches("x86_64", "x86_64") is True
        assert arch_matches("x86_64", "arm64") is False

    def test_empty_request_matches_anything(self):
        assert platform_matches("linux", "") is True
        assert arch_matches("x86_64", "") is True

    def test_noarch_build_target_default_and_override(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_NOARCH_BUILD_PLATFORM", raising=False)
        monkeypatch.delenv("CVCPKG_NOARCH_BUILD_ARCH", raising=False)
        assert noarch_build_target() == ("linux", "x86_64")
        monkeypatch.setenv("CVCPKG_NOARCH_BUILD_PLATFORM", "macos")
        monkeypatch.setenv("CVCPKG_NOARCH_BUILD_ARCH", "arm64")
        assert noarch_build_target() == ("macos", "arm64")


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

    def test_freebsd(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "freebsd14")
        assert detect_platform() == "freebsd"

    def test_freebsd13(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "freebsd13")
        assert detect_platform() == "freebsd"

    def test_openbsd(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "openbsd7")
        assert detect_platform() == "openbsd"

    def test_netbsd(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "netbsd10")
        assert detect_platform() == "netbsd"

    def test_dragonfly(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "dragonfly6")
        assert detect_platform() == "dragonflybsd"

    def test_dragonfly_unversioned(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "dragonfly")
        assert detect_platform() == "dragonflybsd"

    def test_ghostbsd_detects_as_freebsd(self, monkeypatch):
        # GhostBSD's kernel identifies as FreeBSD — compat mode by design.
        monkeypatch.setattr("sys.platform", "freebsd15")
        assert detect_platform() == "freebsd"

    def test_unsupported(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "haiku1")
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
        # Unknown architectures are passed through as-is rather than raising.
        assert detect_arch() == "mips64"


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

    def test_freebsd_libc(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "freebsd14")
        assert detect_libc() == "freebsd-libc"

    def test_openbsd_libc(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "openbsd7")
        assert detect_libc() == "openbsd-libc"

    def test_netbsd_libc(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "netbsd10")
        assert detect_libc() == "netbsd-libc"

    def test_dragonfly_libc(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "dragonfly6")
        assert detect_libc() == "dragonfly-libc"


class TestDefaultTuple:
    def test_returns_dict(self):
        t = default_tuple()
        assert "platform" in t
        assert "arch" in t
        assert t["platform"] in (
            "linux",
            "macos",
            "windows",
            "freebsd",
            "openbsd",
            "netbsd",
            "dragonflybsd",
        )
        assert t["arch"] in ("x86_64", "arm64")
