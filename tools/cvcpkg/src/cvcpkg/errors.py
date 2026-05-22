"""Typed exception hierarchy for cvcpkg."""

from __future__ import annotations


class CvcpkgError(Exception):
    """Base exception for all cvcpkg errors."""


class SchemaError(CvcpkgError):
    """A YAML document does not conform to its schema."""


class CatalogError(CvcpkgError):
    """Problem fetching or parsing the catalog."""


class ResolveError(CvcpkgError):
    """The resolver could not find a consistent set of bundles."""


class IntegrityError(CvcpkgError):
    """SHA-256 mismatch on a downloaded archive or installed file."""


class InstallError(CvcpkgError):
    """A bundle could not be installed into the prefix."""


class CollisionError(InstallError):
    """Two bundles ship differing files at the same path."""


class AbiError(CvcpkgError):
    """ABI-tag mismatch between two bundles."""
