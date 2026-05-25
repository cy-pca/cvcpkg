"""Ed25519 package signing and verification for cvcpkg.

Provides key generation, archive signing (detached signatures), and
verification.  Keys are stored in ``~/.config/cvcpkg/keys/``.

The ``cryptography`` package is a required dependency of cvcpkg.

Key format:
    Private key: PEM-encoded Ed25519 (optionally password-protected)
    Public  key: PEM-encoded Ed25519 public key
    Fingerprint: SHA-256 of the raw 32-byte public key bytes (hex)

Signature format:
    64-byte Ed25519 signature, base64url-encoded (no padding), stored
    in a ``.sig`` file or inline in manifests/catalogs.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cvcpkg.errors import SigningError

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

# ── Key fingerprinting ──────────────────────────────────────────


def fingerprint(public_key_bytes: bytes) -> str:
    """Compute the SHA-256 fingerprint of a raw 32-byte Ed25519 public key."""
    return hashlib.sha256(public_key_bytes).hexdigest()


# ── Data model ──────────────────────────────────────────────────


@dataclass
class KeyInfo:
    """Metadata about an Ed25519 key."""

    fingerprint: str
    label: str  # human-readable name
    public_pem: str
    has_private: bool = False
    path: Path | None = None


@dataclass
class Signature:
    """A detached Ed25519 signature."""

    sig_b64: str  # base64url-encoded 64-byte signature
    key_fingerprint: str  # SHA-256 of the public key


# ── Key directory ───────────────────────────────────────────────


def _default_keys_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) / "cvcpkg" if xdg else Path.home() / ".config" / "cvcpkg"
    return base / "keys"


# ── Key generation ──────────────────────────────────────────────


def generate_keypair(
    label: str,
    keys_dir: Path | None = None,
    password: str | None = None,
) -> KeyInfo:
    """Generate an Ed25519 keypair and save to *keys_dir*.

    Returns a ``KeyInfo`` with the fingerprint and paths.
    Files created::

        <keys_dir>/<label>.key      — private key (PEM, mode 0600)
        <keys_dir>/<label>.pub      — public key  (PEM)
        <keys_dir>/<label>.fp       — fingerprint (hex string)
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if keys_dir is None:
        keys_dir = _default_keys_dir()
    keys_dir.mkdir(parents=True, exist_ok=True)

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    # Serialize
    enc = (
        serialization.BestAvailableEncryption(password.encode())
        if password
        else serialization.NoEncryption()
    )
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        enc,
    )
    pub_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    raw_pub = pub.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fp = fingerprint(raw_pub)

    # Write files
    priv_path = keys_dir / f"{label}.key"
    pub_path = keys_dir / f"{label}.pub"
    fp_path = keys_dir / f"{label}.fp"

    priv_path.write_bytes(priv_pem)
    priv_path.chmod(0o600)
    pub_path.write_bytes(pub_pem)
    fp_path.write_text(fp + "\n")

    return KeyInfo(
        fingerprint=fp,
        label=label,
        public_pem=pub_pem.decode(),
        has_private=True,
        path=priv_path,
    )


# ── Key loading ─────────────────────────────────────────────────


def load_private_key(
    key_path: Path,
    password: str | None = None,
) -> Ed25519PrivateKey:  # type: ignore[name-defined]
    """Load an Ed25519 private key from a PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    data = key_path.read_bytes()
    pw = password.encode() if password else None
    key = load_pem_private_key(data, password=pw)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(key, Ed25519PrivateKey):
        raise SigningError(f"Key at {key_path} is not an Ed25519 key")
    return key


def load_public_key(pub_path: Path) -> Ed25519PublicKey:  # type: ignore[name-defined]
    """Load an Ed25519 public key from a PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    data = pub_path.read_bytes()
    key = load_pem_public_key(data)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(key, Ed25519PublicKey):
        raise SigningError(f"Key at {pub_path} is not an Ed25519 public key")
    return key


def _pub_fingerprint(pub_key: Ed25519PublicKey) -> str:  # type: ignore[name-defined]
    """Get the fingerprint of an Ed25519 public key object."""
    from cryptography.hazmat.primitives import serialization

    raw = pub_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return fingerprint(raw)


# ── Key listing ─────────────────────────────────────────────────


def list_keys(keys_dir: Path | None = None) -> list[KeyInfo]:
    """List all keys in the key directory."""
    if keys_dir is None:
        keys_dir = _default_keys_dir()
    if not keys_dir.is_dir():
        return []

    seen: set[str] = set()
    keys: list[KeyInfo] = []
    for pub_path in sorted(keys_dir.glob("*.pub")):
        label = pub_path.stem
        if label in seen:
            continue
        seen.add(label)
        fp_path = keys_dir / f"{label}.fp"
        fp = fp_path.read_text().strip() if fp_path.is_file() else ""
        priv_exists = (keys_dir / f"{label}.key").is_file()
        pub_pem = pub_path.read_text()
        keys.append(
            KeyInfo(
                fingerprint=fp,
                label=label,
                public_pem=pub_pem,
                has_private=priv_exists,
                path=pub_path,
            )
        )
    return keys


def import_public_key(
    pub_pem: str,
    label: str,
    keys_dir: Path | None = None,
) -> KeyInfo:
    """Import a public key PEM string into the keyring."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(pub_pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise SigningError("Not an Ed25519 public key")

    raw = key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fp = fingerprint(raw)

    if keys_dir is None:
        keys_dir = _default_keys_dir()
    keys_dir.mkdir(parents=True, exist_ok=True)

    pub_path = keys_dir / f"{label}.pub"
    fp_path = keys_dir / f"{label}.fp"
    pub_path.write_text(pub_pem)
    fp_path.write_text(fp + "\n")

    return KeyInfo(
        fingerprint=fp,
        label=label,
        public_pem=pub_pem,
        has_private=False,
        path=pub_path,
    )


# ── Signing ─────────────────────────────────────────────────────


def sign_file(
    file_path: Path,
    key_path: Path,
    password: str | None = None,
) -> Signature:
    """Create a detached Ed25519 signature for a file.

    Signs the SHA-256 digest of the file contents.
    Returns a ``Signature`` with the base64url-encoded sig and
    the key fingerprint.
    """
    priv = load_private_key(key_path, password)
    pub = priv.public_key()
    fp = _pub_fingerprint(pub)

    digest = hashlib.sha256(file_path.read_bytes()).digest()
    sig_bytes = priv.sign(digest)
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).decode().rstrip("=")

    return Signature(sig_b64=sig_b64, key_fingerprint=fp)


def sign_bytes(
    data: bytes,
    key_path: Path,
    password: str | None = None,
) -> Signature:
    """Create a detached Ed25519 signature over raw bytes.

    Signs the SHA-256 digest of *data*.
    """
    priv = load_private_key(key_path, password)
    pub = priv.public_key()
    fp = _pub_fingerprint(pub)

    digest = hashlib.sha256(data).digest()
    sig_bytes = priv.sign(digest)
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).decode().rstrip("=")

    return Signature(sig_b64=sig_b64, key_fingerprint=fp)


def write_signature(sig: Signature, sig_path: Path) -> None:
    """Write a ``.sig`` file (YAML)."""
    import yaml

    sig_path.write_text(
        yaml.dump(
            {"signature": sig.sig_b64, "key_fingerprint": sig.key_fingerprint},
            default_flow_style=False,
        )
    )


def read_signature(sig_path: Path) -> Signature:
    """Read a ``.sig`` file."""
    import yaml

    data = yaml.safe_load(sig_path.read_text())
    return Signature(
        sig_b64=data["signature"],
        key_fingerprint=data["key_fingerprint"],
    )


# ── Verification ────────────────────────────────────────────────


def verify_file(
    file_path: Path,
    sig: Signature,
    keys_dir: Path | None = None,
) -> KeyInfo:
    """Verify a detached signature against trusted keys.

    Returns the ``KeyInfo`` of the key that validated the signature.
    Raises ``SigningError`` if no trusted key can verify the sig.
    """

    digest = hashlib.sha256(file_path.read_bytes()).digest()
    return _verify_digest(digest, sig, keys_dir)


def verify_bytes(
    data: bytes,
    sig: Signature,
    keys_dir: Path | None = None,
) -> KeyInfo:
    """Verify a signature over raw bytes."""
    digest = hashlib.sha256(data).digest()
    return _verify_digest(digest, sig, keys_dir)


def _verify_digest(
    digest: bytes,
    sig: Signature,
    keys_dir: Path | None = None,
) -> KeyInfo:
    """Verify against the trusted keyring, preferring fingerprint match."""
    from cryptography.exceptions import InvalidSignature

    # Decode signature
    padded = sig.sig_b64 + "=" * (-len(sig.sig_b64) % 4)
    sig_bytes = base64.urlsafe_b64decode(padded)

    trusted = list_keys(keys_dir)
    if not trusted:
        raise SigningError("No trusted keys in keyring")

    # Try fingerprint-matched key first
    for ki in trusted:
        if ki.fingerprint == sig.key_fingerprint:
            pub = load_public_key(ki.path) if ki.path else None
            if pub is None:
                continue
            try:
                pub.verify(sig_bytes, digest)
                return ki
            except InvalidSignature:
                raise SigningError(
                    f"Signature invalid: key '{ki.label}' ({ki.fingerprint[:16]}…) "
                    "did not verify"
                ) from None

    # If no fingerprint match, try all keys (allows rotation)
    for ki in trusted:
        pub_path = ki.path
        if pub_path is None:
            continue
        pub = load_public_key(pub_path)
        try:
            pub.verify(sig_bytes, digest)
            return ki
        except InvalidSignature:
            continue

    raise SigningError(
        f"No trusted key verified the signature " f"(fingerprint: {sig.key_fingerprint[:16]}…)"
    )
