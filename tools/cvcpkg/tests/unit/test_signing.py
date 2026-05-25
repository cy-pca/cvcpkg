"""Tests for cvcpkg.signing — Ed25519 key management, signing, and verification."""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

from cvcpkg.signing import (
    Signature,
    KeyInfo,
    fingerprint,
    generate_keypair,
    import_public_key,
    list_keys,
    load_private_key,
    load_public_key,
    sign_file,
    sign_bytes,
    verify_file,
    verify_bytes,
    write_signature,
    read_signature,
    _pub_fingerprint,
)
from cvcpkg.errors import SigningError

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def keys_dir(tmp_path: Path) -> Path:
    d = tmp_path / "keys"
    d.mkdir()
    return d


@pytest.fixture
def sample_key(keys_dir: Path) -> KeyInfo:
    return generate_keypair("test", keys_dir=keys_dir)


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world")
    return f


# ── Key generation ──────────────────────────────────────────────


class TestKeyGeneration:
    def test_generate_creates_files(self, keys_dir: Path) -> None:
        info = generate_keypair("mykey", keys_dir=keys_dir)
        assert (keys_dir / "mykey.key").is_file()
        assert (keys_dir / "mykey.pub").is_file()
        assert (keys_dir / "mykey.fp").is_file()
        assert info.fingerprint == (keys_dir / "mykey.fp").read_text().strip()
        assert info.has_private is True
        assert info.label == "mykey"

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX perms")
    def test_private_key_permissions(self, keys_dir: Path) -> None:
        generate_keypair("sec", keys_dir=keys_dir)
        mode = (keys_dir / "sec.key").stat().st_mode & 0o777
        assert mode == 0o600

    def test_generate_with_password(self, keys_dir: Path) -> None:
        info = generate_keypair("pw", keys_dir=keys_dir, password="s3cret")
        priv_path = keys_dir / "pw.key"
        # Loading without password should fail
        with pytest.raises(Exception):
            load_private_key(priv_path, password=None)
        # Loading with correct password should work
        key = load_private_key(priv_path, password="s3cret")
        assert key is not None

    def test_fingerprint_is_64_hex(self, sample_key: KeyInfo) -> None:
        assert len(sample_key.fingerprint) == 64
        int(sample_key.fingerprint, 16)  # should not raise

    def test_fingerprint_deterministic(self) -> None:
        raw = b"\x00" * 32
        assert fingerprint(raw) == fingerprint(raw)


# ── Key listing ─────────────────────────────────────────────────


class TestKeyListing:
    def test_list_empty(self, keys_dir: Path) -> None:
        assert list_keys(keys_dir) == []

    def test_list_after_generate(self, keys_dir: Path) -> None:
        generate_keypair("a", keys_dir=keys_dir)
        generate_keypair("b", keys_dir=keys_dir)
        keys = list_keys(keys_dir)
        labels = {k.label for k in keys}
        assert labels == {"a", "b"}

    def test_list_shows_private_flag(self, keys_dir: Path) -> None:
        generate_keypair("full", keys_dir=keys_dir)
        # Import just a public key
        info = generate_keypair("tmp", keys_dir=keys_dir)
        (keys_dir / "pub_only.pub").write_text(info.public_pem)
        (keys_dir / "pub_only.fp").write_text(info.fingerprint + "\n")
        keys = list_keys(keys_dir)
        by_label = {k.label: k for k in keys}
        assert by_label["full"].has_private is True
        assert by_label["pub_only"].has_private is False


# ── Key import ──────────────────────────────────────────────────


class TestKeyImport:
    def test_import_public_key(self, keys_dir: Path) -> None:
        orig = generate_keypair("origin", keys_dir=keys_dir)
        # Import the public key under a different label
        imported = import_public_key(orig.public_pem, "imported", keys_dir=keys_dir)
        assert imported.fingerprint == orig.fingerprint
        assert imported.has_private is False
        assert (keys_dir / "imported.pub").is_file()


# ── Signing ─────────────────────────────────────────────────────


class TestSigning:
    def test_sign_file(self, sample_key: KeyInfo, sample_file: Path, keys_dir: Path) -> None:
        sig = sign_file(sample_file, keys_dir / "test.key")
        assert sig.key_fingerprint == sample_key.fingerprint
        assert len(sig.sig_b64) > 0

    def test_sign_bytes(self, sample_key: KeyInfo, keys_dir: Path) -> None:
        sig = sign_bytes(b"test data", keys_dir / "test.key")
        assert sig.key_fingerprint == sample_key.fingerprint

    def test_different_data_different_sig(self, keys_dir: Path) -> None:
        generate_keypair("k", keys_dir=keys_dir)
        sig1 = sign_bytes(b"aaa", keys_dir / "k.key")
        sig2 = sign_bytes(b"bbb", keys_dir / "k.key")
        assert sig1.sig_b64 != sig2.sig_b64


# ── Signature file I/O ──────────────────────────────────────────


class TestSignatureIO:
    def test_write_and_read(self, tmp_path: Path) -> None:
        sig = Signature(sig_b64="dGVzdA", key_fingerprint="abcd1234" * 8)
        sig_path = tmp_path / "test.sig"
        write_signature(sig, sig_path)
        loaded = read_signature(sig_path)
        assert loaded.sig_b64 == sig.sig_b64
        assert loaded.key_fingerprint == sig.key_fingerprint


# ── Verification ────────────────────────────────────────────────


class TestVerification:
    def test_verify_file_roundtrip(
        self, sample_key: KeyInfo, sample_file: Path, keys_dir: Path
    ) -> None:
        sig = sign_file(sample_file, keys_dir / "test.key")
        ki = verify_file(sample_file, sig, keys_dir)
        assert ki.label == "test"
        assert ki.fingerprint == sample_key.fingerprint

    def test_verify_bytes_roundtrip(self, sample_key: KeyInfo, keys_dir: Path) -> None:
        data = b"some payload"
        sig = sign_bytes(data, keys_dir / "test.key")
        ki = verify_bytes(data, sig, keys_dir)
        assert ki.fingerprint == sample_key.fingerprint

    def test_verify_tampered_data_fails(
        self, sample_key: KeyInfo, sample_file: Path, keys_dir: Path
    ) -> None:
        sig = sign_file(sample_file, keys_dir / "test.key")
        # Tamper with the file
        sample_file.write_bytes(b"tampered!")
        with pytest.raises(SigningError):
            verify_file(sample_file, sig, keys_dir)

    def test_verify_wrong_signature_fails(
        self, sample_key: KeyInfo, sample_file: Path, keys_dir: Path
    ) -> None:
        bad_sig = Signature(sig_b64="dGVzdA" * 11, key_fingerprint=sample_key.fingerprint)
        with pytest.raises(SigningError):
            verify_file(sample_file, bad_sig, keys_dir)

    def test_verify_no_keys_raises(self, sample_file: Path, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_keys"
        empty_dir.mkdir()
        sig = Signature(sig_b64="dGVzdA", key_fingerprint="aabb" * 16)
        with pytest.raises(SigningError, match="No trusted keys"):
            verify_file(sample_file, sig, empty_dir)

    def test_verify_with_imported_pubkey(self, tmp_path: Path) -> None:
        """Signing with key A, verifying with imported pubkey of A."""
        signer_dir = tmp_path / "signer"
        signer_dir.mkdir()
        verifier_dir = tmp_path / "verifier"
        verifier_dir.mkdir()

        # Signer generates keypair
        info = generate_keypair("signer", keys_dir=signer_dir)

        # Verifier imports public key
        import_public_key(info.public_pem, "trusted-signer", keys_dir=verifier_dir)

        # Sign a file
        data_file = tmp_path / "payload.bin"
        data_file.write_bytes(b"important data")
        sig = sign_file(data_file, signer_dir / "signer.key")

        # Verify with verifier's keyring
        ki = verify_file(data_file, sig, verifier_dir)
        assert ki.label == "trusted-signer"
        assert ki.fingerprint == info.fingerprint


# ── CLI commands ────────────────────────────────────────────────


class TestCLI:
    def test_key_generate(self, keys_dir: Path) -> None:
        from click.testing import CliRunner
        from cvcpkg.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["key", "generate", "--label", "ci", "--keys-dir", str(keys_dir)]
        )
        assert result.exit_code == 0
        assert "Generated key" in result.output
        assert (keys_dir / "ci.key").is_file()

    def test_key_list(self, keys_dir: Path) -> None:
        from click.testing import CliRunner
        from cvcpkg.cli import cli

        generate_keypair("mykey", keys_dir=keys_dir)
        runner = CliRunner()
        result = runner.invoke(cli, ["key", "list", "--keys-dir", str(keys_dir)])
        assert result.exit_code == 0
        assert "mykey" in result.output

    def test_key_export(self, keys_dir: Path) -> None:
        from click.testing import CliRunner
        from cvcpkg.cli import cli

        generate_keypair("exp", keys_dir=keys_dir)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["key", "export", "--label", "exp", "--keys-dir", str(keys_dir)]
        )
        assert result.exit_code == 0
        assert "BEGIN PUBLIC KEY" in result.output

    def test_sign_and_verify_cli(self, keys_dir: Path, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from cvcpkg.cli import cli

        generate_keypair("signer", keys_dir=keys_dir)
        test_file = tmp_path / "archive.tar.gz"
        test_file.write_bytes(b"fake archive content")

        runner = CliRunner()

        # Sign
        result = runner.invoke(
            cli,
            [
                "sign",
                str(test_file),
                "--signing-key",
                str(keys_dir / "signer.key"),
            ],
        )
        assert result.exit_code == 0
        assert "Signed:" in result.output
        assert test_file.with_suffix(".gz.sig").is_file()

        # Verify
        result = runner.invoke(
            cli,
            [
                "verify-sig",
                str(test_file),
                "--keys-dir",
                str(keys_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Verified:" in result.output

    def test_key_import_cli(self, keys_dir: Path, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from cvcpkg.cli import cli

        info = generate_keypair("source", keys_dir=keys_dir)
        pub_file = tmp_path / "exported.pub"
        pub_file.write_text(info.public_pem)

        dest_dir = tmp_path / "dest_keys"
        dest_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "key",
                "import",
                str(pub_file),
                "--label",
                "remote",
                "--keys-dir",
                str(dest_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Imported" in result.output
        assert (dest_dir / "remote.pub").is_file()
