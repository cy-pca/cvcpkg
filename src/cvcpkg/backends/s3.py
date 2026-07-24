# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""AWS S3 storage backend (requires ``boto3``).

Install: ``pip install cvcpkg[s3]`` or ``pip install boto3``

Works with any S3-compatible store (MinIO, Ceph RGW, Wasabi,
Backblaze B2 via S3 API).  Honors the standard AWS credential
chain: ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
``AWS_PROFILE``, ``~/.aws/credentials``, instance metadata, etc.

URI format: ``s3://bucket/key``

Environment variables for non-AWS endpoints::

    CVCPKG_S3_ENDPOINT_URL=https://minio.lab.example.com:9000
    CVCPKG_S3_REGION=us-east-1
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import BinaryIO, ClassVar
from urllib.parse import urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` → (bucket, key)."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Expected s3:// URI, got: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket:
        raise ValueError(f"No bucket in URI: {uri}")
    return bucket, key


def _get_client():
    """Create a boto3 S3 client, honoring custom endpoint."""
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "boto3 is required for the S3 backend. Install it with: pip install cvcpkg[s3]"
        ) from exc

    kwargs = {}
    endpoint = os.environ.get("CVCPKG_S3_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = os.environ.get("CVCPKG_S3_REGION")
    if region:
        kwargs["region_name"] = region
    return boto3.client("s3", **kwargs)


class S3Backend(StorageBackend):
    """Read and write objects on S3-compatible storage."""

    schemes: ClassVar[tuple[str, ...]] = ("s3",)

    def head(self, uri: str) -> ObjectInfo:
        bucket, key = _parse_s3_uri(uri)
        client = _get_client()
        resp = client.head_object(Bucket=bucket, Key=key)
        return ObjectInfo(
            size=resp.get("ContentLength", -1),
            etag=resp.get("ETag", "").strip('"'),
            content_type=resp.get("ContentType", ""),
        )

    def open(self, uri: str) -> BinaryIO:
        bucket, key = _parse_s3_uri(uri)
        client = _get_client()
        resp = client.get_object(Bucket=bucket, Key=key)
        return resp["Body"]

    def supports_range(self, uri: str) -> bool:
        return True

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        bucket, key = _parse_s3_uri(uri)
        client = _get_client()
        kwargs: dict = {"Bucket": bucket, "Key": key, "Body": data}
        if size >= 0:
            kwargs["ContentLength"] = size
        client.put_object(**kwargs)

    def list(self, uri: str) -> Iterable[str]:
        bucket, key = _parse_s3_uri(uri)
        if key and not key.endswith("/"):
            key += "/"
        client = _get_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=key, Delimiter="/"):
            for obj in page.get("Contents", []):
                yield obj["Key"].removeprefix(key)
            for prefix in page.get("CommonPrefixes", []):
                yield prefix["Prefix"].removeprefix(key)
