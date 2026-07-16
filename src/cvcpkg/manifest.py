"""Data models for bundle manifests, release indexes, and requirements files.

Uses plain dataclasses (no pydantic dependency) so the package stays
dependency-light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cvcpkg.errors import SchemaError

# ── Bundle manifest (share/libcvc-deps/manifest.yaml) ───────────


@dataclass
class AbiTag:
    cxx_std: int = 17
    cxx_runtime: str = ""
    libc: str = ""
    crt_link: str = ""  # "dynamic" | "static"
    extra: list[str] = field(default_factory=list)


@dataclass
class CmakePackage:
    name: str
    targets: list[str]


@dataclass
class Dependency:
    name: str
    version: str = ""
    reason: str = ""
    org: str = ""
    server: str = ""  # federated registry host[:port]; "" = current server


@dataclass
class BundleManifest:
    """Parsed ``share/libcvc-deps/manifest.yaml``."""

    schema_version: int
    name: str
    version: str
    upstream_version: str
    cvc_revision: int
    platform: str
    arch: str
    build_type: str
    link: str
    link_actual: str = ""
    triplet: str = ""
    abi: AbiTag = field(default_factory=AbiTag)
    introduced_in: str = ""
    last_seen_in: str = ""
    # True when this bundle is itself a cross-build toolchain -- a recipe that
    # declares ``cross_toolchain`` (e.g. emsdk) -- rather than a target-runtime
    # deliverable.  Such host tools install into a separate host-tools prefix
    # and are stripped on install unless kept.  See cvcpkg.host_tools.
    host_tool: bool = False

    # contents
    description: str = ""
    files: list[str] = field(default_factory=list)
    cmake_packages: list[CmakePackage] = field(default_factory=list)
    pkgconfig: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    # dependencies
    required_deps: list[Dependency] = field(default_factory=list)
    optional_deps: list[Dependency] = field(default_factory=list)

    # provides
    provides: list[str] = field(default_factory=list)

    # integrity
    sha256: str = ""
    size_bytes: int = 0
    built_at: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> BundleManifest:
        """Parse a manifest.yaml dict into a BundleManifest."""
        if d.get("schema_version") not in (1, 2, 3):
            raise SchemaError(f"unsupported manifest schema_version: {d.get('schema_version')}")

        try:
            b = d.get("bundle", {})
            abi_raw = b.get("abi", {})
            abi = AbiTag(
                cxx_std=abi_raw.get("cxx_std", 17),
                cxx_runtime=abi_raw.get("cxx_runtime", ""),
                libc=abi_raw.get("libc", ""),
                crt_link=abi_raw.get("crt_link", ""),
                extra=abi_raw.get("extra", []),
            )

            contents = d.get("contents", {})
            # Support both new ("dependencies") and legacy ("depends")
            deps = d.get("dependencies", {})
            if isinstance(deps, dict):
                dep_required = deps.get("required", [])
            else:
                dep_required = []
            # Fallback: legacy flat "depends" list
            if not dep_required:
                legacy_deps = d.get("depends", [])
                if isinstance(legacy_deps, list):
                    dep_required = legacy_deps
            integrity = d.get("integrity", {})
            meta = d.get("meta", {})

            return cls(
                schema_version=d["schema_version"],
                name=b["name"],
                version=b["version"],
                upstream_version=b["upstream_version"],
                cvc_revision=b["cvc_revision"],
                platform=b["platform"],
                arch=b["arch"],
                build_type=b.get("build_type", b.get("config", "release")),
                link=b["link"],
                link_actual=b.get("link_actual", b["link"]),
                triplet=b.get("triplet", ""),
                abi=abi,
                introduced_in=b.get("introduced_in", ""),
                last_seen_in=b.get("last_seen_in", ""),
                host_tool=bool(b.get("host_tool", False)),
                description=contents.get("description", meta.get("description", "")),
                files=contents.get("files", []),
                cmake_packages=[
                    CmakePackage(name=p["name"], targets=p["targets"])
                    for p in contents.get("cmake_packages", [])
                ],
                pkgconfig=contents.get("pkgconfig", []),
                tools=contents.get("tools", []),
                required_deps=[
                    Dependency(
                        name=dep["name"],
                        version=dep.get("version", ""),
                        reason=dep.get("reason", ""),
                        org=dep.get("org", ""),
                        server=dep.get("server", ""),
                    )
                    for dep in dep_required
                ],
                optional_deps=[
                    Dependency(
                        name=dep["name"],
                        version=dep.get("version", ""),
                        reason=dep.get("reason", ""),
                        org=dep.get("org", ""),
                        server=dep.get("server", ""),
                    )
                    for dep in (deps.get("optional", []) if isinstance(deps, dict) else [])
                ],
                provides=d.get("provides", []),
                sha256=integrity.get("sha256", ""),
                size_bytes=integrity.get("size_bytes", 0),
                built_at=integrity.get("built_at", meta.get("built_at", "")),
            )
        except KeyError as e:
            raise SchemaError(f"manifest missing required field: {e}") from e

    @classmethod
    def from_yaml(cls, path: str) -> BundleManifest:
        p = Path(path)
        if not p.is_file():
            raise SchemaError(f"manifest file not found: {path}")
        with open(p) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise SchemaError(f"manifest is not a YAML mapping: {path}")
        return cls.from_dict(data)


# ── Catalog entry (lightweight view for the resolver) ────────────


@dataclass
class CatalogEntry:
    """One bundle in the catalog, carrying enough info for resolution."""

    name: str
    version: str
    upstream_version: str
    cvc_revision: int
    platform: str
    arch: str
    build_type: str
    link: str
    sha256: str
    size_bytes: int
    archive_url: str
    source_release: str  # libcvc-deps release that first shipped it
    required_deps: list[Dependency] = field(default_factory=list)
    mirror_urls: list[str] = field(default_factory=list)  # fallback download URLs
    signature: str = ""  # base64url Ed25519 sig (empty = unsigned)
    key_fingerprint: str = ""  # SHA-256 of signing public key
    org: str = ""  # organization slug (empty = public/base package)

    @property
    def qualified_name(self) -> str:
        """Return ``org/name`` for org packages, plain ``name`` otherwise."""
        return f"{self.org}/{self.name}" if self.org else self.name


# ── Release index (libcvc-deps-<ver>-index.yaml) ────────────────


@dataclass
class ReleaseIndex:
    schema_version: int
    release_version: str
    recommended: dict[str, str]  # component name → version
    bundles: list[CatalogEntry]

    @classmethod
    def from_dict(cls, d: dict) -> ReleaseIndex:
        return cls(
            schema_version=d.get("schema_version", 1),
            release_version=d.get("release_version", ""),
            recommended=d.get("recommended", {}),
            bundles=[
                CatalogEntry(
                    name=e["name"],
                    version=e["version"],
                    upstream_version=e.get("upstream_version", ""),
                    cvc_revision=e.get("cvc_revision", 1),
                    platform=e.get("platform", ""),
                    arch=e.get("arch", ""),
                    build_type=e.get("build_type", ""),
                    link=e.get("link", ""),
                    sha256=e.get("sha256", ""),
                    size_bytes=e.get("size_bytes", 0),
                    archive_url=e.get("archive_url", ""),
                    source_release=e.get("source_release", ""),
                    required_deps=[
                        Dependency(
                            name=dep["name"],
                            version=dep.get("version", ""),
                            org=dep.get("org", ""),
                            server=dep.get("server", ""),
                        )
                        for dep in e.get("required_deps", [])
                    ],
                    mirror_urls=e.get("mirror_urls", []),
                )
                for e in d.get("bundles", [])
            ],
        )


# ── Requirements file (cvc-requirements.yaml) ───────────────────


@dataclass
class ComponentReq:
    name: str
    version: str = ""
    exclude: bool = False


@dataclass
class Requirements:
    platform: str = "auto"
    arch: str = "auto"
    config: str = "release"
    link: str = "shared"
    libcvc_deps: str = ""  # optional release pin
    components: list[ComponentReq] = field(default_factory=list)
    overrides: list[ComponentReq] = field(default_factory=list)
    accept_abi_mismatch: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> Requirements:
        components: list[ComponentReq] = []
        for c in d.get("components", []):
            if isinstance(c, str):
                components.append(ComponentReq(name=c))
            else:
                components.append(
                    ComponentReq(
                        name=c["name"],
                        version=c.get("version", ""),
                        exclude=c.get("exclude", False),
                    )
                )

        overrides: list[ComponentReq] = []
        for o in d.get("overrides", []):
            overrides.append(
                ComponentReq(
                    name=o["name"],
                    version=o.get("version", ""),
                    exclude=o.get("exclude", False),
                )
            )

        return cls(
            platform=d.get("platform", "auto"),
            arch=d.get("arch", "auto"),
            config=d.get("config", "release"),
            link=d.get("link", "shared"),
            libcvc_deps=d.get("libcvc-deps", ""),
            components=components,
            overrides=overrides,
            accept_abi_mismatch=d.get("accept_abi_mismatch", False),
        )

    @classmethod
    def from_yaml(cls, path: str) -> Requirements:
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))
