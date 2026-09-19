# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the SFTP/SSH backend (``cvcpkg.backends.sftp``).

``paramiko`` is an optional extra and absent on the CI runner, so a fake
``paramiko`` module is injected via ``sys.modules``.  Backend-method tests
patch ``_get_transport`` to hand back a MagicMock transport, and the
transport-construction / ImportError branches drive ``_get_transport``
directly against the fake.
"""

import io
import sys
import types

import pytest
from unittest.mock import MagicMock

from cvcpkg.backends import sftp
from cvcpkg.storage import ObjectInfo


# ── URI parsing ─────────────────────────────────────────────────


class TestParseSftpUri:
    def test_full(self):
        host, port, user, path = sftp._parse_sftp_uri("sftp://joe@host.example:2222/pub/a.tar")
        assert host == "host.example"
        assert port == 2222
        assert user == "joe"
        assert path == "/pub/a.tar"

    def test_defaults(self):
        host, port, user, path = sftp._parse_sftp_uri("sftp://host.example/pub/a.tar")
        assert host == "host.example"
        assert port == 22  # default
        assert user is None
        assert path == "/pub/a.tar"

    def test_percent_encoded_path(self):
        _, _, _, path = sftp._parse_sftp_uri("sftp://h/pub/a%20b.tar")
        assert path == "/pub/a b.tar"

    def test_no_host_raises(self):
        with pytest.raises(ValueError, match="No host in SFTP URI"):
            sftp._parse_sftp_uri("sftp:///pub/a.tar")


# ── Fake paramiko ───────────────────────────────────────────────


class _SSHException(Exception):
    """Stand-in for ``paramiko.SSHException``."""


@pytest.fixture
def fake_paramiko(monkeypatch):
    """Inject a fake ``paramiko`` module and return it for assertions."""
    mod = types.ModuleType("paramiko")
    mod.Transport = MagicMock(name="Transport")
    mod.SFTPClient = MagicMock(name="SFTPClient")
    mod.RSAKey = MagicMock(name="RSAKey")
    mod.Agent = MagicMock(name="Agent")
    mod.SSHException = _SSHException
    monkeypatch.setitem(sys.modules, "paramiko", mod)
    return mod


# ── _get_transport ──────────────────────────────────────────────


class TestGetTransport:
    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "paramiko", None)
        with pytest.raises(ImportError, match="paramiko is required"):
            sftp._get_transport("host", 22, "joe")

    def test_identity_file_branch(self, fake_paramiko, monkeypatch):
        monkeypatch.setenv("CVCPKG_SSH_IDENTITY_FILE", "~/.ssh/id_ed25519_cvc")
        key = fake_paramiko.RSAKey.from_private_key_file.return_value
        transport = fake_paramiko.Transport.return_value

        result = sftp._get_transport("host", 2200, "joe")

        fake_paramiko.Transport.assert_called_once_with(("host", 2200))
        # Path is expanded before being handed to paramiko.
        called_path = fake_paramiko.RSAKey.from_private_key_file.call_args[0][0]
        assert "~" not in called_path
        transport.connect.assert_called_once_with(username="joe", pkey=key)
        # Agent path must not run when an identity file is set.
        fake_paramiko.Agent.assert_not_called()
        assert result is transport

    def test_identity_file_uses_getlogin_when_no_user(self, fake_paramiko, monkeypatch):
        monkeypatch.setenv("CVCPKG_SSH_IDENTITY_FILE", "/keys/id_rsa")
        monkeypatch.setattr(sftp.os, "getlogin", lambda: "loginuser")
        transport = fake_paramiko.Transport.return_value

        sftp._get_transport("host", 22, None)

        _, kwargs = transport.connect.call_args
        assert kwargs["username"] == "loginuser"

    def test_agent_branch_first_key_succeeds(self, fake_paramiko, monkeypatch):
        monkeypatch.delenv("CVCPKG_SSH_IDENTITY_FILE", raising=False)
        transport = fake_paramiko.Transport.return_value
        key_a, key_b = MagicMock(name="keyA"), MagicMock(name="keyB")
        fake_paramiko.Agent.return_value.get_keys.return_value = [key_a, key_b]

        sftp._get_transport("host", 22, "joe")

        transport.connect.assert_called_once_with(username="joe")
        # First key authenticates, so the loop breaks before the second.
        transport.auth_publickey.assert_called_once_with("joe", key_a)

    def test_agent_branch_retries_after_ssh_exception(self, fake_paramiko, monkeypatch):
        monkeypatch.delenv("CVCPKG_SSH_IDENTITY_FILE", raising=False)
        transport = fake_paramiko.Transport.return_value
        key_a, key_b = MagicMock(name="keyA"), MagicMock(name="keyB")
        fake_paramiko.Agent.return_value.get_keys.return_value = [key_a, key_b]
        # First key is rejected, second succeeds.
        transport.auth_publickey.side_effect = [_SSHException("nope"), None]

        sftp._get_transport("host", 22, "joe")

        assert transport.auth_publickey.call_count == 2
        assert transport.auth_publickey.call_args_list[1][0] == ("joe", key_b)

    def test_agent_all_keys_rejected(self, fake_paramiko, monkeypatch):
        monkeypatch.delenv("CVCPKG_SSH_IDENTITY_FILE", raising=False)
        transport = fake_paramiko.Transport.return_value
        key_a, key_b = MagicMock(name="keyA"), MagicMock(name="keyB")
        fake_paramiko.Agent.return_value.get_keys.return_value = [key_a, key_b]
        # Every key is rejected -> loop exhausts without a break.
        transport.auth_publickey.side_effect = _SSHException("nope")

        result = sftp._get_transport("host", 22, "joe")

        assert transport.auth_publickey.call_count == 2
        assert result is transport

    def test_agent_no_keys(self, fake_paramiko, monkeypatch):
        monkeypatch.delenv("CVCPKG_SSH_IDENTITY_FILE", raising=False)
        transport = fake_paramiko.Transport.return_value
        fake_paramiko.Agent.return_value.get_keys.return_value = []

        sftp._get_transport("host", 22, "joe")

        transport.auth_publickey.assert_not_called()

    def test_agent_construction_error_is_swallowed(self, fake_paramiko, monkeypatch):
        monkeypatch.delenv("CVCPKG_SSH_IDENTITY_FILE", raising=False)
        transport = fake_paramiko.Transport.return_value
        fake_paramiko.Agent.side_effect = RuntimeError("no agent socket")

        # Must not raise despite the agent blowing up.
        result = sftp._get_transport("host", 22, "joe")
        assert result is transport


# ── SftpBackend ─────────────────────────────────────────────────


@pytest.fixture
def patched_transport(monkeypatch):
    """Patch ``_get_transport`` so backend methods skip the connection dance."""
    transport = MagicMock(name="transport")
    monkeypatch.setattr(sftp, "_get_transport", lambda host, port, user: transport)
    return transport


class TestSftpBackend:
    def test_schemes_and_supports_range(self):
        assert sftp.SftpBackend.schemes == ("sftp", "ssh")
        assert sftp.SftpBackend().supports_range("sftp://h/p") is False

    def test_head(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        sftp_client.stat.return_value.st_size = 4096

        info = sftp.SftpBackend().head("sftp://h/pub/a.tar")

        assert isinstance(info, ObjectInfo)
        assert info.size == 4096
        sftp_client.stat.assert_called_once_with("/pub/a.tar")
        patched_transport.close.assert_called_once()

    def test_head_zero_size_becomes_unknown(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        sftp_client.stat.return_value.st_size = 0

        info = sftp.SftpBackend().head("sftp://h/empty")
        assert info.size == -1

    def test_head_closes_transport_on_error(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        sftp_client.stat.side_effect = OSError("stat failed")

        with pytest.raises(OSError, match="stat failed"):
            sftp.SftpBackend().head("sftp://h/x")
        # finally: must still close the transport.
        patched_transport.close.assert_called_once()

    def test_open(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        sftp_client.open.return_value.read.return_value = b"sftp-bytes"

        result = sftp.SftpBackend().open("sftp://h/pub/a.tar")

        assert result.read() == b"sftp-bytes"
        sftp_client.open.assert_called_once_with("/pub/a.tar", "rb")
        patched_transport.close.assert_called_once()

    def test_put(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        remote_file = MagicMock(name="remote_file")
        # ``with sftp.open(...) as f`` -> context manager.
        sftp_client.open.return_value.__enter__.return_value = remote_file

        payload = b"x" * (1 << 16) + b"tail"  # forces two read() chunks
        sftp.SftpBackend().put("sftp://h/pub/a.tar", io.BytesIO(payload))

        sftp_client.open.assert_called_once_with("/pub/a.tar", "wb")
        written = b"".join(c.args[0] for c in remote_file.write.call_args_list)
        assert written == payload
        patched_transport.close.assert_called_once()

    def test_list_returns_sorted(self, fake_paramiko, patched_transport):
        sftp_client = fake_paramiko.SFTPClient.from_transport.return_value
        sftp_client.listdir.return_value = ["c", "a", "b"]

        result = sftp.SftpBackend().list("sftp://h/pub")

        assert result == ["a", "b", "c"]
        sftp_client.listdir.assert_called_once_with("/pub")
        patched_transport.close.assert_called_once()


# ── Registry dispatch ───────────────────────────────────────────


class TestDispatch:
    def test_get_backend_sftp(self):
        from cvcpkg.storage import get_backend

        backend = get_backend("sftp://host/path")
        assert "sftp" in backend.schemes
        assert "ssh" in backend.schemes

    def test_get_backend_ssh(self):
        from cvcpkg.storage import get_backend

        assert "ssh" in get_backend("ssh://host/path").schemes
