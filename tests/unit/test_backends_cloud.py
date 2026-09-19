# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the SDK-backed cloud backends: azure (azure-storage-blob),
gcs (google-cloud-storage) and s3 (boto3).

All three SDKs are optional extras and are *absent* on the CI runner, so the
happy-path tests patch each module's ``_get_client`` factory to hand back a
MagicMock, and the client-construction / ImportError branches inject or force
the SDK modules through ``sys.modules``.
"""

import io
import sys
import types

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from cvcpkg.backends import azure, gcs, s3


# ════════════════════════════════════════════════════════════════
#  Azure Blob
# ════════════════════════════════════════════════════════════════


class TestAzureParse:
    def test_parse(self):
        container, blob = azure._parse_azblob_uri("azblob://mycontainer/path/to/blob.bin")
        assert container == "mycontainer"
        assert blob == "path/to/blob.bin"

    def test_parse_bare_container(self):
        container, blob = azure._parse_azblob_uri("azblob://c")
        assert container == "c"
        assert blob == ""


@pytest.fixture
def fake_azure(monkeypatch):
    """Install fake ``azure.storage.blob`` and ``azure.identity`` modules."""
    azure_pkg = types.ModuleType("azure")
    storage_pkg = types.ModuleType("azure.storage")
    blob_mod = types.ModuleType("azure.storage.blob")
    identity_mod = types.ModuleType("azure.identity")

    container_client = MagicMock(name="ContainerClient")
    blob_mod.ContainerClient = container_client
    default_cred = MagicMock(name="DefaultAzureCredential")
    identity_mod.DefaultAzureCredential = default_cred

    azure_pkg.storage = storage_pkg
    storage_pkg.blob = blob_mod
    azure_pkg.identity = identity_mod

    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.storage", storage_pkg)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    return SimpleNamespace(
        ContainerClient=container_client, DefaultAzureCredential=default_cred
    )


class TestAzureGetClient:
    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "azure.storage.blob", None)
        with pytest.raises(ImportError, match="azure-storage-blob is required"):
            azure._get_client("c")

    def test_connection_string_branch(self, fake_azure, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=...")
        result = azure._get_client("mycontainer")
        fake_azure.ContainerClient.from_connection_string.assert_called_once_with(
            "DefaultEndpointsProtocol=...", "mycontainer"
        )
        assert result is fake_azure.ContainerClient.from_connection_string.return_value
        fake_azure.DefaultAzureCredential.assert_not_called()

    def test_default_credential_branch_with_account_url(self, fake_azure, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net")
        result = azure._get_client("cont")
        fake_azure.DefaultAzureCredential.assert_called_once_with()
        fake_azure.ContainerClient.assert_called_once_with(
            "https://acct.blob.core.windows.net",
            "cont",
            credential=fake_azure.DefaultAzureCredential.return_value,
        )
        assert result is fake_azure.ContainerClient.return_value

    def test_default_credential_branch_default_url(self, fake_azure, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
        azure._get_client("cont")
        args, kwargs = fake_azure.ContainerClient.call_args
        assert args[0] == "https://<account>.blob.core.windows.net"


class TestAzureBackend:
    def test_schemes_and_supports_range(self):
        assert azure.AzureBlobBackend.schemes == ("azblob",)
        assert azure.AzureBlobBackend().supports_range("azblob://c/b") is True

    def test_head(self, monkeypatch):
        client = MagicMock()
        props = client.get_blob_client.return_value.get_blob_properties.return_value
        props.size = 2048
        props.etag = "etag123"
        props.content_settings.content_type = "application/gzip"
        monkeypatch.setattr(azure, "_get_client", lambda container: client)

        info = azure.AzureBlobBackend().head("azblob://c/blob/path")
        assert info.size == 2048
        assert info.etag == "etag123"
        assert info.content_type == "application/gzip"
        client.get_blob_client.assert_called_once_with("blob/path")

    def test_head_falsy_fields(self, monkeypatch):
        client = MagicMock()
        props = client.get_blob_client.return_value.get_blob_properties.return_value
        props.size = 0
        props.etag = None
        props.content_settings.content_type = None
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        info = azure.AzureBlobBackend().head("azblob://c/b")
        assert info.size == -1
        assert info.etag == ""
        assert info.content_type == ""

    def test_open(self, monkeypatch):
        client = MagicMock()
        stream = client.get_blob_client.return_value.download_blob.return_value
        stream.readinto.side_effect = lambda buf: buf.write(b"azuredata")
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        result = azure.AzureBlobBackend().open("azblob://c/blob")
        assert result.read() == b"azuredata"

    def test_put_with_size(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        data = io.BytesIO(b"x")
        azure.AzureBlobBackend().put("azblob://c/blob", data, size=5)
        client.get_blob_client.return_value.upload_blob.assert_called_once_with(
            data, overwrite=True, length=5
        )

    def test_put_without_size(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        data = io.BytesIO(b"x")
        azure.AzureBlobBackend().put("azblob://c/blob", data)
        client.get_blob_client.return_value.upload_blob.assert_called_once_with(
            data, overwrite=True
        )

    def test_list_adds_trailing_slash_and_strips_prefix(self, monkeypatch):
        client = MagicMock()
        client.list_blobs.return_value = [
            SimpleNamespace(name="pre/a"),
            SimpleNamespace(name="pre/b"),
        ]
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        result = list(azure.AzureBlobBackend().list("azblob://c/pre"))
        assert result == ["a", "b"]
        client.list_blobs.assert_called_once_with(name_starts_with="pre/")

    def test_list_empty_prefix(self, monkeypatch):
        client = MagicMock()
        client.list_blobs.return_value = [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
        monkeypatch.setattr(azure, "_get_client", lambda container: client)
        result = list(azure.AzureBlobBackend().list("azblob://c"))
        assert result == ["a", "b"]
        client.list_blobs.assert_called_once_with(name_starts_with="")


# ════════════════════════════════════════════════════════════════
#  Google Cloud Storage
# ════════════════════════════════════════════════════════════════


class TestGcsParse:
    def test_parse(self):
        bucket, key = gcs._parse_gs_uri("gs://mybucket/path/to/key")
        assert bucket == "mybucket"
        assert key == "path/to/key"


@pytest.fixture
def fake_gcs(monkeypatch):
    """Install a fake ``google.cloud`` package exposing ``storage``."""
    google_pkg = types.ModuleType("google")
    cloud_pkg = types.ModuleType("google.cloud")
    storage_mod = MagicMock(name="storage")
    cloud_pkg.storage = storage_mod
    google_pkg.cloud = cloud_pkg
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_pkg)
    return storage_mod


class TestGcsGetClient:
    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "google.cloud", None)
        with pytest.raises(ImportError, match="google-cloud-storage is required"):
            gcs._get_client()

    def test_success(self, fake_gcs):
        result = gcs._get_client()
        fake_gcs.Client.assert_called_once_with()
        assert result is fake_gcs.Client.return_value


class TestGcsBackend:
    def test_schemes_and_supports_range(self):
        assert gcs.GcsBackend.schemes == ("gs",)
        assert gcs.GcsBackend().supports_range("gs://b/k") is True

    def test_head(self, monkeypatch):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        blob.size = 1000
        blob.etag = "etag-x"
        blob.content_type = "application/x-tar"
        monkeypatch.setattr(gcs, "_get_client", lambda: client)

        info = gcs.GcsBackend().head("gs://mybucket/path/key")
        assert info.size == 1000
        assert info.etag == "etag-x"
        assert info.content_type == "application/x-tar"
        client.bucket.assert_called_once_with("mybucket")
        client.bucket.return_value.blob.assert_called_once_with("path/key")
        blob.reload.assert_called_once()

    def test_head_falsy_fields(self, monkeypatch):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        blob.size = 0
        blob.etag = None
        blob.content_type = None
        monkeypatch.setattr(gcs, "_get_client", lambda: client)
        info = gcs.GcsBackend().head("gs://b/k")
        assert info.size == -1
        assert info.etag == ""
        assert info.content_type == ""

    def test_open(self, monkeypatch):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        blob.download_to_file.side_effect = lambda buf: buf.write(b"gcsbytes")
        monkeypatch.setattr(gcs, "_get_client", lambda: client)
        result = gcs.GcsBackend().open("gs://b/k")
        assert result.read() == b"gcsbytes"

    def test_put_with_size(self, monkeypatch):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        monkeypatch.setattr(gcs, "_get_client", lambda: client)
        data = io.BytesIO(b"x")
        gcs.GcsBackend().put("gs://b/k", data, size=10)
        blob.upload_from_file.assert_called_once_with(data, size=10)

    def test_put_without_size(self, monkeypatch):
        client = MagicMock()
        blob = client.bucket.return_value.blob.return_value
        monkeypatch.setattr(gcs, "_get_client", lambda: client)
        data = io.BytesIO(b"x")
        gcs.GcsBackend().put("gs://b/k", data)
        blob.upload_from_file.assert_called_once_with(data, size=None)

    def test_list(self, monkeypatch):
        client = MagicMock()
        client.list_blobs.return_value = [
            SimpleNamespace(name="pre/a"),
            SimpleNamespace(name="pre/b"),
        ]
        monkeypatch.setattr(gcs, "_get_client", lambda: client)
        result = list(gcs.GcsBackend().list("gs://b/pre"))
        assert result == ["a", "b"]
        client.list_blobs.assert_called_once_with("b", prefix="pre/", delimiter="/")


# ════════════════════════════════════════════════════════════════
#  AWS S3 (boto3)
# ════════════════════════════════════════════════════════════════


class TestS3Parse:
    def test_parse(self):
        bucket, key = s3._parse_s3_uri("s3://bucket/path/key")
        assert bucket == "bucket"
        assert key == "path/key"

    def test_wrong_scheme(self):
        with pytest.raises(ValueError, match="Expected s3"):
            s3._parse_s3_uri("http://host/key")

    def test_no_bucket(self):
        with pytest.raises(ValueError, match="No bucket"):
            s3._parse_s3_uri("s3:///key-only")


@pytest.fixture
def fake_boto3(monkeypatch):
    boto3 = MagicMock(name="boto3")
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    return boto3


class TestS3GetClient:
    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)
        with pytest.raises(ImportError, match="boto3 is required"):
            s3._get_client()

    def test_plain(self, fake_boto3, monkeypatch):
        monkeypatch.delenv("CVCPKG_S3_ENDPOINT_URL", raising=False)
        monkeypatch.delenv("CVCPKG_S3_REGION", raising=False)
        result = s3._get_client()
        fake_boto3.client.assert_called_once_with("s3")
        assert result is fake_boto3.client.return_value

    def test_endpoint_and_region(self, fake_boto3, monkeypatch):
        monkeypatch.setenv("CVCPKG_S3_ENDPOINT_URL", "https://minio.lab:9000")
        monkeypatch.setenv("CVCPKG_S3_REGION", "us-west-2")
        s3._get_client()
        fake_boto3.client.assert_called_once_with(
            "s3", endpoint_url="https://minio.lab:9000", region_name="us-west-2"
        )


class TestS3Backend:
    def test_schemes_and_supports_range(self):
        assert s3.S3Backend.schemes == ("s3",)
        assert s3.S3Backend().supports_range("s3://b/k") is True

    def test_head(self, monkeypatch):
        client = MagicMock()
        client.head_object.return_value = {
            "ContentLength": 500,
            "ETag": '"abc123"',
            "ContentType": "application/gzip",
        }
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        info = s3.S3Backend().head("s3://bucket/key")
        assert info.size == 500
        assert info.etag == "abc123"  # quotes stripped
        assert info.content_type == "application/gzip"
        client.head_object.assert_called_once_with(Bucket="bucket", Key="key")

    def test_head_defaults(self, monkeypatch):
        client = MagicMock()
        client.head_object.return_value = {}
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        info = s3.S3Backend().head("s3://bucket/key")
        assert info.size == -1
        assert info.etag == ""
        assert info.content_type == ""

    def test_open(self, monkeypatch):
        client = MagicMock()
        body = io.BytesIO(b"s3bytes")
        client.get_object.return_value = {"Body": body}
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        result = s3.S3Backend().open("s3://b/k")
        assert result.read() == b"s3bytes"
        client.get_object.assert_called_once_with(Bucket="b", Key="k")

    def test_put_with_size(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        data = io.BytesIO(b"payload")
        s3.S3Backend().put("s3://b/k", data, size=7)
        client.put_object.assert_called_once_with(
            Bucket="b", Key="k", Body=data, ContentLength=7
        )

    def test_put_without_size(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        data = io.BytesIO(b"payload")
        s3.S3Backend().put("s3://b/k", data)
        client.put_object.assert_called_once_with(Bucket="b", Key="k", Body=data)

    def test_list(self, monkeypatch):
        client = MagicMock()
        paginator = client.get_paginator.return_value
        paginator.paginate.return_value = [
            {
                "Contents": [{"Key": "pre/a"}, {"Key": "pre/b"}],
                "CommonPrefixes": [{"Prefix": "pre/sub/"}],
            }
        ]
        monkeypatch.setattr(s3, "_get_client", lambda: client)
        result = list(s3.S3Backend().list("s3://bucket/pre"))
        assert result == ["a", "b", "sub/"]
        client.get_paginator.assert_called_once_with("list_objects_v2")
        paginator.paginate.assert_called_once_with(
            Bucket="bucket", Prefix="pre/", Delimiter="/"
        )


# ── Registry dispatch for the cloud schemes ─────────────────────


class TestDispatch:
    def test_get_backend_s3(self):
        from cvcpkg.storage import get_backend

        assert "s3" in get_backend("s3://bucket/key").schemes

    def test_get_backend_gs(self):
        from cvcpkg.storage import get_backend

        assert "gs" in get_backend("gs://bucket/key").schemes

    def test_get_backend_azblob(self):
        from cvcpkg.storage import get_backend

        assert "azblob" in get_backend("azblob://container/blob").schemes
