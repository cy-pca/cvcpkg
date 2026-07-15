"""Federated dependency reference parsing (cvcpkg.refs)."""

from __future__ import annotations

import pytest

from cvcpkg.refs import DepRef, parse_dep_ref


class TestParseDepRef:
    @pytest.mark.parametrize(
        "ref,expected",
        [
            ("zlib", DepRef(name="zlib")),
            ("zlib@1.2", DepRef(name="zlib", version="1.2")),
            ("shell/iqi-core", DepRef(name="iqi-core", org="shell")),
            ("shell/iqi-core@^1", DepRef(name="iqi-core", version="^1", org="shell")),
            ("cvc://edge-b.lab/iqi-core", DepRef(name="iqi-core", server="edge-b.lab")),
            (
                "cvc://edge-b.lab/shell/iqi-core@^1",
                DepRef(name="iqi-core", version="^1", org="shell", server="edge-b.lab"),
            ),
            (
                "cvc://edge-b.lab:8420/shell/x",
                DepRef(name="x", org="shell", server="edge-b.lab:8420"),
            ),
            ("  zlib  ", DepRef(name="zlib")),
        ],
    )
    def test_string_forms(self, ref, expected):
        assert parse_dep_ref(ref) == expected

    def test_dict_form(self):
        d = {"name": "iqi-core", "version": "^1", "org": "shell", "server": "edge-b.lab"}
        assert parse_dep_ref(d) == DepRef(
            name="iqi-core", version="^1", org="shell", server="edge-b.lab"
        )

    def test_dict_strips_scheme_from_server(self):
        d = {"name": "x", "server": "https://edge-b.lab:8420/"}
        assert parse_dep_ref(d).server == "edge-b.lab:8420"

    def test_depref_passthrough(self):
        r = DepRef(name="z", server="edge-b.lab")
        assert parse_dep_ref(r) is r

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "a/b/c",  # too many segments
            "cvc://",  # no host
            "cvc://host-only",  # no package path
            {"version": "1"},  # dict without name
            12345,  # not str/dict
        ],
    )
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_dep_ref(bad)

    def test_dotted_org_without_scheme_is_rejected(self):
        # A dotted first segment is a host typed without cvc:// — must not be
        # silently accepted as an org (that would defeat the allowlist).
        with pytest.raises(ValueError, match="looks like a host"):
            parse_dep_ref("edge-b.lab/iqi-core")

    @pytest.mark.parametrize(
        "ref",
        [
            "zlib",
            "zlib@1.2",
            "shell/iqi-core@^1",
            "cvc://edge-b.lab/iqi-core",
            "cvc://edge-b.lab/shell/iqi-core@^1",
        ],
    )
    def test_to_uri_roundtrip(self, ref):
        assert parse_dep_ref(parse_dep_ref(ref).to_uri()) == parse_dep_ref(ref)

    def test_qualified_name(self):
        assert parse_dep_ref("shell/iqi").qualified_name == "shell/iqi"
        assert parse_dep_ref("iqi").qualified_name == "iqi"
