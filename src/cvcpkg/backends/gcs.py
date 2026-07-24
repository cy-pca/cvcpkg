# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Google Cloud Storage backend (requires ``google-cloud-storage``).

Install: ``pip install cvcpkg[gcs]``

URI format: ``gs://bucket/key``
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import BinaryIO, ClassVar

from cvcpkg.storage import ObjectInfo, StorageBackend


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def _get_client():
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is required for the GCS backend. "
            "Install it with: pip install cvcpkg[gcs]"
        ) from exc
    return storage.Client()


class GcsBackend(StorageBackend):
    """Read and write objects on Google Cloud Storage."""

    schemes: ClassVar[tuple[str, ...]] = ("gs",)

    def head(self, uri: str) -> ObjectInfo:
        bucket_name, key = _parse_gs_uri(uri)
        client = _get_client()
        blob = client.bucket(bucket_name).blob(key)
        blob.reload()
        return ObjectInfo(
            size=blob.size or -1,
            etag=blob.etag or "",
            content_type=blob.content_type or "",
        )

    def open(self, uri: str) -> BinaryIO:
        bucket_name, key = _parse_gs_uri(uri)
        client = _get_client()
        blob = client.bucket(bucket_name).blob(key)
        buf = io.BytesIO()
        blob.download_to_file(buf)
        buf.seek(0)
        return buf

    def supports_range(self, uri: str) -> bool:
        return True

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        bucket_name, key = _parse_gs_uri(uri)
        client = _get_client()
        blob = client.bucket(bucket_name).blob(key)
        blob.upload_from_file(data, size=size if size >= 0 else None)

    def list(self, uri: str) -> Iterable[str]:
        bucket_name, prefix = _parse_gs_uri(uri)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        client = _get_client()
        blobs = client.list_blobs(bucket_name, prefix=prefix, delimiter="/")
        for blob in blobs:
            yield blob.name.removeprefix(prefix)
