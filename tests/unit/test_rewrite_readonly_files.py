"""Prefix rewriting must survive read-only files in a dependency tree.

Dependencies legitimately ship read-only files: perl installs
``lib/<ver>/<arch>/Config.pm`` mode 444.  Prefix rewriting walks the whole
installed tree (it has to -- automake's libdir lives in a ``.pm``), so it
reaches those files.  A bare ``write_text`` raises EACCES there, and because
the rewrite runs as one pass over the dep prefix, a single read-only file
aborted the entire dependency install and failed the build.

Real failure this pins:

    _install_deps -> _rewrite_script_prefixes
    PermissionError: [Errno 13] Permission denied:
      .../cvcpkg-prefix-openssh-*/lib/5.40.2/x86_64-linux-thread-multi/Config.pm
"""

from __future__ import annotations

import stat

import pytest

from cvcpkg.builder import (
    _rewrite_pc_prefixes,
    _rewrite_script_prefixes,
    _write_text_preserving_mode,
)

# Must match _TEMP_PREFIX_RE: /<dirs>/cvcpkg-<name>-<id>/install
STALE = "/tmp/cvcpkg-openssh-abc123/install"

READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH  # 0o444


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


class TestWriteTextPreservingMode:
    def test_writes_a_read_only_file_and_restores_its_mode(self, tmp_path):
        f = tmp_path / "Config.pm"
        f.write_text("old", encoding="utf-8")
        f.chmod(READ_ONLY)
        assert _mode(f) == 0o444

        _write_text_preserving_mode(f, "new")

        assert f.read_text(encoding="utf-8") == "new"
        assert _mode(f) == 0o444, "the file must not be left more writable than it was"

    def test_ordinary_writable_file_is_untouched_in_mode(self, tmp_path):
        f = tmp_path / "plain.txt"
        f.write_text("old", encoding="utf-8")
        f.chmod(0o644)

        _write_text_preserving_mode(f, "new")

        assert f.read_text(encoding="utf-8") == "new"
        assert _mode(f) == 0o644


@pytest.mark.skipif(
    hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
    reason="root ignores the write bit, so a read-only file is still writable",
)
class TestRewritersTolerateReadOnlyFiles:
    def test_script_rewrite_does_not_die_on_a_read_only_pm(self, tmp_path):
        """The exact shape that broke openssh: a read-only .pm holding a stale path."""
        prefix = tmp_path / "prefix"
        (prefix / "lib" / "5.40.2" / "x86_64-linux-thread-multi").mkdir(parents=True)
        cfg = prefix / "lib" / "5.40.2" / "x86_64-linux-thread-multi" / "Config.pm"
        cfg.write_text(f"my $incpath = '{STALE}/include';\n", encoding="utf-8")
        cfg.chmod(READ_ONLY)

        # Must not raise.
        _rewrite_script_prefixes(prefix)

        text = cfg.read_text(encoding="utf-8")
        assert STALE not in text, "the stale temp prefix should have been rewritten"
        assert str(prefix) in text
        assert _mode(cfg) == 0o444, "mode must be restored"

    def test_pc_rewrite_does_not_die_on_a_read_only_pc(self, tmp_path):
        prefix = tmp_path / "prefix"
        pc_dir = prefix / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True)
        pc = pc_dir / "openssl.pc"
        pc.write_text(f"prefix={STALE}\nName: openssl\n", encoding="utf-8")
        pc.chmod(READ_ONLY)

        _rewrite_pc_prefixes(prefix)

        assert f"prefix={prefix}" in pc.read_text(encoding="utf-8")
        assert _mode(pc) == 0o444

    def test_one_read_only_file_does_not_abort_the_whole_pass(self, tmp_path):
        """The actual severity: a single unwritable file killed the dep install.

        Later files in the walk must still be rewritten.
        """
        prefix = tmp_path / "prefix"
        bin_dir = prefix / "bin"
        bin_dir.mkdir(parents=True)
        lib = prefix / "lib" / "5.40.2" / "x86_64-linux-thread-multi"
        lib.mkdir(parents=True)

        blocker = lib / "Config.pm"
        blocker.write_text(f"path = '{STALE}'\n", encoding="utf-8")
        blocker.chmod(READ_ONLY)

        for name in ("aclocal", "automake", "libtoolize"):
            s = bin_dir / name
            s.write_text(f"#!/bin/sh\nprefix={STALE}\n", encoding="utf-8")
            s.chmod(0o755)

        _rewrite_script_prefixes(prefix)

        for name in ("aclocal", "automake", "libtoolize"):
            assert STALE not in (bin_dir / name).read_text(encoding="utf-8"), (
                f"{name} was not rewritten — a read-only file earlier in the walk "
                "aborted the pass"
            )
