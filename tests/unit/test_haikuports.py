"""Tests for the HaikuPorts integration — draft emitter, local lint, CLI.

Offline throughout: no network, no haikuporter, no Haiku box.  The install-tree
facts are built from real ELF objects borrowed off the host when one is
available, since the point of :func:`scan_install_tree` is that it reads real
binaries rather than trusting metadata.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cvcpkg import haikuports as hp

ZLIB_RECIPE = {
    "schema_version": 1,
    "recipe": {
        "name": "zlib",
        "upstream_version": "1.3.1",
        "cvc_revision": 3,
        "description": "zlib — a general-purpose lossless data compression library.",
        "homepage": "https://zlib.net/",
        "license": "Zlib",
    },
    "source": {
        "type": "tarball",
        "url": "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz",
        "sha256": "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23",
        "strip_components": 1,
    },
    "depends": {"build": ["cmake", "ninja"], "host_tools": []},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
    "package": {"files": ["lib/libz*"]},
    "test": {"script": "test.sh"},
}


def _recipe(**overrides):
    """A copy of ZLIB_RECIPE with top-level sections replaced."""
    import copy

    data = copy.deepcopy(ZLIB_RECIPE)
    for key, value in overrides.items():
        data[key] = value
    return data


def _make_install_tree(root: Path) -> Path:
    """A Haiku-shaped install tree with a real ELF library and command."""
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    real_so = next(iter(Path("/lib/x86_64-linux-gnu").glob("libz.so.1.*")), None)
    if real_so is None:
        pytest.skip("no host libz.so.1.* to borrow for the ELF fixture")
    shutil.copy(real_so, root / "lib" / "libz.so.1.3")
    (root / "lib" / "libz.so.1").symlink_to("libz.so.1.3")
    (root / "lib" / "libz.so").symlink_to("libz.so.1")
    shutil.copy("/bin/cat", root / "bin" / "minigzip")
    return root


# ── Name / licence / text helpers ───────────────────────────────


class TestMappings:
    def test_known_port_carries_the_real_category(self):
        # zlib is sys-libs, not dev-libs — the whole reason this is a table.
        assert hp.port_for("zlib") == ("sys-libs", "zlib", True)
        assert hp.port_for("libgeos") == ("sci-libs", "geos", True)
        assert hp.port_for("c-ares") == ("net-dns", "c_ares", True)

    def test_proposed_port_is_not_verified(self):
        cat, port, verified = hp.port_for("vtk")
        assert (cat, port) == ("sci-visualization", "vtk")
        assert verified is False

    def test_unknown_port_falls_back_loudly(self):
        assert hp.port_for("some-new-thing") == ("TODO-category", "some_new_thing", False)

    def test_resolvable_forbids_dashes(self):
        # .PackageInfo's entity_name grammar excludes '-'.
        assert hp.resolvable("c-ares") == "c_ares"
        assert hp.resolvable("i586-pc-haiku-gcc") == "i586_pc_haiku_gcc"

    def test_every_mapped_licence_is_a_real_haiku_name(self):
        assert set(hp.SPDX_TO_HAIKU.values()) <= hp.HAIKU_LICENSES

    def test_licence_translation(self):
        assert hp.haiku_licenses("Zlib")[0] == ["Zlib"]
        # SPDX ids are NOT Haiku names.
        assert hp.haiku_licenses("Apache-2.0")[0] == ["Apache v2"]
        assert hp.haiku_licenses("BSD-3-Clause")[0] == ["BSD (3-clause)"]

    def test_unmapped_licence_passes_through_with_a_todo(self):
        names, notes = hp.haiku_licenses("BSL-1.0")
        assert names == ["BSL-1.0"]
        assert any("licenses/BSL-1.0" in n for n in notes)

    def test_dual_licence_asks_a_human_to_choose(self):
        names, notes = hp.haiku_licenses("Apache-2.0 OR MIT")
        assert names == ["Apache v2", "MIT"]
        assert any("choice (OR)" in n for n in notes)

    def test_missing_licence_is_a_todo_not_a_guess(self):
        names, notes = hp.haiku_licenses(None)
        assert names == []
        assert notes and notes[0].startswith(hp.TODO)


class TestSummary:
    def test_strips_the_leading_name_and_trailing_stop(self):
        summary, notes = hp.summary_from(
            "zlib — a general-purpose lossless data compression library.", "zlib"
        )
        assert summary == "A general-purpose lossless data compression library"
        assert notes == []

    def test_flags_an_over_long_summary(self):
        long = "x — " + " ".join(["word"] * 30) + "."
        _, notes = hp.summary_from(long, "x")
        assert any("70 or fewer" in n for n in notes)

    def test_flags_a_summary_starting_with_the_port_name(self):
        _, notes = hp.summary_from("Zlib compresses things well", "zlib")
        assert any("must not start with the port name" in n for n in notes)

    def test_description_wraps_under_100_chars_with_backslashes(self):
        body, notes = hp.wrap_description(" ".join(["alpha"] * 60))
        assert all(len(line) <= 100 for line in body.split("\n"))
        assert body.count(" \\\n") >= 1
        assert notes and "SUMMARY" in notes[0]


class TestSourceHelpers:
    def test_version_becomes_portversion(self):
        url = "https://x/v1.3.1/zlib-1.3.1.tar.gz"
        assert (
            hp._interpolate_version(url, "1.3.1")
            == "https://x/v$portVersion/zlib-$portVersion.tar.gz"
        )

    def test_braces_when_followed_by_a_word_character(self):
        assert hp._interpolate_version("http://x/2.8rc", "2.8") == "http://x/${portVersion}rc"

    def test_absent_version_is_left_alone(self):
        assert hp._interpolate_version("http://x/latest.tar.gz", "1.0") == "http://x/latest.tar.gz"

    def test_archive_top_dir(self):
        assert hp._archive_top_dir("http://x/CGAL-6.0.1.tar.xz", "6.0.1") == "CGAL-6.0.1"
        assert hp._archive_top_dir("http://x/a-1.tar.bz2", "1") == "a-1"


# ── Install-tree facts (the build evidence) ─────────────────────


class TestScanInstallTree:
    def test_reads_real_sonames_and_needed(self, tmp_path):
        facts = hp.scan_install_tree(_make_install_tree(tmp_path / "install"))
        assert facts.commands == ["minigzip"]
        assert [lib.name for lib in facts.libraries] == ["libz"]
        assert facts.libraries[0].version == "1.3"
        # compat comes from the SONAME's major, not from the port version.
        assert facts.libraries[0].compat == "1"
        assert facts.needed  # DT_NEEDED of the copied command

    def test_empty_tree_scans_cleanly(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        facts = hp.scan_install_tree(root)
        assert facts.commands == [] and facts.libraries == [] and facts.needed == []

    def test_non_elf_is_ignored_not_crashed_on(self, tmp_path):
        (tmp_path / "junk").write_text("not an ELF")
        assert hp._elf_dynamic(tmp_path / "junk") == ("", [])
        assert hp._elf_dynamic(tmp_path / "missing") == ("", [])

    def test_base_system_libs_are_never_required(self, tmp_path):
        facts = hp.InstallFacts(needed=["libroot.so", "libbe.so"])
        # BASE_SYSTEM_LIBS is what the `haiku` requirement already covers.
        assert {"libroot", "libbe"} <= hp.BASE_SYSTEM_LIBS
        assert facts.own_lib_names == set()


# ── Refusals ────────────────────────────────────────────────────


class TestRefusals:
    def test_python_interpreter_columns(self):
        data = _recipe(recipe={**ZLIB_RECIPE["recipe"], "name": "click-cp312"})
        with pytest.raises(hp.ConversionRefusedError, match="PYTHON_VERSIONS"):
            hp.draft_recipe(data)

    def test_python_wheel_source(self):
        data = _recipe(source={"type": "python_wheel", "url": "https://x/a.whl"})
        with pytest.raises(hp.ConversionRefusedError, match="dev-python"):
            hp.draft_recipe(data)

    def test_vendored_source_has_no_source_uri(self):
        data = _recipe(source={"type": "vendored", "path": "third-party/levmar"})
        with pytest.raises(hp.ConversionRefusedError, match="SOURCE_URI"):
            hp.draft_recipe(data)

    def test_prebuilt_source(self):
        data = _recipe(source={"type": "prebuilt", "url": "https://x/a.zip"})
        with pytest.raises(hp.ConversionRefusedError, match="from source"):
            hp.draft_recipe(data)

    def test_missing_url(self):
        data = _recipe(source={"type": "tarball"})
        with pytest.raises(hp.ConversionRefusedError, match="SOURCE_URI"):
            hp.draft_recipe(data)

    def test_image_recipe(self):
        data = _recipe(
            recipe={**ZLIB_RECIPE["recipe"], "name": "haiku-image"},
            source={"type": "tarball", "url": "https://x/a.iso"},
        )
        with pytest.raises(
            hp.ConversionRefusedError, match="not\n?a Haiku package|not a Haiku package"
        ):
            hp.draft_recipe(data)


# ── The draft itself ────────────────────────────────────────────


class TestDraftRecipe:
    def test_placement_and_filename(self):
        draft = hp.draft_recipe(ZLIB_RECIPE)
        assert draft.relpath == "sys-libs/zlib/zlib-1.3.1.recipe"
        assert draft.filename == "zlib-1.3.1.recipe"

    def test_derived_fields(self):
        text = hp.draft_recipe(ZLIB_RECIPE).text
        assert 'SUMMARY="A general-purpose lossless data compression library"' in text
        assert 'HOMEPAGE="https://zlib.net/"' in text
        assert 'LICENSE="Zlib"' in text
        assert 'CHECKSUM_SHA256="9a93b2b7dfdac' in text
        assert "zlib-$portVersion.tar.gz" in text
        assert 'ARCHITECTURES="x86_64"' in text

    def test_revision_is_one_not_cvc_revision(self):
        # cvc_revision is 3; HaikuPorts REVISION semantics are different.
        text = hp.draft_recipe(ZLIB_RECIPE).text
        assert 'REVISION="1"' in text
        assert 'REVISION="3"' not in text

    def test_copyright_is_never_invented(self):
        draft = hp.draft_recipe(ZLIB_RECIPE)
        assert 'COPYRIGHT=""' in draft.text
        assert any("COPYRIGHT is REQUIRED" in t for t in draft.todos)

    def test_build_and_install_are_refusing_stubs(self):
        text = hp.draft_recipe(ZLIB_RECIPE).text
        assert "BUILD()\n{" in text and "INSTALL()\n{" in text
        # An accidentally-submitted draft must fail loudly, not silently pass.
        assert text.count("exit 1") >= 2
        assert "TODO(human): BUILD() is not implemented" in text

    def test_build_script_is_quoted_as_reference_not_translated(self):
        script = "#!/usr/bin/env bash\ncvc_cmake_build -DZLIB_BUILD_EXAMPLES=OFF\n"
        text = hp.draft_recipe(ZLIB_RECIPE, build_script=script).text
        assert "#   cvc_cmake_build -DZLIB_BUILD_EXAMPLES=OFF" in text
        # Quoted, never lifted into an executable line.
        assert "\n\tcvc_cmake_build" not in text

    def test_banner_says_draft_and_no_pr(self):
        text = hp.draft_recipe(ZLIB_RECIPE).text
        assert "NOT A FINISHED PORT" in text
        assert "never opens a HaikuPorts pull request" in text
        assert "NO BUILD EVIDENCE" in text
        # The draft is for the reader's own haikuporter; upstreaming is their
        # decision, not something cvcpkg schedules or performs.
        assert "your own haikuporter" in text
        assert "entirely your call" in text

    def test_ungrounded_draft_refuses_to_guess_resolvables(self):
        draft = hp.draft_recipe(ZLIB_RECIPE)
        assert draft.grounded is False
        assert "lib:libz" not in draft.text
        assert any("PROVIDES needs one `lib:" in t for t in draft.todos)

    def test_grounded_draft_derives_resolvables(self, tmp_path):
        facts = hp.scan_install_tree(_make_install_tree(tmp_path / "install"))
        draft = hp.draft_recipe(ZLIB_RECIPE, facts=facts)
        assert draft.grounded is True
        assert "lib:libz = 1.3 compat >= 1" in draft.text
        assert "cmd:minigzip" in draft.text
        assert "devel:libz = 1.3 compat >= 1" in draft.text
        assert "zlib == $portVersion base" in draft.text
        assert "NO BUILD EVIDENCE" not in draft.text

    def test_own_name_is_first_in_provides(self):
        facts = hp.InstallFacts(libraries=[hp.LibFact("libz", "1.3", "1")])
        text = hp.draft_recipe(ZLIB_RECIPE, facts=facts).text
        block = text.split('PROVIDES="\n')[1].split('\t"')[0]
        assert block.splitlines()[0].strip().startswith("zlib =")

    def test_build_deps_split_into_prereqs_and_requires(self):
        data = _recipe(
            depends={"build": ["cmake", "ninja"], "runtime": [{"name": "fftw3"}], "host_tools": []}
        )
        text = hp.draft_recipe(data).text
        assert "cmd:cmake" in text and "cmd:ninja" in text
        assert "fftw_devel" in text  # cvcpkg fftw3 -> HaikuPorts sci-libs/fftw
        assert "haiku_devel" in text

    def test_unmapped_dependency_is_reported_not_invented(self):
        data = _recipe(depends={"build": ["cmake"], "runtime": ["totally-made-up"]})
        draft = hp.draft_recipe(data)
        assert "totally-made-up" not in draft.text
        assert any("no known HaikuPorts port" in t for t in draft.todos)

    def test_source_dir_emitted_only_when_it_differs(self):
        assert "SOURCE_DIR" not in hp.draft_recipe(ZLIB_RECIPE).text
        data = _recipe(
            recipe={**ZLIB_RECIPE["recipe"], "name": "cgal", "upstream_version": "6.0.1"},
            source={
                "type": "tarball",
                "url": "https://github.com/CGAL/cgal/releases/download/v6.0.1/CGAL-6.0.1.tar.xz",
                "sha256": "0" * 64,
            },
        )
        assert 'SOURCE_DIR="CGAL-$portVersion"' in hp.draft_recipe(data).text

    def test_mirror_becomes_a_second_source_uri(self):
        data = _recipe(
            source={**ZLIB_RECIPE["source"], "mirror": "https://mirror/zlib-1.3.1.tar.gz"}
        )
        text = hp.draft_recipe(data).text
        assert text.count("zlib-$portVersion.tar.gz") == 2

    def test_patches_are_reported_not_silently_carried(self):
        draft = hp.draft_recipe(_recipe(patches=["netbsd-dirfd.patch"]))
        # cvcpkg patches are -p1 diffs; HaikuPorts wants a git-am patchset.
        assert "PATCHES=" not in draft.text
        assert any("patchset" in t and "netbsd-dirfd.patch" in t for t in draft.todos)

    def test_existing_upstream_port_warns_about_slotting(self):
        draft = hp.draft_recipe(ZLIB_RECIPE)
        assert any("ALREADY CARRIES sys-libs/zlib" in t for t in draft.todos)

    def test_renamed_port_is_flagged(self):
        data = _recipe(recipe={**ZLIB_RECIPE["recipe"], "name": "c-ares"})
        draft = hp.draft_recipe(data)
        assert draft.port == "c_ares"
        assert any("port renamed 'c-ares' -> 'c_ares'" in t for t in draft.todos)


# ── Local lint (HaikuPorts' own machine-checkable rules) ────────


class TestLintDraft:
    def test_draft_never_lints_clean(self):
        # A draft with outstanding TODOs must not look submittable.
        problems = hp.lint_draft(hp.draft_recipe(ZLIB_RECIPE).text, port="zlib")
        assert any("still a draft" in p for p in problems)
        assert any("COPYRIGHT is empty" in p for p in problems)

    def test_no_trailing_whitespace_is_ever_emitted(self):
        # HaikuPorts CI fails on any trailing blank; check the emitter directly.
        for line in hp.draft_recipe(ZLIB_RECIPE).text.split("\n"):
            assert line == line.rstrip(), repr(line)

    def test_trailing_whitespace_is_caught(self):
        assert any("trailing whitespace" in p for p in hp.lint_draft('SUMMARY="A b c" \n'))

    def test_required_fields(self):
        problems = hp.lint_draft('REVISION="1"\n')
        for missing in ("SUMMARY", "DESCRIPTION", "HOMEPAGE", "SOURCE_URI", "ARCHITECTURES"):
            assert any(p.startswith(missing) for p in problems), missing

    def test_summary_rules(self):
        base = (
            'DESCRIPTION="d"\nHOMEPAGE="h"\nREVISION="1"\nSOURCE_URI="u"\nARCHITECTURES="x86_64"\n'
            'COPYRIGHT="1995 Someone"\nLICENSE="MIT"\n'
        )
        assert any(
            "must not end with a full stop" in p
            for p in hp.lint_draft('SUMMARY="A nice thing."\n' + base)
        )
        assert any(
            "must start with a capital" in p
            for p in hp.lint_draft('SUMMARY="a nice thing"\n' + base)
        )
        assert any("three words" in p for p in hp.lint_draft('SUMMARY="Nice thing"\n' + base))
        assert any(
            "must not start with the port name" in p
            for p in hp.lint_draft('SUMMARY="Zlib is a thing"\n' + base, port="zlib")
        )

    def test_copyright_rules(self):
        assert any("e-mail" in p for p in hp.lint_draft('COPYRIGHT="1995 A B <a@b.c>"\n'))
        assert any("'copyright'" in p for p in hp.lint_draft('COPYRIGHT="Copyright 1995 A B"\n'))

    def test_field_order_is_checked(self):
        bad = 'HOMEPAGE="h"\nSUMMARY="A b c"\nDESCRIPTION="d"\nREVISION="1"\nSOURCE_URI="u"\n'
        assert any("order" in p for p in hp.lint_draft(bad))

    def test_line_length_limit_exempts_urls_and_checksums(self):
        url = 'SOURCE_URI="https://example.invalid/' + "a" * 120 + '.tar.gz"'
        assert not any("100-character" in p for p in hp.lint_draft(url + "\n"))
        assert any("100-character" in p for p in hp.lint_draft('MESSAGE="' + "x" * 120 + '"\n'))


# ── CLI (offline; no build, no network, no PR) ──────────────────

click_testing = pytest.importorskip("click.testing")
from click.testing import CliRunner  # noqa: E402

from cvcpkg.cli import cli  # noqa: E402

# These tests assert on ``res.stdout`` and ``res.stderr`` separately, because
# the point of `haiku draft-recipe` is that stdout is only ever the recipe and
# the advice goes to stderr.  Do NOT reach for ``CliRunner(mix_stderr=False)``:
# that argument was REMOVED in click 8.2 (we pin 8.4), where the runner already
# captures the two streams separately — ``.stdout``/``.stderr`` are split and
# ``.output`` is the interleaved pair.


def _recipes_dir(tmp_path: Path, data: dict, name: str = "zlib") -> Path:
    import yaml

    root = tmp_path / "recipes" / name
    root.mkdir(parents=True)
    (root / "recipe.yaml").write_text(yaml.safe_dump(data))
    (root / "build.sh").write_text("#!/usr/bin/env bash\ncvc_cmake_build -DX=OFF\n")
    return root.parent


class TestHaikuCLI:
    def test_draft_to_stdout(self, tmp_path):
        rdir = _recipes_dir(tmp_path, ZLIB_RECIPE)
        res = CliRunner().invoke(
            cli,
            [
                "haiku",
                "draft-recipe",
                str(rdir / "zlib"),
                "--no-default-recipes",
                "--recipes-dir",
                str(rdir),
            ],
        )
        assert res.exit_code == 0, res.stderr
        # stdout is only ever the recipe; advice goes to stderr.
        assert res.stdout.startswith("# ===")
        assert 'SUMMARY="A general-purpose' in res.stdout
        assert "still need a human" in res.stderr

    def test_output_writes_into_a_ports_tree(self, tmp_path):
        rdir = _recipes_dir(tmp_path, ZLIB_RECIPE)
        out = tmp_path / "haikuports"
        res = CliRunner().invoke(
            cli,
            [
                "haiku",
                "draft-recipe",
                str(rdir / "zlib"),
                "--recipes-dir",
                str(rdir),
                "--no-default-recipes",
                "--output",
                str(out),
            ],
        )
        assert res.exit_code == 0, res.stderr
        written = out / "sys-libs" / "zlib" / "zlib-1.3.1.recipe"
        assert written.is_file()
        assert "NOT A FINISHED PORT" in written.read_text()

    def test_dry_run_writes_nothing(self, tmp_path):
        rdir = _recipes_dir(tmp_path, ZLIB_RECIPE)
        out = tmp_path / "haikuports"
        res = CliRunner().invoke(
            cli,
            [
                "haiku",
                "draft-recipe",
                str(rdir / "zlib"),
                "--recipes-dir",
                str(rdir),
                "--no-default-recipes",
                "--output",
                str(out),
                "--dry-run",
            ],
        )
        assert res.exit_code == 0, res.stderr
        assert "would write" in res.stderr
        assert not (out / "sys-libs").exists()

    def test_existing_file_is_not_clobbered(self, tmp_path):
        rdir = _recipes_dir(tmp_path, ZLIB_RECIPE)
        out = tmp_path / "haikuports"
        target = out / "sys-libs" / "zlib" / "zlib-1.3.1.recipe"
        target.parent.mkdir(parents=True)
        target.write_text("hand-written, do not clobber\n")
        args = [
            "haiku",
            "draft-recipe",
            str(rdir / "zlib"),
            "--recipes-dir",
            str(rdir),
            "--no-default-recipes",
            "--output",
            str(out),
        ]
        res = CliRunner().invoke(cli, args)
        assert res.exit_code != 0
        assert target.read_text().startswith("hand-written")
        assert "--force" in res.stderr

    def test_refusal_is_a_clean_error(self, tmp_path):
        data = _recipe(recipe={**ZLIB_RECIPE["recipe"], "name": "click-cp312"})
        rdir = _recipes_dir(tmp_path, data, name="click-cp312")
        res = CliRunner().invoke(
            cli,
            [
                "haiku",
                "draft-recipe",
                str(rdir / "click-cp312"),
                "--recipes-dir",
                str(rdir),
                "--no-default-recipes",
            ],
        )
        assert res.exit_code != 0
        assert "Refusing" in res.stderr

    def test_there_is_no_submit_command(self):
        # Load-bearing: HaikuPorts' PR template opens with "You are not a robot."
        res = CliRunner().invoke(cli, ["haiku", "--help"])
        assert res.exit_code == 0
        assert "submit" not in res.output.lower()
        assert "draft-recipe" in res.output
