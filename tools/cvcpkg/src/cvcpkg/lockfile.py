"""Read and write lockfile.yaml (§5.4 of the roadmap)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cvcpkg.errors import SchemaError


@dataclass
class LockEntry:
    name: str
    version: str
    upstream_version: str = ""
    source_release: str = ""
    sha256: str = ""
    size_bytes: int = 0
    archive_url: str = ""


@dataclass
class Lockfile:
    schema_version: int = 2
    platform: str = ""
    arch: str = ""
    config: str = ""
    link: str = ""
    resolved_at: str = ""
    catalog_revision: int = 0
    catalog_sha256: str = ""
    bundles: list[LockEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "arch": self.arch,
            "config": self.config,
            "link": self.link,
            "resolved_at": self.resolved_at
            or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "catalog_revision": self.catalog_revision,
            "catalog_sha256": self.catalog_sha256,
            "bundles": [
                {
                    "name": e.name,
                    "version": e.version,
                    "upstream_version": e.upstream_version,
                    "source_release": e.source_release,
                    "sha256": e.sha256,
                    "size_bytes": e.size_bytes,
                    "archive_url": e.archive_url,
                }
                for e in self.bundles
            ],
        }

    def write(self, path: Path) -> None:
        """Write the lockfile to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Lockfile":
        try:
            return cls(
                schema_version=d.get("schema_version", 2),
                platform=d.get("platform", ""),
                arch=d.get("arch", ""),
                config=d.get("config", ""),
                link=d.get("link", ""),
                resolved_at=d.get("resolved_at", ""),
                catalog_revision=d.get("catalog_revision", 0),
                catalog_sha256=d.get("catalog_sha256", ""),
                bundles=[
                    LockEntry(
                        name=e["name"],
                        version=e["version"],
                        upstream_version=e.get("upstream_version", ""),
                        source_release=e.get("source_release", ""),
                        sha256=e.get("sha256", ""),
                        size_bytes=e.get("size_bytes", 0),
                        archive_url=e.get("archive_url", ""),
                    )
                    for e in d.get("bundles", [])
                ],
            )
        except (KeyError, TypeError) as e:
            raise SchemaError(f"malformed lockfile: {e}") from e

    @classmethod
    def read(cls, path: Path) -> "Lockfile":
        if not path.is_file():
            raise SchemaError(f"lockfile not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise SchemaError(f"lockfile is not a YAML mapping: {path}")
        return cls.from_dict(data)
