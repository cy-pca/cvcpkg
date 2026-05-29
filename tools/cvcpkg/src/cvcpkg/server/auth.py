"""Token-based authentication for cvcpkg-server.

Tokens are HMAC-SHA256 hashes of random secrets.  Only the hash is
persisted; the raw token is shown once at creation time.  Tokens
carry a role (reader, publisher, admin) and an optional expiry.

Token store is a YAML file at ``<state_dir>/tokens.yaml``.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets
from pathlib import Path

import yaml

from cvcpkg.server.models import TokenRecord, TokenRole

# 32-byte server-side HMAC key (generated once, persisted alongside tokens).
_HMAC_KEY_FILE = "hmac_key"
_TOKENS_FILE = "tokens.yaml"


def _ensure_hmac_key(state_dir: Path) -> bytes:
    """Return the HMAC key, creating it if it doesn't exist."""
    key_path = state_dir / _HMAC_KEY_FILE
    if key_path.is_file():
        return key_path.read_bytes()
    state_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def _hash_token(raw_token: str, hmac_key: bytes) -> str:
    """Derive a deterministic hash from *raw_token* using HMAC-SHA256."""
    return hmac.new(hmac_key, raw_token.encode(), hashlib.sha256).hexdigest()


def _load_tokens(state_dir: Path) -> list[TokenRecord]:
    tokens_path = state_dir / _TOKENS_FILE
    if not tokens_path.is_file():
        return []
    with open(tokens_path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        return []
    return [TokenRecord(**entry) for entry in data]


def _save_tokens(state_dir: Path, tokens: list[TokenRecord]) -> None:
    tokens_path = state_dir / _TOKENS_FILE
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(tokens_path, "w") as f:
        yaml.safe_dump(
            [t.model_dump(mode="json") for t in tokens],
            f,
            default_flow_style=False,
            sort_keys=False,
        )
    tokens_path.chmod(0o600)


class TokenStore:
    """Manages API tokens for the server."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._hmac_key = _ensure_hmac_key(state_dir)
        self._tokens = _load_tokens(state_dir)

    def create(
        self,
        name: str,
        role: TokenRole = TokenRole.publisher,
        expires_in_days: int | None = None,
        email: str = "",
    ) -> str:
        """Create a new token and return the raw secret (shown once)."""
        if any(t.name == name and not t.revoked for t in self._tokens):
            raise ValueError(f"active token named '{name}' already exists")

        raw = f"cvctok_{secrets.token_urlsafe(32)}"
        token_hash = _hash_token(raw, self._hmac_key)

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                days=expires_in_days
            )

        record = TokenRecord(
            name=name,
            role=role,
            token_hash=token_hash,
            email=email,
            expires_at=expires_at,
        )
        self._tokens.append(record)
        self._persist()
        return raw

    def verify(self, raw_token: str) -> TokenRecord | None:
        """Verify a raw token and return its record, or None."""
        token_hash = _hash_token(raw_token, self._hmac_key)
        now = datetime.datetime.now(datetime.timezone.utc)
        for t in self._tokens:
            if t.token_hash == token_hash:
                if t.revoked:
                    return None
                if t.expires_at is not None and t.expires_at < now:
                    return None
                return t
        return None

    def revoke(self, name: str) -> bool:
        """Revoke a token by name.  Returns True if found."""
        for t in self._tokens:
            if t.name == name and not t.revoked:
                t.revoked = True
                self._persist()
                return True
        return False

    def update_email(self, name: str, email: str) -> bool:
        """Update the email for a token by name.  Returns True if found."""
        for t in self._tokens:
            if t.name == name and not t.revoked:
                t.email = email
                self._persist()
                return True
        return False

    def list_tokens(self) -> list[TokenRecord]:
        """Return all token records (without secrets)."""
        return list(self._tokens)

    def _persist(self) -> None:
        _save_tokens(self._state_dir, self._tokens)
