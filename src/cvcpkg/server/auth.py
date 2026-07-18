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
        description: str = "",
        metadata: str = "",
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
            description=description,
            metadata=metadata,
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
            matches_current = hmac.compare_digest(t.token_hash, token_hash)
            matches_previous = (
                t.previous_token_hash != ""
                and t.previous_hash_expires_at is not None
                and now < t.previous_hash_expires_at
                and hmac.compare_digest(t.previous_token_hash, token_hash)
            )
            if matches_current or matches_previous:
                if t.revoked:
                    return None
                if t.expires_at is not None and t.expires_at < now:
                    return None
                if matches_previous and not matches_current:
                    # Copy so the transient flag never reaches _persist().
                    return t.model_copy(update={"via_previous_hash": True})
                return t
        return None

    def rotate(self, name: str, grace_minutes: int = 0) -> str | None:
        """Swap in a new secret for an active token, returning the raw value.

        The record (name, role, expiry, org memberships keyed by name) is
        untouched; only the secret changes.  With ``grace_minutes > 0`` the
        old secret keeps verifying until the window closes, so stored
        copies can be updated without an outage.  Returns None if no
        active token has this name.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        for t in self._tokens:
            if t.name == name and not t.revoked:
                if t.expires_at is not None and t.expires_at < now:
                    # An expired token cannot verify, so a "rotated" secret
                    # would be dead on arrival — treat like not found.
                    return None
                raw = f"cvctok_{secrets.token_urlsafe(32)}"
                if grace_minutes > 0:
                    window_end = now + datetime.timedelta(minutes=grace_minutes)
                    if t.expires_at is not None and t.expires_at < window_end:
                        # The old secret can never outlive the token itself.
                        window_end = t.expires_at
                    t.previous_token_hash = t.token_hash
                    t.previous_hash_expires_at = window_end
                else:
                    t.previous_token_hash = ""
                    t.previous_hash_expires_at = None
                t.token_hash = _hash_token(raw, self._hmac_key)
                self._persist()
                return raw
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

    def update_profile(
        self,
        name: str,
        description: str | None = None,
        metadata: str | None = None,
    ) -> bool:
        """Update profile fields for a token by name.  Returns True if found."""
        for t in self._tokens:
            if t.name == name and not t.revoked:
                if description is not None:
                    t.description = description
                if metadata is not None:
                    t.metadata = metadata
                self._persist()
                return True
        return False

    def get_public_profile(self, name: str) -> TokenRecord | None:
        """Look up a user by name, returning their record (without secret)."""
        for t in self._tokens:
            if t.name == name and not t.revoked:
                return t
        return None

    def get_profile_by_email(self, email: str) -> TokenRecord | None:
        """Look up a user by email, returning first matching active record."""
        for t in self._tokens:
            if t.email == email and not t.revoked:
                return t
        return None

    def search_users(
        self,
        *,
        name: str = "",
        email: str = "",
        role: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TokenRecord], int]:
        """Search active users with optional name/email/role filter.

        Returns (page, total_matching).
        """
        results = [t for t in self._tokens if not t.revoked]
        if name:
            results = [t for t in results if name.lower() in t.name.lower()]
        if email:
            results = [t for t in results if email.lower() in t.email.lower()]
        if role:
            results = [t for t in results if t.role.value == role]
        total = len(results)
        return results[offset : offset + limit], total

    def list_tokens(self) -> list[TokenRecord]:
        """Return all token records (without secrets)."""
        return list(self._tokens)

    def _persist(self) -> None:
        _save_tokens(self._state_dir, self._tokens)
