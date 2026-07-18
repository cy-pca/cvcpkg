"""Integration test: migrate a populated cvcpkg server to a real S3 backend.

Exercises cvcpkg's *actual* ``s3://`` storage backend (``cvcpkg.backends.s3``,
boto3) end-to-end through the migration + doctor, against a mocked S3 service
(``moto``).  Garage — the target in production — speaks the same S3 API, so
this proves the exact code path a ``file://`` → Garage migration takes,
without needing a live cluster (the live Garage round-trip is proven
separately in the vm-provisioning repo).

Skipped automatically when ``moto``/``boto3`` aren't installed.
"""

from __future__ import annotations

import hashlib

import pytest
import yaml

boto3 = pytest.importorskip("boto3", reason="boto3 required for the S3 migration test")
moto = pytest.importorskip("moto", reason="moto required for the S3 migration test")

from cvcpkg.server import archive_store  # noqa: E402
from cvcpkg.server import storage_doctor as doc  # noqa: E402
from cvcpkg.server.storage_migration import run_migration  # noqa: E402

_BUCKET = "cvcpkg-archives"
_ARCHIVES = {
    "alpha-1.0.0-linux-x86_64-release-shared.tar.zst": b"ALPHA" * 5000,
    "beta-1.0.0-linux-x86_64-release-shared.tar.zst": bytes(range(256)) * 40,
    "gamma-2.1.0-linux-arm64-release-static.tar.zst": b"gamma-payload-\x00\x01\x02" * 700,
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _populate(state_dir, archives):
    adir = state_dir / archive_store.ARCHIVES_SUBDIR
    adir.mkdir(parents=True, exist_ok=True)
    bundles = []
    for fn, data in archives.items():
        (adir / fn).write_bytes(data)
        bundles.append(
            {
                "name": fn.split("-")[0],
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": _sha(data),
                "size_bytes": len(data),
                "archive_url": f"/v1/download/{fn}",
            }
        )
    (state_dir / "index.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": bundles})
    )


@pytest.fixture
def s3_env(monkeypatch):
    """Mocked S3 with dummy creds; yields a boto3 client for assertions/botching."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("CVCPKG_S3_REGION", "us-east-1")
    # Leave CVCPKG_S3_ENDPOINT_URL unset: moto patches botocore at the default
    # endpoint, so cvcpkg's S3Backend talks to the mock transparently.
    monkeypatch.delenv("CVCPKG_S3_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        # A fresh registry so the s3 backend is (re)loaded under the mock.
        from cvcpkg import storage

        storage._registry.pop("s3", None)
        yield client
        storage._registry.pop("s3", None)


def _s3_key(filename: str) -> str:
    return f"prefix/{archive_store.ARCHIVES_SUBDIR}/{filename}"


def test_migrate_file_to_s3_and_serve(tmp_path, s3_env):
    _populate(tmp_path, _ARCHIVES)
    dest = f"s3://{_BUCKET}/prefix"

    result = run_migration(tmp_path, dest, deep_verify=True)
    assert result.ok and result.flipped
    assert result.migrated == len(_ARCHIVES)

    # Objects really landed in S3 with the right bytes.
    for fn, data in _ARCHIVES.items():
        obj = s3_env.get_object(Bucket=_BUCKET, Key=_s3_key(fn))
        assert obj["Body"].read() == data

    # Active backend switched to S3, and the doctor confirms integrity there.
    assert archive_store.load_storage_uri(tmp_path, f"file://{tmp_path}") == dest
    report = doc.diagnose(tmp_path, deep=True)
    assert report.healthy, [(f.filename, f.status, f.detail) for f in report.findings]

    # And the server's archive-read primitive streams the bytes back from S3.
    for fn, data in _ARCHIVES.items():
        with archive_store.open_stream(dest, fn) as fh:
            assert fh.read() == data


def test_doctor_heals_botched_s3_migration(tmp_path, s3_env):
    _populate(tmp_path, _ARCHIVES)
    source = f"file://{tmp_path}"
    dest = f"s3://{_BUCKET}/prefix"
    assert run_migration(tmp_path, dest).ok

    # Botch the destination the way a half-finished migration would: drop one
    # object entirely, truncate another so its hash no longer matches.
    missing_fn = "alpha-1.0.0-linux-x86_64-release-shared.tar.zst"
    corrupt_fn = "beta-1.0.0-linux-x86_64-release-shared.tar.zst"
    s3_env.delete_object(Bucket=_BUCKET, Key=_s3_key(missing_fn))
    s3_env.put_object(Bucket=_BUCKET, Key=_s3_key(corrupt_fn), Body=b"truncated")

    report = doc.diagnose(tmp_path, deep=True)
    assert not report.healthy
    assert {f.filename for f in report.missing} == {missing_fn}
    assert {f.filename for f in report.corrupt} == {corrupt_fn}

    # Heal from the still-intact local source backend.
    heal = doc.heal(tmp_path, source_uri=source)
    assert heal.ok, heal.unhealable
    assert set(heal.healed) == {missing_fn, corrupt_fn}

    # S3 now matches the catalog again.
    assert doc.diagnose(tmp_path, deep=True).healthy
    assert (
        s3_env.get_object(Bucket=_BUCKET, Key=_s3_key(missing_fn))["Body"].read()
        == _ARCHIVES[missing_fn]
    )
