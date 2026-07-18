"""Host-tools install record + strip semantics.

Build-time host tools install into a separate host-tools prefix; that
separation is recorded in the deliverable prefix
(``share/libcvc-deps/host-tools.yaml``) so install can strip the host-tools
prefix -- unless the caller keeps it.  The per-bundle ``host_tool`` manifest
flag is exercised here too (via ``generate_manifest``).
"""

from __future__ import annotations

import yaml

from cvcpkg.builder import Recipe, generate_manifest
from cvcpkg.host_tools import (
    HostToolsRecord,
    read_host_tools_record,
    record_path,
    strip_host_tools,
    write_host_tools_record,
)

# ── the per-bundle host_tool manifest flag ──────────────────────


def _recipe(tmp_path, raw):
    d = tmp_path / "recipes" / raw["recipe"]["name"]
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(raw))
    (d / "build.sh").write_text("#!/bin/sh\ntrue\n")
    return Recipe.load(d)


_LIB_RAW = {
    "schema_version": 1,
    "recipe": {"name": "zlib", "upstream_version": "1.3.1", "cvc_revision": 1},
    "source": {"type": "vendored", "path": "."},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
}

_TOOLCHAIN_RAW = {
    "schema_version": 1,
    "recipe": {"name": "emsdk", "upstream_version": "3.1.0", "cvc_revision": 1},
    "source": {"type": "vendored", "path": "."},
    "cross_toolchain": {"target_platforms": ["wasm"], "env": {}},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
}


class TestHostToolManifestFlag:
    def test_normal_recipe_has_no_host_tool_flag(self, tmp_path):
        r = _recipe(tmp_path, _LIB_RAW)
        install = tmp_path / "install"
        install.mkdir()
        m = generate_manifest(r, install, "linux", "x86_64", "release", "shared")
        # Absent for deliverables (keeps normal manifests clean).
        assert "host_tool" not in m["bundle"]

    def test_cross_toolchain_recipe_flagged_host_tool(self, tmp_path):
        r = _recipe(tmp_path, _TOOLCHAIN_RAW)
        install = tmp_path / "install"
        install.mkdir()
        m = generate_manifest(r, install, "linux", "x86_64", "release", "shared")
        assert m["bundle"]["host_tool"] is True

    def test_explicit_override_true(self, tmp_path):
        r = _recipe(tmp_path, _LIB_RAW)
        install = tmp_path / "install"
        install.mkdir()
        m = generate_manifest(r, install, "linux", "x86_64", "release", "shared", host_tool=True)
        assert m["bundle"]["host_tool"] is True

    def test_explicit_override_false_beats_derivation(self, tmp_path):
        r = _recipe(tmp_path, _TOOLCHAIN_RAW)
        install = tmp_path / "install"
        install.mkdir()
        m = generate_manifest(r, install, "linux", "x86_64", "release", "shared", host_tool=False)
        assert "host_tool" not in m["bundle"]


# ── the host-tools install record ───────────────────────────────


class TestHostToolsRecord:
    def test_write_then_read_roundtrip(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        ht = tmp_path / "deps.host-tools"
        write_host_tools_record(prefix, ht, ["bazel", "cmake", "bazel"])

        rec = read_host_tools_record(prefix)
        assert rec is not None
        assert rec.present is True
        assert rec.prefix == str(ht)
        # deduped + sorted
        assert rec.tools == ["bazel", "cmake"]
        assert rec.stripped is False

    def test_record_written_under_share_libcvc_deps(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        write_host_tools_record(prefix, tmp_path / "ht", ["ninja"])
        p = record_path(prefix)
        assert p == prefix / "share" / "libcvc-deps" / "host-tools.yaml"
        assert p.is_file()
        data = yaml.safe_load(p.read_text())
        assert data["schema_version"] == 1
        assert data["host_tools"]["present"] is True

    def test_read_absent_returns_none(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        assert read_host_tools_record(prefix) is None

    def test_from_dict_tolerates_missing_block(self):
        rec = HostToolsRecord.from_dict({"schema_version": 1})
        assert rec.present is False
        assert rec.tools == []


# ── strip semantics ─────────────────────────────────────────────


class TestStripHostTools:
    def _setup(self, tmp_path, tools=("bazel",)):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        ht = tmp_path / "deps.host-tools"
        (ht / "bin").mkdir(parents=True)
        (ht / "bin" / "bazel").write_text("#!/bin/sh\n")
        write_host_tools_record(prefix, ht, list(tools))
        return prefix, ht

    def test_strip_removes_prefix_and_marks_stripped(self, tmp_path):
        prefix, ht = self._setup(tmp_path)
        stripped = strip_host_tools(
            prefix, keep=False, now="2026-07-16T00:00:00+00:00", owned_prefix=ht
        )
        assert stripped == ht
        assert not ht.exists()
        # record updated in place
        rec = read_host_tools_record(prefix)
        assert rec is not None
        assert rec.stripped is True
        assert rec.stripped_at == "2026-07-16T00:00:00+00:00"
        # tools preserved for provenance
        assert rec.tools == ["bazel"]

    def test_keep_leaves_prefix_intact(self, tmp_path):
        prefix, ht = self._setup(tmp_path)
        assert strip_host_tools(prefix, keep=True) is None
        assert ht.exists()
        rec = read_host_tools_record(prefix)
        assert rec is not None
        assert rec.stripped is False

    def test_no_record_is_noop(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        assert strip_host_tools(prefix, keep=False) is None

    def test_already_stripped_is_noop(self, tmp_path):
        prefix, ht = self._setup(tmp_path)
        strip_host_tools(prefix, keep=False, now="2026-07-16T00:00:00+00:00", owned_prefix=ht)
        # second call: record says stripped -> nothing to do
        assert strip_host_tools(prefix, keep=False, owned_prefix=ht) is None

    def test_refuses_to_strip_when_prefix_equals_deliverable(self, tmp_path):
        # separation disabled: host-tools prefix == deliverable prefix
        prefix = tmp_path / "deps"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "cmake").write_text("x")
        write_host_tools_record(prefix, prefix, ["cmake"])
        assert strip_host_tools(prefix, keep=False) is None
        # deliverable must be untouched
        assert (prefix / "bin" / "cmake").exists()

    def test_refuses_a_build_machine_path_outside_the_deliverable(self, tmp_path):
        # THE install case.  The record ships inside the package, so `prefix:`
        # is an absolute path from whatever machine built it.  Installing must
        # never delete it: here it is someone's home directory, not ours.
        #
        # Real incident: `cvcpkg install --prefix /tmp/smoke-release` printed
        # "stripped build prefix /home/usjrkx/clients/wt-validate-out3.build"
        # and deleted the build machine's directory.
        victim = tmp_path / "somebody-elses-data"
        (victim / "important").mkdir(parents=True)
        (victim / "important" / "file.txt").write_text("do not delete me")

        prefix = tmp_path / "install-prefix"
        prefix.mkdir()
        write_host_tools_record(prefix, victim, ["bazel"])

        # install passes no owned_prefix: it never creates a build prefix.
        assert strip_host_tools(prefix, keep=False) is None
        assert (victim / "important" / "file.txt").read_text() == "do not delete me"
        # and it must stay retryable rather than claim a false success
        rec = read_host_tools_record(prefix)
        assert rec is not None and rec.stripped is False

    def test_strips_a_build_prefix_that_shipped_inside_the_deliverable(self, tmp_path):
        # The one thing install may prune: a tree that actually arrived in the
        # package, so removing it only undoes what we extracted.
        prefix = tmp_path / "deps"
        shipped = prefix / "build-prefix"
        (shipped / "bin").mkdir(parents=True)
        (shipped / "bin" / "bazel").write_text("x")
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "app").write_text("keep")
        write_host_tools_record(prefix, shipped, ["bazel"])

        assert strip_host_tools(prefix, keep=False, now="t") == shipped
        assert not shipped.exists()
        assert (prefix / "bin" / "app").exists(), "pruning must not touch the deliverable"

    def test_owned_prefix_must_match_the_record_to_strip(self, tmp_path):
        # Vouching for one path does not authorise deleting a different one.
        prefix, ht = self._setup(tmp_path)
        other = tmp_path / "not-the-recorded-one"
        other.mkdir()
        assert strip_host_tools(prefix, keep=False, owned_prefix=other) is None
        assert ht.exists()

    def test_missing_prefix_dir_still_marks_stripped(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        gone = tmp_path / "deps.host-tools"  # never created
        write_host_tools_record(prefix, gone, ["bazel"])
        stripped = strip_host_tools(prefix, keep=False, now="t", owned_prefix=gone)
        assert stripped == gone
        rec = read_host_tools_record(prefix)
        assert rec is not None and rec.stripped is True

    def test_partial_strip_stays_retryable(self, tmp_path, monkeypatch):
        # If removal only partially succeeds (locked/read-only files), the
        # prefix survives -> record must stay stripped=false (retryable) and
        # strip must NOT falsely report success.
        import cvcpkg.host_tools as ht_mod

        prefix, ht = self._setup(tmp_path)
        monkeypatch.setattr(ht_mod.shutil, "rmtree", lambda *a, **k: None)  # no-op
        result = strip_host_tools(prefix, keep=False, now="t", owned_prefix=ht)
        assert result is None  # nothing conclusively stripped
        assert ht.exists()  # leftover remains
        rec = read_host_tools_record(prefix)
        assert rec is not None
        assert rec.stripped is False  # retryable, not a false success
        assert rec.stripped_at == ""
        # a later call (with real rmtree restored) still strips it
        monkeypatch.undo()
        assert strip_host_tools(prefix, keep=False, now="t2", owned_prefix=ht) == ht
        assert not ht.exists()

    def test_symlinked_prefix_unlinks_link_not_target(self, tmp_path):
        # A host-tools prefix that is a symlink (e.g. to a shared toolchain
        # cache) must be unlinked, not rmtree'd, and the target preserved.
        prefix = tmp_path / "deps"
        prefix.mkdir()
        real = tmp_path / "shared-cache"
        (real / "bin").mkdir(parents=True)
        (real / "bin" / "bazel").write_text("x")
        link = tmp_path / "deps.host-tools"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            import pytest

            pytest.skip("symlinks unsupported on this platform")
        write_host_tools_record(prefix, link, ["bazel"])

        stripped = strip_host_tools(prefix, keep=False, now="t", owned_prefix=link)
        assert stripped == link
        assert not link.exists() and not link.is_symlink()  # link removed
        assert real.exists() and (real / "bin" / "bazel").exists()  # target kept
        rec = read_host_tools_record(prefix)
        assert rec is not None and rec.stripped is True


# ── CLI wiring ──────────────────────────────────────────────────


class TestHostToolsCliFlags:
    """The --keep-host-tools/--strip-host-tools flags are wired on both
    commands (also asserts the callback signatures accept the new param)."""

    def _help(self, args):
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        return CliRunner().invoke(cli, args)

    def test_build_has_keep_build_prefix_flag(self):
        res = self._help(["build", "--help"])
        assert res.exit_code == 0
        assert "--keep-build-prefix" in res.output
        assert "--strip-build-prefix" in res.output

    def test_install_has_keep_build_prefix_flag(self):
        res = self._help(["install", "--help"])
        assert res.exit_code == 0
        assert "--keep-build-prefix" in res.output
        assert "--strip-build-prefix" in res.output
        # the old spelling is a hidden deprecated alias
        assert "--keep-host-tools" not in res.output

    def test_build_has_build_prefix_flag(self):
        res = self._help(["build", "--help"])
        assert res.exit_code == 0
        assert "--build-prefix" in res.output

    def test_deprecated_host_tools_aliases_still_accepted(self):
        # hidden from --help, but must still parse (back-compat)
        res = self._help(["build", "--help"])
        assert "--host-tools-prefix" not in res.output

    def test_empty_install_is_a_noop_and_leaves_record_intact(self, tmp_path):
        """'cvcpkg install' with no components short-circuits before finalize,
        so it must not touch a pre-existing host-tools prefix or its record."""
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        prefix = tmp_path / "deps"
        prefix.mkdir()
        ht = tmp_path / "deps.host-tools"
        (ht / "bin").mkdir(parents=True)
        (ht / "bin" / "bazel").write_text("x")
        write_host_tools_record(prefix, ht, ["bazel"])

        reqs = tmp_path / "cvc-requirements.yaml"
        reqs.write_text("components: []\n")
        res = CliRunner().invoke(
            cli, ["install", "--from", str(reqs), "--prefix", str(prefix), "--local"]
        )
        assert res.exit_code == 0
        assert ht.exists()  # untouched
        rec = read_host_tools_record(prefix)
        assert rec is not None and rec.stripped is False
