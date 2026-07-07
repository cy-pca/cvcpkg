"""SFTP / SSH storage backend (requires ``paramiko``).

Install: ``pip install cvcpkg[sftp]`` or ``pip install paramiko``

Honors ``~/.ssh/config``, SSH agent keys, and identity files.

URI format: ``sftp://[user@]host[:port]/path``

Environment variables::

    CVCPKG_SSH_IDENTITY_FILE=~/.ssh/id_ed25519_cvc
    CVCPKG_SSH_KNOWN_HOSTS=~/.ssh/known_hosts
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable
from typing import BinaryIO, ClassVar
from urllib.parse import unquote, urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend


def _parse_sftp_uri(uri: str) -> tuple[str, int, str | None, str]:
    """Parse ``sftp://[user@]host[:port]/path`` → (host, port, user, path)."""
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    port = parsed.port or 22
    user = parsed.username
    path = unquote(parsed.path)
    if not host:
        raise ValueError(f"No host in SFTP URI: {uri}")
    return host, port, user, path


def _get_transport(host: str, port: int, user: str | None):
    """Create a paramiko Transport with reasonable defaults."""
    try:
        import paramiko
    except ImportError as exc:
        raise ImportError(
            "paramiko is required for the SFTP backend. Install it with: pip install cvcpkg[sftp]"
        ) from exc

    transport = paramiko.Transport((host, port))

    # Try SSH agent first, then explicit key, then password
    identity = os.environ.get("CVCPKG_SSH_IDENTITY_FILE")
    if identity:
        key = paramiko.RSAKey.from_private_key_file(os.path.expanduser(identity))
        transport.connect(username=user or os.getlogin(), pkey=key)
    else:
        # Use agent
        transport.connect(username=user or os.getlogin())
        # Try to authenticate via agent
        try:
            agent = paramiko.Agent()
            agent_keys = agent.get_keys()
            if agent_keys:
                for key in agent_keys:
                    try:
                        transport.auth_publickey(user or os.getlogin(), key)
                        break
                    except paramiko.SSHException:
                        continue
        except Exception:
            pass

    return transport


class SftpBackend(StorageBackend):
    """Read and write objects over SFTP/SSH."""

    schemes: ClassVar[tuple[str, ...]] = ("sftp", "ssh")

    def head(self, uri: str) -> ObjectInfo:
        host, port, user, path = _parse_sftp_uri(uri)
        import paramiko

        transport = _get_transport(host, port, user)
        try:
            sftp = paramiko.SFTPClient.from_transport(transport)
            stat = sftp.stat(path)
            return ObjectInfo(size=stat.st_size if stat.st_size else -1)
        finally:
            transport.close()

    def open(self, uri: str) -> BinaryIO:
        host, port, user, path = _parse_sftp_uri(uri)
        import paramiko

        transport = _get_transport(host, port, user)
        sftp = paramiko.SFTPClient.from_transport(transport)
        # Read fully — we can't keep the transport open across calls
        data = sftp.open(path, "rb").read()
        transport.close()
        return io.BytesIO(data)

    def supports_range(self, uri: str) -> bool:
        return False

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        host, port, user, path = _parse_sftp_uri(uri)
        import paramiko

        transport = _get_transport(host, port, user)
        try:
            sftp = paramiko.SFTPClient.from_transport(transport)
            with sftp.open(path, "wb") as f:
                while True:
                    chunk = data.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            transport.close()

    def list(self, uri: str) -> Iterable[str]:
        host, port, user, path = _parse_sftp_uri(uri)
        import paramiko

        transport = _get_transport(host, port, user)
        try:
            sftp = paramiko.SFTPClient.from_transport(transport)
            return sorted(sftp.listdir(path))
        finally:
            transport.close()
