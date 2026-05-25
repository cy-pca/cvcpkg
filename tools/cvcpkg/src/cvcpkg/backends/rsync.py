"""rsync subprocess backend.

Requires ``rsync`` on PATH.  Best for intra-cluster mirrors
where rsync is the standard transfer tool.

URI format: ``rsync://host/path`` or ``rsync://host::module/path``
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


def _require_rsync() -> str:
    exe = shutil.which("rsync")
    if not exe:
        raise FileNotFoundError(
            "rsync not found on PATH.  Install rsync to use the rsync:// backend."
        )
    return exe


class RsyncBackend(StorageBackend):
    """Fetch objects via ``rsync``.

    Uses ``rsync --inplace --partial`` for efficient transfers.
    """

    schemes: ClassVar[tuple[str, ...]] = ("rsync",)

    def head(self, uri: str) -> ObjectInfo:
        # rsync doesn't have a great HEAD equivalent; we return unknown size
        return ObjectInfo(size=-1)

    def open(self, uri: str) -> BinaryIO:
        exe = _require_rsync()
        # rsync to stdout: rsync <uri> -
        # Alternatively, rsync to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".download") as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run(
                [exe, "--inplace", "--partial", "--progress", uri, tmp_path],
                check=True,
                capture_output=True,
            )
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            return io.BytesIO(data)
        except subprocess.CalledProcessError as exc:
            Path(tmp_path).unlink(missing_ok=True)
            raise OSError(f"rsync failed for {uri}: {exc.stderr.decode(errors='replace')}") from exc

    def supports_range(self, uri: str) -> bool:
        return False  # rsync handles its own delta-transfer

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        exe = _require_rsync()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(data, tmp)

        try:
            subprocess.run(
                [exe, "--inplace", "--partial", tmp_path, uri],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"rsync put failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def list(self, uri: str) -> Iterable[str]:
        exe = _require_rsync()
        try:
            result = subprocess.run(
                [exe, "--list-only", uri],
                check=True,
                capture_output=True,
                text=True,
            )
            entries = []
            for line in result.stdout.strip().splitlines():
                # rsync --list-only format: "drwxr-xr-x       4,096 2024/01/01 12:00:00 dirname"
                parts = line.split(None, 4)
                if len(parts) >= 5:
                    entries.append(parts[4])
            return entries
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"rsync list failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc
