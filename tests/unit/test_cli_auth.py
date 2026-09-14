# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Pure CLI-auth policy: loopback matcher, PKCE, challenge validation."""

from __future__ import annotations

import base64
import hashlib

import pytest

# cli_auth.py lives under cvcpkg.server and imports the DB rows + auth -> models
# -> pydantic, so it needs the server extras even for these pure-policy checks.
pytest.importorskip("pydantic", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="server extras not installed")

from cvcpkg.server import cli_auth


@pytest.mark.parametrize(
    "uri,ok",
    [
        ("http://127.0.0.1:49812/callback", True),
        ("http://[::1]:49812/callback", True),
        ("http://127.0.0.1:1024/callback", True),
        # localhost is DNS-resolvable and therefore steerable — refused.
        ("http://localhost:49812/callback", False),
        # https loopback is not how native apps do it (and we require http here).
        ("https://127.0.0.1:49812/callback", False),
        # a query string, wrong path, or sub-1024 port are all refused.
        ("http://127.0.0.1:49812/callback?x=1", False),
        ("http://127.0.0.1:49812/other", False),
        ("http://127.0.0.1:80/callback", False),
        ("http://8.8.8.8:49812/callback", False),
        ("not a uri", False),
    ],
)
def test_loopback_matcher(uri, ok):
    assert cli_auth.loopback_redirect_ok(uri) is ok


def test_pkce_s256():
    verifier = "the-verifier-value-used-by-the-client-1234567890"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert cli_auth.verify_pkce_s256(verifier, challenge)
    assert not cli_auth.verify_pkce_s256("wrong", challenge)
    assert not cli_auth.verify_pkce_s256(verifier, "")
    assert not cli_auth.verify_pkce_s256("", challenge)


def test_valid_code_challenge():
    good = base64.urlsafe_b64encode(hashlib.sha256(b"x").digest()).rstrip(b"=").decode()
    assert len(good) == 43
    assert cli_auth.valid_code_challenge(good)
    assert not cli_auth.valid_code_challenge("too-short")
    assert not cli_auth.valid_code_challenge("!" * 43)


def test_cli_clients_is_locked_down():
    assert set(cli_auth.CLI_CLIENTS) == {"cvcpkg-cli"}
    assert cli_auth.CLI_CLIENTS["cvcpkg-cli"]["public"] is True
