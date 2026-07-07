"""Azure Blob Storage backend (requires ``azure-storage-blob``).

Install: ``pip install cvcpkg[azure]``

URI format: ``azblob://container/blob-path``

Honors ``AZURE_STORAGE_CONNECTION_STRING`` or the default
Azure credential chain.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable
from typing import BinaryIO, ClassVar
from urllib.parse import urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend


def _parse_azblob_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    container = parsed.netloc
    blob_path = parsed.path.lstrip("/")
    return container, blob_path


def _get_client(container: str):
    try:
        from azure.storage.blob import ContainerClient
    except ImportError as exc:
        raise ImportError(
            "azure-storage-blob is required for the Azure backend. "
            "Install it with: pip install cvcpkg[azure]"
        ) from exc

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        return ContainerClient.from_connection_string(conn_str, container)

    # Fall back to DefaultAzureCredential
    from azure.identity import DefaultAzureCredential

    account_url = os.environ.get(
        "AZURE_STORAGE_ACCOUNT_URL",
        "https://<account>.blob.core.windows.net",
    )
    credential = DefaultAzureCredential()
    return ContainerClient(account_url, container, credential=credential)


class AzureBlobBackend(StorageBackend):
    """Read and write objects on Azure Blob Storage."""

    schemes: ClassVar[tuple[str, ...]] = ("azblob",)

    def head(self, uri: str) -> ObjectInfo:
        container, blob_path = _parse_azblob_uri(uri)
        client = _get_client(container)
        props = client.get_blob_client(blob_path).get_blob_properties()
        return ObjectInfo(
            size=props.size or -1,
            etag=props.etag or "",
            content_type=props.content_settings.content_type or "",
        )

    def open(self, uri: str) -> BinaryIO:
        container, blob_path = _parse_azblob_uri(uri)
        client = _get_client(container)
        stream = client.get_blob_client(blob_path).download_blob()
        buf = io.BytesIO()
        stream.readinto(buf)
        buf.seek(0)
        return buf

    def supports_range(self, uri: str) -> bool:
        return True

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        container, blob_path = _parse_azblob_uri(uri)
        client = _get_client(container)
        kwargs = {}
        if size >= 0:
            kwargs["length"] = size
        client.get_blob_client(blob_path).upload_blob(data, overwrite=True, **kwargs)

    def list(self, uri: str) -> Iterable[str]:
        container, prefix = _parse_azblob_uri(uri)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        client = _get_client(container)
        for blob in client.list_blobs(name_starts_with=prefix):
            yield blob.name.removeprefix(prefix)
