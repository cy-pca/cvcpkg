"""`cvcpkg yank` / `cvcpkg unyank` -- driving the server's yank endpoints.

The endpoints existed with nothing in the CLI calling them, so retiring a bad
bundle meant hand-writing curl.  That is easy to get wrong in the direction
that matters: the scope query params are optional and an omitted one means
"every variant of this version", so a slightly-off command silently yanks more
than intended, and a non-matching one reports success having changed nothing.

These tests pin the parts a reviewer cannot eyeball:
  * scope flags are tri-state (absent = unscoped) -- NOT the shared
    --platform/--config/--link defaults of auto/release/shared
  * the preview filter matches the server's filter exactly
  * a no-match refuses instead of POSTing a silent no-op
"""

from __future__ import annotations

from unittest import mock

from click.testing import CliRunner

from cvcpkg.cli import _publish
from cvcpkg.cli._publish import _matching_bundles, _orphaned_variants, nuke, unyank, yank

# The real readline shape from the dev catalog: cvc.1 is the broken bundle that
# a libpq build installed instead of cvc.2 (libreadline.so with no libtinfo).
_READLINE = [
    {
        "version": "8.3+cvc.1",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "yanked": False,
    },
    {
        "version": "8.3+cvc.2",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "yanked": False,
    },
    {
        "version": "8.3+cvc.2",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "static",
        "yanked": False,
    },
    {
        "version": "8.3+cvc.2",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "debug",
        "link": "shared",
        "yanked": False,
    },
    {
        "version": "8.3+cvc.2",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "debug",
        "link": "static",
        "yanked": False,
    },
]

_ARGS = ["--server", "http://test", "--token", "t"]


def _api(listing, capture):
    """Fake _api_request: GET returns the listing, POST records and answers."""

    def _fn(method, url, token, **kwargs):
        capture.append((method, url, kwargs.get("params", {})))
        if method == "get":
            return {"total": len(listing), "packages": listing}
        return {"message": "ok", "count": 1}

    return _fn


class TestScopeIsTriState:
    def test_omitted_scope_sends_no_params_and_means_every_variant(self):
        # The trap: the shared _platform_opt/_config_opt/_link_opt default to
        # auto/release/shared.  If yank reused them it would quietly scope to
        # one variant while the operator asked for the whole version.
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(yank, ["readline", "8.3+cvc.2", *_ARGS, "--yes"])
        assert res.exit_code == 0, res.output
        post = [c for c in calls if c[0] == "post"][0]
        assert post[2] == {}, f"unscoped yank must send no scope params, got {post[2]}"

    def test_supplied_scope_is_forwarded_verbatim(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(
                yank,
                [
                    "readline",
                    "8.3+cvc.1",
                    *_ARGS,
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                    "--config",
                    "release",
                    "--link",
                    "shared",
                    "--yes",
                ],
            )
        assert res.exit_code == 0, res.output
        post = [c for c in calls if c[0] == "post"][0]
        assert post[2] == {
            "platform": "linux",
            "arch": "x86_64",
            "link": "shared",
            "build_type": "release",
        }
        assert post[1].endswith("/v1/packages/readline/8.3+cvc.1/yank")


class TestPreviewMatchesTheServer:
    # _matching_bundles must mirror app.py's yank filter and db_stores.yank:
    # exact version match + equality on each supplied scope field.  If it
    # drifts, the preview lies about what is about to be yanked.
    def test_unscoped_matches_every_variant_of_that_version_only(self):
        got = _matching_bundles(_READLINE, "8.3+cvc.2", None, None, None, None)
        assert len(got) == 4
        assert all(b["version"] == "8.3+cvc.2" for b in got)

    def test_each_scope_field_narrows(self):
        got = _matching_bundles(_READLINE, "8.3+cvc.2", "linux", "x86_64", "release", "shared")
        assert len(got) == 1
        assert got[0]["link"] == "shared" and got[0]["build_type"] == "release"

    def test_scope_that_matches_nothing_yields_nothing(self):
        assert _matching_bundles(_READLINE, "8.3+cvc.2", "windows", None, None, None) == []

    def test_version_is_matched_exactly_not_by_prefix(self):
        # "8.3+cvc.1" must not match "8.3+cvc.10"; the server compares equality.
        bundles = _READLINE + [
            {
                "version": "8.3+cvc.10",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "yanked": False,
            },
        ]
        got = _matching_bundles(bundles, "8.3+cvc.1", None, None, None, None)
        assert [b["version"] for b in got] == ["8.3+cvc.1"]


class TestRefusesSilentNoOps:
    def test_no_matching_bundle_errors_without_posting(self):
        # The endpoint happily returns {"count": 0} for a typo'd version, so the
        # operator thinks they retired something.  Refuse instead.
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(yank, ["readline", "8.3+cvc.99", *_ARGS, "--yes"])
        assert res.exit_code != 0
        assert "no bundle matches" in res.output
        assert "8.3+cvc.1" in res.output, "should list the versions that do exist"
        assert not [c for c in calls if c[0] == "post"], "must not POST a no-op"

    def test_unknown_package_errors(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api([], calls)):
            res = CliRunner().invoke(yank, ["nope", "1.0.0+cvc.1", *_ARGS, "--yes"])
        assert res.exit_code != 0
        assert "no package named" in res.output

    def test_already_yanked_reports_no_change_and_skips_the_post(self):
        listing = [dict(b, yanked=True) for b in _READLINE]
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(listing, calls)):
            res = CliRunner().invoke(yank, ["readline", "8.3+cvc.1", *_ARGS, "--yes"])
        assert res.exit_code == 0, res.output
        assert "nothing to do" in res.output
        assert not [c for c in calls if c[0] == "post"]


class TestConfirmation:
    def test_prompts_and_aborts_on_no(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(yank, ["readline", "8.3+cvc.1", *_ARGS], input="n\n")
        assert res.exit_code != 0
        assert not [c for c in calls if c[0] == "post"], "aborted yank must not POST"

    def test_yes_flag_skips_the_prompt(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(yank, ["readline", "8.3+cvc.1", *_ARGS, "--yes"])
        assert res.exit_code == 0, res.output
        assert [c for c in calls if c[0] == "post"]


class TestOrphanWarning:
    def test_warns_when_the_last_active_bundle_for_a_variant_goes(self):
        # readline 8.3+cvc.2 release/static is the only bundle for that variant.
        target = [
            b
            for b in _READLINE
            if b["version"] == "8.3+cvc.2"
            and b["link"] == "static"
            and b["build_type"] == "release"
        ]
        assert _orphaned_variants(target, _READLINE) == ["linux/x86_64/release/static"]

    def test_no_warning_when_another_version_still_covers_the_variant(self):
        # cvc.1 release/shared is covered by cvc.2 release/shared -- the real
        # readline case, and exactly why yanking it is safe.
        target = [b for b in _READLINE if b["version"] == "8.3+cvc.1"]
        assert _orphaned_variants(target, _READLINE) == []

    def test_already_yanked_bundles_do_not_orphan_anything(self):
        target = [dict(b, yanked=True) for b in _READLINE if b["version"] == "8.3+cvc.2"]
        assert _orphaned_variants(target, _READLINE) == []


class TestUnyank:
    def test_unyank_posts_to_the_unyank_route(self):
        listing = [dict(b, yanked=True) for b in _READLINE]
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(listing, calls)):
            res = CliRunner().invoke(unyank, ["readline", "8.3+cvc.1", *_ARGS, "--yes"])
        assert res.exit_code == 0, res.output
        post = [c for c in calls if c[0] == "post"][0]
        assert post[1].endswith("/v1/packages/readline/8.3+cvc.1/unyank")

    def test_unyank_of_an_active_bundle_is_a_no_op(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_READLINE, calls)):
            res = CliRunner().invoke(unyank, ["readline", "8.3+cvc.1", *_ARGS, "--yes"])
        assert res.exit_code == 0, res.output
        assert "nothing to do" in res.output
        assert not [c for c in calls if c[0] == "post"]

    def test_listing_asks_for_yanked_bundles(self):
        # unyank's targets are yanked, so the listing must include them or the
        # command could never find anything to restore.
        listing = [dict(b, yanked=True) for b in _READLINE]
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(listing, calls)):
            CliRunner().invoke(unyank, ["readline", "8.3+cvc.1", *_ARGS, "--yes"])
        get = [c for c in calls if c[0] == "get"][0]
        assert get[2].get("include_yanked") == "true"


# cvc.1 yanked, cvc.2 active -- the shape nuke is designed for.
_MIXED = [dict(b, yanked=(b["version"] == "8.3+cvc.1")) for b in _READLINE]


class TestNuke:
    # nuke is irreversible (row AND archive bytes), so its gates are stronger
    # than yank's: yanked-first, typed confirmation, no --yes.

    def test_refuses_a_bundle_that_is_not_yanked(self):
        # nuke only accelerates retention; a live bundle must be yanked first.
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(
                nuke, ["readline", "8.3+cvc.2", *_ARGS, "--confirm", "readline==8.3+cvc.2"]
            )
        assert res.exit_code != 0
        assert "not yanked" in res.output
        assert "yank it first" in res.output.lower()
        assert not [c for c in calls if c[0] == "post"], "must not POST for a live bundle"

    def test_typed_confirmation_must_match(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(nuke, ["readline", "8.3+cvc.1", *_ARGS], input="nope\n")
        assert res.exit_code != 0
        assert not [c for c in calls if c[0] == "post"], "wrong confirmation must not POST"

    def test_confirm_flag_must_match_the_target(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(
                nuke, ["readline", "8.3+cvc.1", *_ARGS, "--confirm", "readline==9.9"]
            )
        assert res.exit_code != 0
        assert "does not match" in res.output
        assert not [c for c in calls if c[0] == "post"]

    def test_correct_confirmation_posts_to_nuke_with_scope(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(
                nuke,
                [
                    "readline",
                    "8.3+cvc.1",
                    *_ARGS,
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                    "--config",
                    "release",
                    "--link",
                    "shared",
                    "--confirm",
                    "readline==8.3+cvc.1",
                ],
            )
        assert res.exit_code == 0, res.output
        post = [c for c in calls if c[0] == "post"][0]
        assert post[1].endswith("/v1/packages/readline/8.3+cvc.1/nuke")
        assert post[2] == {
            "platform": "linux",
            "arch": "x86_64",
            "link": "shared",
            "build_type": "release",
        }

    def test_typed_prompt_accepts_the_exact_target(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(
                nuke, ["readline", "8.3+cvc.1", *_ARGS], input="readline==8.3+cvc.1\n"
            )
        assert res.exit_code == 0, res.output
        assert [c for c in calls if c[0] == "post"]

    def test_has_no_yes_flag(self):
        # A muscle-memory -y carried from yank must not destroy archives.
        res = CliRunner().invoke(nuke, ["--help"])
        opts = res.output.split("Options:")[-1]
        assert "-y" not in opts and "--yes" not in opts
        assert "--confirm" in opts

    def test_no_match_refuses_without_posting(self):
        calls: list = []
        with mock.patch.object(_publish, "_api_request", _api(_MIXED, calls)):
            res = CliRunner().invoke(
                nuke, ["readline", "8.3+cvc.99", *_ARGS, "--confirm", "readline==8.3+cvc.99"]
            )
        assert res.exit_code != 0
        assert "no bundle matches" in res.output
        assert not [c for c in calls if c[0] == "post"]


class TestSearchRevealsYanked:
    """`cvcpkg search` used to print yanked and active bundles as identical
    rows, so a user could not tell which to unyank.  The renderer must
    distinguish them and hand back a runnable unyank command.
    """

    _RESP = {
        "total": 2,
        "package_count": 1,
        "packages": [
            {
                "name": "readline",
                "version": "8.3+cvc.2",
                "platform": "linux",
                "arch": "x86_64",
                "link": "shared",
                "build_type": "release",
                "size_bytes": 900000,
                "yanked": False,
                "yanked_at": None,
            },
            {
                "name": "readline",
                "version": "8.3+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "link": "shared",
                "build_type": "release",
                "size_bytes": 910000,
                "yanked": True,
                "yanked_at": "2026-07-17T15:18:16Z",
            },
        ],
    }

    def _run(self, flags):
        import httpx

        from cvcpkg.cli._search import search

        class _Resp:
            status_code = 200

            def json(self):
                return self._RESP

        _Resp._RESP = self._RESP

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return _Resp()

        with mock.patch.object(httpx, "Client", _Client):
            return CliRunner().invoke(
                search, ["readline", "--server", "http://t", "--token", "x", *flags]
            )

    def test_include_yanked_marks_which_row_is_yanked(self):
        res = self._run(["--include-yanked"])
        assert res.exit_code == 0, res.output
        assert "State" in res.output
        # both rows visible, one marked yanked, one active -- not identical
        assert "active" in res.output
        assert "yanked" in res.output

    def test_emits_a_runnable_unyank_command_for_the_yanked_row(self):
        res = self._run(["--include-yanked"])
        assert (
            "cvcpkg unyank readline 8.3+cvc.1 "
            "--platform linux --arch x86_64 --config release --link shared" in res.output
        )

    def test_yanked_only_filters_to_yanked_rows(self):
        res = self._run(["--yanked-only"])
        assert res.exit_code == 0, res.output
        assert "8.3+cvc.1" in res.output  # the yanked one
        assert "8.3+cvc.2" not in res.output  # the active one filtered out
