"""Re-homing a prefix: repointing paths a package baked in at configure time.

Recipes build into an isolated temp install dir and the resulting archive is
later extracted into some *other* prefix, so anything a package hardcoded at
configure time points at a directory that no longer exists.  ``build_all``
repoints them when merging; the builder must do the same after extracting a
dependency archive, which it did not -- swig died on the dev cluster with:

    aclocal: error: couldn't open directory
    '/tmp/cvcpkg-builder/cvcpkg-automake-g3spihed/install/share/aclocal-1.17'

The fixtures below are the literal shapes taken from that published
automake-1.17 artifact, so they encode the real failure rather than an
invented one.
"""

from __future__ import annotations

from cvcpkg.builder import _rewrite_pc_prefixes, _rewrite_script_prefixes

# The exact baked prefix from the automake artifact that broke swig.
_BAKED = "/tmp/cvcpkg-builder/cvcpkg-automake-g3spihed/install"


def _prefix(tmp_path):
    p = tmp_path / "deps"
    (p / "bin").mkdir(parents=True)
    return p


class TestScriptRehoming:
    def test_aclocal_shaped_script_is_rehomed(self, tmp_path):
        # Verbatim lines 28/68/69 of the shipped bin/aclocal.
        p = _prefix(tmp_path)
        aclocal = p / "bin" / "aclocal"
        aclocal.write_text(
            "#!/usr/bin/perl\n"
            f"  unshift (@INC, '{_BAKED}/share/automake-1.17')\n"
            f"my @automake_includes = ('{_BAKED}/share/aclocal-' . $APIVERSION);\n"
            f"my @system_includes = ('{_BAKED}/share/aclocal');\n"
        )
        _rewrite_script_prefixes(p)
        text = aclocal.read_text()
        assert _BAKED not in text
        assert f"{p}/share/aclocal-" in text
        assert f"{p}/share/automake-1.17" in text

    def test_automake_config_pm_under_share_is_rehomed(self, tmp_path):
        # THE gap: rewriting only bin/ fixes aclocal but leaves automake broken,
        # because automake reads $libdir/am/*.am and $libdir lives here.
        p = _prefix(tmp_path)
        cfg = p / "share" / "automake-1.17" / "Automake" / "Config.pm"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            f"our $libdir = $ENV{{\"AUTOMAKE_LIBDIR\"}} || '{_BAKED}/share/automake-1.17';\n"
        )
        _rewrite_script_prefixes(p)
        text = cfg.read_text()
        assert _BAKED not in text
        assert f"{p}/share/automake-1.17" in text

    def test_libtool_la_archive_is_rehomed(self, tmp_path):
        # .la files carry an absolute libdir= that breaks downstream libtool links.
        p = _prefix(tmp_path)
        la = p / "lib" / "libfoo.la"
        la.parent.mkdir(parents=True)
        la.write_text(f"dlname='libfoo.so.1'\nlibdir='{_BAKED}/lib'\n")
        _rewrite_script_prefixes(p)
        assert f"libdir='{p}/lib'" in la.read_text()

    def test_binary_file_is_never_rewritten(self, tmp_path):
        # Substitution changes length; doing it to a binary corrupts offsets.
        # The NUL sits past a 512-byte sniff window on purpose.
        p = _prefix(tmp_path)
        blob = p / "bin" / "tool"
        original = b"A" * 600 + b"\x00" + _BAKED.encode() + b"\x00"
        blob.write_bytes(original)
        _rewrite_script_prefixes(p)
        assert blob.read_bytes() == original

    def test_undecodable_bytes_are_left_alone(self, tmp_path):
        # errors="replace" + write-back would silently swap these for U+FFFD.
        p = _prefix(tmp_path)
        script = p / "bin" / "latin"
        original = b"#!/bin/sh\n# caf\xe9\necho " + _BAKED.encode() + b"\n"
        script.write_bytes(original)
        _rewrite_script_prefixes(p)
        assert script.read_bytes() == original

    def test_oversized_file_is_skipped(self, tmp_path):
        from cvcpkg.builder import _MAX_REWRITE_BYTES

        p = _prefix(tmp_path)
        big = p / "bin" / "huge.sh"
        big.write_text(f"{_BAKED}\n" + "x" * (_MAX_REWRITE_BYTES + 1))
        _rewrite_script_prefixes(p)
        assert _BAKED in big.read_text()

    def test_unrelated_absolute_paths_are_untouched(self, tmp_path):
        # Only cvcpkg temp-install dirs are stale by construction; a real system
        # path must survive.
        p = _prefix(tmp_path)
        script = p / "bin" / "keep.sh"
        script.write_text("#!/bin/sh\nexec /usr/bin/perl /opt/vendor/install/x\n")
        before = script.read_text()
        _rewrite_script_prefixes(p)
        assert script.read_text() == before

    def test_data_file_outside_the_suffix_allowlist_is_skipped(self, tmp_path):
        # Bounds the walk: we do not read every byte of a multi-GB prefix.
        p = _prefix(tmp_path)
        doc = p / "share" / "doc" / "readme.txt"
        doc.parent.mkdir(parents=True)
        doc.write_text(f"built in {_BAKED}\n")
        _rewrite_script_prefixes(p)
        assert _BAKED in doc.read_text()

    def test_backslashes_in_the_target_path_are_not_regex_escapes(self, tmp_path):
        # ``re`` treats backslashes in a *replacement* as escapes, so rewriting
        # into a Windows prefix (C:\Users\...) died with
        #   re.error: bad escape \U at position 2
        # on every windows-latest job.  A prefix containing backslashes
        # reproduces it on any platform: POSIX makes one literal directory of
        # this name, Windows makes it nested -- either way str(prefix) carries
        # backslashes, and "\U" is an invalid escape.
        p = tmp_path / r"deps\Users\x"
        (p / "bin").mkdir(parents=True)
        script = p / "bin" / "aclocal"
        script.write_text(f"my @inc = ('{_BAKED}/share/aclocal');")
        _rewrite_script_prefixes(p)  # must not raise re.error
        text = script.read_text()
        assert _BAKED not in text
        assert str(p) in text

    def test_is_idempotent(self, tmp_path):
        p = _prefix(tmp_path)
        s = p / "bin" / "aclocal"
        s.write_text(f"my @inc = ('{_BAKED}/share/aclocal');\n")
        _rewrite_script_prefixes(p)
        once = s.read_text()
        _rewrite_script_prefixes(p)
        assert s.read_text() == once


class TestPcRehoming:
    def test_pc_prefix_is_rehomed(self, tmp_path):
        # The shape shipped by the real readline artifact.
        p = _prefix(tmp_path)
        pc = p / "lib" / "pkgconfig" / "readline.pc"
        pc.parent.mkdir(parents=True)
        pc.write_text(
            f"prefix={_BAKED}\n"
            "exec_prefix=${prefix}\n"
            "libdir=${exec_prefix}/lib\n"
            "Requires.private: ncurses\n"
        )
        _rewrite_pc_prefixes(p)
        text = pc.read_text()
        assert text.startswith(f"prefix={p}\n")
        # relative derivations must survive verbatim
        assert "exec_prefix=${prefix}" in text
        assert "Requires.private: ncurses" in text

    def test_pc_outside_lib_pkgconfig_is_rehomed(self, tmp_path):
        # The builder previously only globbed lib/pkgconfig, missing these.
        p = _prefix(tmp_path)
        for rel in ("lib64/pkgconfig/foo.pc", "share/pkgconfig/bar.pc"):
            pc = p / rel
            pc.parent.mkdir(parents=True, exist_ok=True)
            pc.write_text(f"prefix={_BAKED}\nName: x\n")
        _rewrite_pc_prefixes(p)
        for rel in ("lib64/pkgconfig/foo.pc", "share/pkgconfig/bar.pc"):
            assert (p / rel).read_text().startswith(f"prefix={p}\n")
