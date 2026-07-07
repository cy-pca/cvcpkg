"""Tests for cvcpkg.abi — ABI compatibility checks."""

from __future__ import annotations

import pytest

from cvcpkg.abi import check_abi_compat
from cvcpkg.errors import AbiError
from cvcpkg.manifest import AbiTag


class TestCheckAbiCompat:
    def test_identical_tags_pass(self):
        a = AbiTag(cxx_std=17, cxx_runtime="libstdc++", libc="glibc-2.31", crt_link="dynamic")
        issues = check_abi_compat(a, a, strict=True)
        assert issues == []

    def test_cxx_std_mismatch_strict(self):
        a = AbiTag(cxx_std=17)
        b = AbiTag(cxx_std=20)
        with pytest.raises(AbiError, match="C\\+\\+ standard mismatch"):
            check_abi_compat(a, b, strict=True)

    def test_cxx_std_mismatch_non_strict(self):
        a = AbiTag(cxx_std=17)
        b = AbiTag(cxx_std=20)
        issues = check_abi_compat(a, b, strict=False)
        assert any("C++ standard mismatch" in i for i in issues)

    def test_cxx_std_zero_ignored(self):
        a = AbiTag(cxx_std=17)
        b = AbiTag(cxx_std=0)
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []

    def test_runtime_family_mismatch(self):
        a = AbiTag(cxx_runtime="libstdc++-12")
        b = AbiTag(cxx_runtime="libc++-15")
        with pytest.raises(AbiError, match="runtime family mismatch"):
            check_abi_compat(a, b, strict=True)

    def test_runtime_same_family_ok(self):
        a = AbiTag(cxx_runtime="libstdc++-11")
        b = AbiTag(cxx_runtime="libstdc++-12")
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []

    def test_runtime_empty_ignored(self):
        a = AbiTag(cxx_runtime="libstdc++")
        b = AbiTag(cxx_runtime="")
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []

    def test_libc_mismatch(self):
        a = AbiTag(libc="glibc-2.31")
        b = AbiTag(libc="musl")
        with pytest.raises(AbiError, match="libc mismatch"):
            check_abi_compat(a, b, strict=True)

    def test_libc_same_ok(self):
        a = AbiTag(libc="glibc-2.31")
        b = AbiTag(libc="glibc-2.31")
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []

    def test_crt_link_mismatch(self):
        a = AbiTag(crt_link="dynamic")
        b = AbiTag(crt_link="static")
        with pytest.raises(AbiError, match="CRT link mismatch"):
            check_abi_compat(a, b, strict=True)

    def test_crt_link_empty_ignored(self):
        a = AbiTag(crt_link="dynamic")
        b = AbiTag(crt_link="")
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []

    def test_multiple_mismatches_non_strict(self):
        a = AbiTag(cxx_std=17, libc="glibc-2.31", crt_link="dynamic")
        b = AbiTag(cxx_std=20, libc="musl", crt_link="static")
        issues = check_abi_compat(a, b, strict=False)
        assert len(issues) == 3

    def test_multiple_mismatches_strict_raises(self):
        a = AbiTag(cxx_std=17, libc="glibc-2.31")
        b = AbiTag(cxx_std=20, libc="musl")
        with pytest.raises(AbiError) as exc_info:
            check_abi_compat(a, b, strict=True)
        msg = str(exc_info.value)
        assert "C++ standard" in msg
        assert "libc" in msg

    def test_all_empty_ok(self):
        a = AbiTag()
        b = AbiTag()
        issues = check_abi_compat(a, b, strict=True)
        assert issues == []
