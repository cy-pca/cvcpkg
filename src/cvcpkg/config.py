"""User and project configuration for cvcpkg.

Configuration is loaded from three sources in increasing precedence:

1. Compiled-in defaults (upstream catalog + GitHub Releases)
2. User config: ``~/.config/cvcpkg/config.yaml``
3. Project overrides: ``catalog:`` and ``mirrors:`` in
   ``cvc-requirements.yaml``
4. CLI flags: ``--catalog``, ``--mirror``

See §5.9.3 of the split-distribution roadmap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ── Defaults ────────────────────────────────────────────────────

DEFAULT_SERVER_URL = "https://cvcpkg.org"
DEFAULT_CATALOG_URL = f"{DEFAULT_SERVER_URL}/v1/catalog"
GITHUB_CATALOG_URL = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"
DEFAULT_CATALOG_FALLBACKS = [GITHUB_CATALOG_URL]


def default_server_url() -> str:
    """Return the server URL from env or compiled-in default."""
    return os.environ.get("CVCPKG_SERVER_URL", DEFAULT_SERVER_URL)


def default_catalog_url() -> str:
    """Derive the catalog URL from the server URL.

    Resolution order:
      1. ``CVCPKG_CATALOG_URL`` env var (explicit override)
      2. ``{CVCPKG_SERVER_URL}/v1/catalog``
      3. ``{DEFAULT_SERVER_URL}/v1/catalog``
    """
    explicit = os.environ.get("CVCPKG_CATALOG_URL")
    if explicit:
        return explicit
    return f"{default_server_url().rstrip('/')}/v1/catalog"


# ── Data model ──────────────────────────────────────────────────


@dataclass
class MirrorRule:
    """Rewrite rule: if an artifact URL starts with *match*, replace
    that prefix with *rewrite*."""

    match: str
    rewrite: str


@dataclass
class CvcpkgConfig:
    """Merged configuration from all sources."""

    # Catalog
    catalog_primary: str = ""
    catalog_fallbacks: list[str] = field(default_factory=lambda: list(DEFAULT_CATALOG_FALLBACKS))

    def __post_init__(self):
        if not self.catalog_primary:
            self.catalog_primary = default_catalog_url()

    # Mirror rewrite rules (tried in order)
    mirrors: list[MirrorRule] = field(default_factory=list)

    # Per-scheme backend options (scheme → dict of options)
    backend_options: dict[str, dict[str, str]] = field(default_factory=dict)

    # ABI
    accept_abi_mismatch: bool = False

    def apply_mirrors(self, url: str) -> list[str]:
        """Return a list of URLs to try, applying mirror rewrites.

        The original URL is always included as the last fallback.
        """
        urls: list[str] = []
        for rule in self.mirrors:
            if url.startswith(rule.match):
                urls.append(rule.rewrite + url[len(rule.match) :])
        urls.append(url)
        return urls


# ── Loading ─────────────────────────────────────────────────────


def _default_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg"
    return Path.home() / ".config" / "cvcpkg"


def _load_yaml_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _parse_mirrors(raw: list) -> list[MirrorRule]:
    rules = []
    for entry in raw:
        if isinstance(entry, dict) and "match" in entry and "rewrite" in entry:
            rules.append(MirrorRule(match=entry["match"], rewrite=entry["rewrite"]))
    return rules


def load_user_config(config_dir: Path | None = None) -> CvcpkgConfig:
    """Load ``~/.config/cvcpkg/config.yaml``."""
    if config_dir is None:
        config_dir = _default_config_dir()
    d = _load_yaml_file(config_dir / "config.yaml")
    if not d:
        return CvcpkgConfig()

    catalog = d.get("catalog", {})
    return CvcpkgConfig(
        catalog_primary=catalog.get("primary", default_catalog_url()),
        catalog_fallbacks=catalog.get("fallback", list(DEFAULT_CATALOG_FALLBACKS)),
        mirrors=_parse_mirrors(d.get("mirrors", [])),
        backend_options=d.get("backends", {}),
        accept_abi_mismatch=d.get("accept_abi_mismatch", False),
    )


def merge_project_config(base: CvcpkgConfig, requirements_dict: dict) -> CvcpkgConfig:
    """Overlay project-level overrides from a requirements dict."""
    catalog = requirements_dict.get("catalog", {})
    if isinstance(catalog, str):
        base.catalog_primary = catalog
    elif isinstance(catalog, dict):
        if "primary" in catalog:
            base.catalog_primary = catalog["primary"]
        if "fallback" in catalog:
            base.catalog_fallbacks = catalog["fallback"]

    mirrors = requirements_dict.get("mirrors", [])
    if mirrors:
        base.mirrors = _parse_mirrors(mirrors) + base.mirrors

    if requirements_dict.get("accept_abi_mismatch"):
        base.accept_abi_mismatch = True

    return base


def merge_cli_overrides(
    base: CvcpkgConfig,
    *,
    catalog_url: str = "",
    mirror_rules: list[str] | None = None,
) -> CvcpkgConfig:
    """Apply CLI flags on top of the merged config.

    *mirror_rules* is a list of ``MATCH=REWRITE`` strings.
    """
    if catalog_url:
        base.catalog_primary = catalog_url

    if mirror_rules:
        cli_mirrors = []
        for rule in mirror_rules:
            if "=" in rule:
                match, rewrite = rule.split("=", 1)
                cli_mirrors.append(MirrorRule(match=match, rewrite=rewrite))
        base.mirrors = cli_mirrors + base.mirrors

    return base


# ── Federated registries ────────────────────────────────────────
#
# A dependency may name a federated registry *host* (see cvcpkg.refs).  The
# host is resolved to a base URL + token through the registries config, which
# is also the trust **allowlist**: a host that is not configured here is
# refused by the resolver, so a recipe can never point the resolver at an
# arbitrary/attacker host.


@dataclass(frozen=True)
class Registry:
    """A federated cvcpkg registry: a logical host -> base URL + credential."""

    host: str
    url: str
    token: str = ""


def _registries_path(config_dir: Path | None = None) -> Path:
    override = os.environ.get("CVCPKG_REGISTRIES_FILE")
    if override:
        return Path(override)
    if config_dir is None:
        config_dir = _default_config_dir()
    return config_dir / "registries.yaml"


def load_registries(config_dir: Path | None = None) -> dict[str, Registry]:
    """Load federated registry entries as ``{host: Registry}``.

    Sources (later overriding earlier):
      1. ``<config_dir>/registries.yaml`` (or ``$CVCPKG_REGISTRIES_FILE``)
      2. ``$CVCPKG_REGISTRIES`` — inline YAML/JSON, for CI/containers

    The set of configured hosts is the federation allowlist.
    """
    raw: dict = {}
    fd = _load_yaml_file(_registries_path(config_dir))
    if isinstance(fd.get("registries"), dict):
        raw.update(fd["registries"])
    env = os.environ.get("CVCPKG_REGISTRIES", "").strip()
    if env:
        try:
            parsed = yaml.safe_load(env)
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise ValueError(f"CVCPKG_REGISTRIES is not valid YAML/JSON: {exc}") from exc
        reg = parsed.get("registries", parsed) if isinstance(parsed, dict) else None
        if isinstance(reg, dict):
            raw.update(reg)

    out: dict[str, Registry] = {}
    for host, entry in raw.items():
        host = str(host).strip()
        if not host or not isinstance(entry, dict):
            continue
        url = str(entry.get("url", "") or "").rstrip("/") or f"https://{host}"
        out[host] = Registry(host=host, url=url, token=str(entry.get("token", "") or ""))
    return out


def registry_for(host: str, config_dir: Path | None = None) -> Registry | None:
    """Return the configured registry for *host*, or None if not allowlisted."""
    if not host:
        return None
    return load_registries(config_dir).get(host)
