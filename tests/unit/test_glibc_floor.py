"""glibc floors for linux bundles — cvcpkg's manylinux analogue.

glibc is backward- but NOT forward-compatible, so a bundle built on 2.39 will
not start on a 2.35 host.  On a heterogeneous fleet (four builders on 2.35,
the CUDA host on 2.39) that made usability depend on which machine the
scheduler happened to pick, and silently broke the cp313t python column.
"""

from __future__ import annotations

import struct

import pytest

from cvcpkg.glibc import (
    DEFAULT_FLOOR,
    builder_capabilities,
    capability_name,
    check_floor,
    format_version,
    host_glibc,
    max_required_glibc,
    parse_version,
    target_floor,
)


def _fake_elf(path, glibc_versions=()):
    """A file that starts with the ELF magic and mentions GLIBC_x.y symbols."""
    body = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 8
    body += struct.pack("<HH", 3, 62)
    for v in glibc_versions:
        body += b"\x00GLIBC_" + v.encode() + b"\x00"
    path.write_bytes(body)
    return path


class TestVersionHandling:
    @pytest.mark.parametrize(
        "text,expected",
        [("2.35", (2, 35)), ("glibc 2.39", (2, 39)), ("2.28.1", (2, 28)), ("nope", None)],
    )
    def test_parse(self, text, expected):
        assert parse_version(text) == expected

    def test_ordering_is_numeric_not_lexical(self):
        # The trap: "2.9" > "2.10" as strings, and 2.9 > 2.10 as floats.
        assert parse_version("2.9") < parse_version("2.10")

    def test_capability_name(self):
        assert capability_name((2, 35)) == "glibc2.35"
        assert format_version((2, 39)) == "2.39"


class TestBuilderCapabilities:
    def test_builder_satisfies_its_own_and_higher_floors(self):
        # A 2.35 builder can produce bundles for 2.35+ hosts...
        caps = builder_capabilities((2, 35))
        assert "glibc2.35" in caps
        assert "glibc2.39" in caps

    def test_builder_cannot_satisfy_a_lower_floor(self):
        # ...but NOT for a 2.28 host: its binaries demand symbols 2.28 lacks.
        assert "glibc2.28" not in builder_capabilities((2, 35))
        assert "glibc2.17" not in builder_capabilities((2, 35))

    def test_newer_builder_excluded_from_fleet_floor(self):
        # The actual bug: the 2.39 CUDA host must NOT be eligible for jobs
        # targeting the fleet's 2.35 floor.
        caps = builder_capabilities((2, 39))
        assert "glibc2.35" not in caps
        assert "glibc2.39" in caps

    def test_non_glibc_host_advertises_nothing(self, monkeypatch):
        # musl/macOS: no glibc at all. `None` as an ARGUMENT means "detect the
        # host", so the non-glibc case has to come from detection returning
        # None rather than from passing it in.
        import cvcpkg.glibc as g

        monkeypatch.setattr(g, "host_glibc", lambda: None)
        assert g.builder_capabilities() == set()

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HOST_GLIBC", "2.28")
        assert host_glibc() == (2, 28)
        assert "glibc2.28" in builder_capabilities()


class TestArtifactScanning:
    def test_finds_the_highest_required_version(self, tmp_path):
        _fake_elf(tmp_path / "libfoo.so", ["2.17", "2.38", "2.4"])
        assert max_required_glibc(tmp_path) == (2, 38)

    def test_ignores_non_elf_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("GLIBC_2.99 is not real")
        assert max_required_glibc(tmp_path) is None

    def test_scans_a_whole_tree(self, tmp_path):
        (tmp_path / "lib").mkdir()
        _fake_elf(tmp_path / "lib" / "a.so", ["2.31"])
        _fake_elf(tmp_path / "lib" / "b.so", ["2.39"])
        assert max_required_glibc(tmp_path) == (2, 39)

    def test_missing_path_is_not_an_error(self, tmp_path):
        assert max_required_glibc(tmp_path / "nope") is None


class TestFloorCheck:
    def test_passes_when_at_or_below_floor(self, tmp_path):
        _fake_elf(tmp_path / "x.so", ["2.35"])
        ok, req, _ = check_floor(tmp_path, (2, 35))
        assert ok and req == (2, 35)

    def test_fails_when_above_floor(self, tmp_path):
        # The exact shape of the python313t breakage.
        _fake_elf(tmp_path / "libpython3.13t.so.1.0", ["2.38"])
        ok, req, msg = check_floor(tmp_path, (2, 35))
        assert not ok
        assert req == (2, 38)
        assert "2.38" in msg and "2.35" in msg
        assert "rebuild" in msg.lower(), "the message must say what to do about it"

    def test_pure_python_bundle_passes(self, tmp_path):
        (tmp_path / "mod.py").write_text("x = 1")
        ok, req, _ = check_floor(tmp_path, (2, 35))
        assert ok and req is None

    def test_default_floor_is_the_fleet_floor(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_GLIBC_FLOOR", raising=False)
        assert target_floor() == DEFAULT_FLOOR == (2, 35)

    def test_floor_is_configurable(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_GLIBC_FLOOR", "2.28")
        assert target_floor() == (2, 28)
