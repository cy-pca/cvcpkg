"""Federated registries config: host -> url+token, and the allowlist gate."""

from __future__ import annotations

import pytest

from cvcpkg.config import Registry, load_registries, registry_for


def _write(config_dir, body: str):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "registries.yaml").write_text(body)


def test_loads_file_entries(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)
    _write(
        tmp_path,
        """
registries:
  edge-b.lab:
    url: http://edge-b.lab:8420
    token: tok-b
  edge-c.lab:
    url: https://edge-c.lab
""",
    )
    regs = load_registries(config_dir=tmp_path)
    assert regs["edge-b.lab"] == Registry("edge-b.lab", "http://edge-b.lab:8420", "tok-b")
    assert regs["edge-c.lab"] == Registry("edge-c.lab", "https://edge-c.lab", "")


def test_default_url_is_https_host(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)
    _write(tmp_path, "registries:\n  edge-d.lab:\n    token: t\n")
    assert load_registries(config_dir=tmp_path)["edge-d.lab"].url == "https://edge-d.lab"


def test_env_overrides_and_merges(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)
    _write(tmp_path, "registries:\n  edge-b.lab:\n    url: http://old\n    token: file-tok\n")
    # Env is inline YAML/JSON and overrides the file entry + adds a new host.
    monkeypatch.setenv(
        "CVCPKG_REGISTRIES",
        '{"edge-b.lab": {"url": "http://new:8420", "token": "env-tok"}, '
        '"edge-z.lab": {"url": "http://z:8420", "token": "z"}}',
    )
    regs = load_registries(config_dir=tmp_path)
    assert regs["edge-b.lab"] == Registry("edge-b.lab", "http://new:8420", "env-tok")
    assert regs["edge-z.lab"].host == "edge-z.lab"


def test_registries_file_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    alt = tmp_path / "alt.yaml"
    alt.write_text("registries:\n  edge-x.lab:\n    url: http://x:8420\n    token: xt\n")
    monkeypatch.setenv("CVCPKG_REGISTRIES_FILE", str(alt))
    regs = load_registries(config_dir=tmp_path)  # config_dir ignored when file env set
    assert regs["edge-x.lab"] == Registry("edge-x.lab", "http://x:8420", "xt")


def test_registry_for_is_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)
    _write(tmp_path, "registries:\n  edge-b.lab:\n    url: http://edge-b.lab:8420\n    token: t\n")
    assert registry_for("edge-b.lab", config_dir=tmp_path) is not None
    # A host that is not configured is not allowlisted.
    assert registry_for("evil.example", config_dir=tmp_path) is None
    assert registry_for("", config_dir=tmp_path) is None


def test_empty_when_no_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)
    assert load_registries(config_dir=tmp_path) == {}
