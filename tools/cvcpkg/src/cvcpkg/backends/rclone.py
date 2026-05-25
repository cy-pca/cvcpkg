"""rclone subprocess backend.

Requires ``rclone`` on PATH.  Supports 70+ remotes (B2,
Dropbox, OneDrive, Swift, WebDAV, IPFS, etc.) via
``rclone cat`` and ``rclone copyto``.

URI format: ``rclone://remote:path``
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, ClassVar

from cvcpkg.storage import ObjectInfo, StorageBackend


def _require_rclone() -> str:
    exe = shutil.which("rclone")
    if not exe:
        raise FileNotFoundError(
            "rclone not found on PATH.  Install rclone to use the rclone:// backend."
        )
    return exe


def _rclone_path(uri: str) -> str:
    """Strip the ``rclone://`` prefix to get the rclone remote:path."""
    if uri.startswith("rclone://"):
        return uri[len("rclone://") :]
    return uri


class RcloneBackend(StorageBackend):
    """Fetch objects via ``rclone``.

    Delegates to whatever remote rclone has configured.
    """

    schemes: ClassVar[tuple[str, ...]] = ("rclone",)

    def head(self, uri: str) -> ObjectInfo:
        exe = _require_rclone()
        remote_path = _rclone_path(uri)
        try:
            result = subprocess.run(
                [exe, "size", "--json", remote_path],
                check=True,
                capture_output=True,
                text=True,
            )
            import json

            info = json.loads(result.stdout)
            return ObjectInfo(size=info.get("bytes", -1))
        except (subprocess.CalledProcessError, Exception):
            return ObjectInfo(size=-1)

    def open(self, uri: str) -> BinaryIO:
        exe = _require_rclone()
        remote_path = _rclone_path(uri)
        try:
            result = subprocess.run(
                [exe, "cat", remote_path],
                check=True,
                capture_output=True,
            )
            return io.BytesIO(result.stdout)
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"rclone cat failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc

    def supports_range(self, uri: str) -> bool:
        return False

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        exe = _require_rclone()
        remote_path = _rclone_path(uri)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(data, tmp)
        try:
            subprocess.run(
                [exe, "copyto", tmp_path, remote_path],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"rclone copyto failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def list(self, uri: str) -> Iterable[str]:
        exe = _require_rclone()
        remote_path = _rclone_path(uri)
        try:
            result = subprocess.run(
                [exe, "lsf", remote_path],
                check=True,
                capture_output=True,
                text=True,
            )
            return [line for line in result.stdout.strip().splitlines() if line]
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"rclone lsf failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc
