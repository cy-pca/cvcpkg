"""Tests for cvcpkg.semver — version parsing and range matching."""

from cvcpkg.semver import Version, satisfies


class TestVersionParse:
    def test_simple(self):
        v = Version.parse("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.pre == ""
        assert v.build == ""

    def test_with_build_metadata(self):
        v = Version.parse("1.76.0+cvc.2")
        assert v.major == 1
        assert v.minor == 76
        assert v.patch == 0
        assert v.build == "cvc.2"
        assert v.cvc_revision == 2

    def test_prerelease(self):
        v = Version.parse("1.0.0-alpha.1")
        assert v.pre == "alpha.1"
        assert v < Version.parse("1.0.0")

    def test_major_only(self):
        v = Version.parse("3")
        assert v == Version(3, 0, 0, "", "")

    def test_major_minor(self):
        v = Version.parse("1.14")
        assert v == Version(1, 14, 0, "", "")


class TestVersionOrdering:
    def test_ascending(self):
        assert Version.parse("1.0.0") < Version.parse("2.0.0")
        assert Version.parse("1.0.0") < Version.parse("1.1.0")
        assert Version.parse("1.0.0") < Version.parse("1.0.1")

    def test_build_metadata_ignored(self):
        a = Version.parse("1.0.0+cvc.1")
        b = Version.parse("1.0.0+cvc.5")
        assert a == b  # build metadata ignored in comparisons

    def test_prerelease_before_release(self):
        assert Version.parse("1.0.0-rc.1") < Version.parse("1.0.0")

    def test_cvc_revision(self):
        v = Version.parse("1.86.0+cvc.3")
        assert v.cvc_revision == 3


class TestSatisfies:
    def test_exact(self):
        assert satisfies("1.3.1", "==1.3.1")
        assert not satisfies("1.3.2", "==1.3.1")

    def test_gte(self):
        assert satisfies("1.3.1", ">=1.3.0")
        assert satisfies("1.3.1", ">=1.3.1")
        assert not satisfies("1.2.9", ">=1.3.0")

    def test_lt(self):
        assert satisfies("1.2.9", "<1.3.0")
        assert not satisfies("1.3.0", "<1.3.0")

    def test_caret(self):
        assert satisfies("1.3.1", "^1.3")
        assert satisfies("1.99.0", "^1.3")
        assert not satisfies("2.0.0", "^1.3")

    def test_tilde(self):
        assert satisfies("1.3.5", "~>1.3.0")
        assert not satisfies("1.4.0", "~>1.3.0")

    def test_comma_separated(self):
        assert satisfies("1.3.5", ">=1.3.0,<2.0.0")
        assert not satisfies("2.0.0", ">=1.3.0,<2.0.0")
        assert not satisfies("1.2.0", ">=1.3.0,<2.0.0")

    def test_large_version_numbers(self):
        # Abseil-style "date" versions
        assert satisfies("20240722", ">=20240722,<20260000")
        assert not satisfies("20230101", ">=20240722,<20260000")
