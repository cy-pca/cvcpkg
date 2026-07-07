"""AWS CLI (``aws s3``) subprocess backend.

Fallback for environments that have the ``aws`` CLI but not
``boto3`` (e.g. HPC sites with site-managed AWS CLI only).

URI format: ``s3-cli://bucket/key``

Maps to ``aws s3 cp s3://bucket/key -`` for reads and
``aws s3 cp - s3://bucket/key`` for writes.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO, ClassVar

from cvcpkg.storage import ObjectInfo, StorageBackend


def _require_aws() -> str:
    exe = shutil.which("aws")
    if not exe:
        raise FileNotFoundError(
            "aws CLI not found on PATH.  Install the AWS CLI or use "
            "pip install cvcpkg[s3] for the boto3-based backend."
        )
    return exe


def _to_s3_uri(uri: str) -> str:
    """Convert ``s3-cli://bucket/key`` to ``s3://bucket/key``."""
    if uri.startswith("s3-cli://"):
        return "s3://" + uri[len("s3-cli://") :]
    return uri


class S3CliBackend(StorageBackend):
    """Read and write objects via the ``aws s3`` CLI."""

    schemes: ClassVar[tuple[str, ...]] = ("s3-cli",)

    def head(self, uri: str) -> ObjectInfo:
        exe = _require_aws()
        _to_s3_uri(uri)
        try:
            subprocess.run(
                [exe, "s3api", "head-object", "--bucket", "", "--key", ""],
                check=False,
                capture_output=True,
            )
            # Simplified: just return unknown size
            return ObjectInfo(size=-1)
        except Exception:
            return ObjectInfo(size=-1)

    def open(self, uri: str) -> BinaryIO:
        exe = _require_aws()
        s3_uri = _to_s3_uri(uri)
        try:
            result = subprocess.run(
                [exe, "s3", "cp", s3_uri, "-"],
                check=True,
                capture_output=True,
            )
            return io.BytesIO(result.stdout)
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"aws s3 cp failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc

    def supports_range(self, uri: str) -> bool:
        return False

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        exe = _require_aws()
        s3_uri = _to_s3_uri(uri)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(data, tmp)
        try:
            subprocess.run(
                [exe, "s3", "cp", tmp_path, s3_uri],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise OSError(
                f"aws s3 cp put failed for {uri}: {exc.stderr.decode(errors='replace')}"
            ) from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)
