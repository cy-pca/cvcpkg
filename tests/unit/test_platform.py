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
    lib_path_var,
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

    def test_dragonfly_detects_as_freebsd(self, monkeypatch):
        # DragonFly consumes the freebsd channel in compat mode. It was never in
        # the recipe schema's platform enum, so no recipe could declare a
        # dragonflybsd build and such a host resolved zero packages.
        monkeypatch.setattr("sys.platform", "dragonfly6")
        assert detect_platform() == "freebsd"

    def test_dragonfly_unversioned_detects_as_freebsd(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "dragonfly")
        assert detect_platform() == "freebsd"

    def test_ghostbsd_detects_as_freebsd(self, monkeypatch):
        # GhostBSD's kernel identifies as FreeBSD — compat mode by design.
        monkeypatch.setattr("sys.platform", "freebsd15")
        assert detect_platform() == "freebsd"

    # Haiku's sys.platform is tri-valued and depends on which CPython is
    # running: upstream CPython appends the major version ("haiku1"),
    # HaikuPorts' python3 carries a MACHDEP patch that reports the bare
    # "haiku", and some builds report the full ABI string ("haikuR1~beta5").
    # detect_platform() must collapse all three, so all three are pinned here.
    @pytest.mark.parametrize("sys_platform", ["haiku", "haiku1", "haikuR1~beta5"])
    def test_haiku(self, monkeypatch, sys_platform):
        monkeypatch.setattr("sys.platform", sys_platform)
        assert detect_platform() == "haiku"

    def test_unsupported(self, monkeypatch):
        # A genuinely unsupported kernel — not Haiku, which is now first-class.
        monkeypatch.setattr("sys.platform", "aix7")
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

    def test_dragonfly_libc_is_freebsd(self, monkeypatch):
        # Follows detect_platform's compat mapping.
        monkeypatch.setattr("sys.platform", "dragonfly6")
        assert detect_libc() == "freebsd-libc"

    def test_haiku_libroot(self, monkeypatch):
        # Regression: with no haiku branch, Haiku fell through to the Linux
        # glibc probe, CDLL("libc.so.6") raised, and the host was reported as
        # "musl" — a wrong ABI tuple that silently mismatches every bundle.
        monkeypatch.setattr("sys.platform", "haiku1")
        assert detect_libc() == "haiku-libroot"


class TestLibPathVar:
    """The loader variable is per-platform and Haiku is the odd one out."""

    def test_macos_uses_dyld(self):
        assert lib_path_var("macos") == "DYLD_LIBRARY_PATH"

    def test_haiku_uses_library_path(self):
        # Haiku's runtime_loader reads LIBRARY_PATH and ignores
        # LD_LIBRARY_PATH; getting this wrong fails silently at link time.
        assert lib_path_var("haiku") == "LIBRARY_PATH"

    @pytest.mark.parametrize("plat", ["linux", "freebsd", "openbsd", "netbsd", "wasi"])
    def test_everything_else_uses_ld_library_path(self, plat):
        assert lib_path_var(plat) == "LD_LIBRARY_PATH"


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
            "haiku",
        )
        assert t["arch"] in ("x86_64", "arm64")
