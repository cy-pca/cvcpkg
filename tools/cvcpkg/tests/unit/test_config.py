"""Tests for cvcpkg.config — configuration loading and merging."""

from pathlib import Path

import yaml

from cvcpkg.config import (
    DEFAULT_CATALOG_URL,
    CvcpkgConfig,
    MirrorRule,
    load_user_config,
    merge_cli_overrides,
    merge_project_config,
)


def test_defaults():
    cfg = CvcpkgConfig()
    assert cfg.catalog_primary == DEFAULT_CATALOG_URL
    assert cfg.mirrors == []
    assert cfg.accept_abi_mismatch is False


def test_load_user_config_missing_dir(tmp_path):
    cfg = load_user_config(tmp_path / "nonexistent")
    assert cfg.catalog_primary == DEFAULT_CATALOG_URL


def test_load_user_config_empty_file(tmp_path):
    (tmp_path / "config.yaml").write_text("")
    cfg = load_user_config(tmp_path)
    assert cfg.catalog_primary == DEFAULT_CATALOG_URL


def test_load_user_config_with_catalog(tmp_path):
    data = {
        "catalog": {
            "primary": "https://example.com/catalog.yaml",
            "fallback": ["https://mirror.example.com/catalog.yaml"],
        }
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(data))
    cfg = load_user_config(tmp_path)
    assert cfg.catalog_primary == "https://example.com/catalog.yaml"
    assert cfg.catalog_fallbacks == ["https://mirror.example.com/catalog.yaml"]


def test_load_user_config_with_mirrors(tmp_path):
    data = {
        "mirrors": [
            {"match": "https://github.com/", "rewrite": "https://ghproxy.example.com/"},
        ]
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(data))
    cfg = load_user_config(tmp_path)
    assert len(cfg.mirrors) == 1
    assert cfg.mirrors[0].match == "https://github.com/"
    assert cfg.mirrors[0].rewrite == "https://ghproxy.example.com/"


def test_load_user_config_with_backends(tmp_path):
    data = {"backends": {"s3": {"endpoint_url": "https://minio.local:9000"}}}
    (tmp_path / "config.yaml").write_text(yaml.dump(data))
    cfg = load_user_config(tmp_path)
    assert cfg.backend_options["s3"]["endpoint_url"] == "https://minio.local:9000"


def test_apply_mirrors():
    cfg = CvcpkgConfig(
        mirrors=[
            MirrorRule(match="https://github.com/", rewrite="https://mirror.local/"),
        ]
    )
    urls = cfg.apply_mirrors("https://github.com/owner/repo/v1.0.tar.gz")
    assert len(urls) == 2
    assert urls[0] == "https://mirror.local/owner/repo/v1.0.tar.gz"
    assert urls[1] == "https://github.com/owner/repo/v1.0.tar.gz"


def test_apply_mirrors_no_match():
    cfg = CvcpkgConfig(mirrors=[MirrorRule(match="https://github.com/", rewrite="https://mirror/")])
    urls = cfg.apply_mirrors("https://example.com/file.tar.gz")
    assert urls == ["https://example.com/file.tar.gz"]


def test_merge_project_config():
    base = CvcpkgConfig()
    reqs = {
        "catalog": {"primary": "https://project.example.com/catalog.yaml"},
        "mirrors": [
            {"match": "https://a/", "rewrite": "https://b/"},
        ],
        "accept_abi_mismatch": True,
    }
    result = merge_project_config(base, reqs)
    assert result.catalog_primary == "https://project.example.com/catalog.yaml"
    assert len(result.mirrors) == 1
    assert result.accept_abi_mismatch is True


def test_merge_project_config_catalog_as_string():
    base = CvcpkgConfig()
    reqs = {"catalog": "https://simple.example.com/catalog.yaml"}
    result = merge_project_config(base, reqs)
    assert result.catalog_primary == "https://simple.example.com/catalog.yaml"


def test_merge_cli_overrides_catalog():
    base = CvcpkgConfig()
    result = merge_cli_overrides(base, catalog_url="https://local/cat.yaml")
    assert result.catalog_primary == "https://local/cat.yaml"


def test_merge_cli_overrides_mirrors():
    base = CvcpkgConfig()
    result = merge_cli_overrides(base, mirror_rules=["https://github.com/=https://mirror/"])
    assert len(result.mirrors) == 1
    assert result.mirrors[0].match == "https://github.com/"
    assert result.mirrors[0].rewrite == "https://mirror/"


def test_merge_cli_overrides_empty():
    base = CvcpkgConfig(catalog_primary="https://keep.me/")
    result = merge_cli_overrides(base)
    assert result.catalog_primary == "https://keep.me/"
