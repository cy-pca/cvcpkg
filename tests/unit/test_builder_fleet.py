"""Multi-homed builder fleet config parsing + worker argv + supervisor CLI."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cvcpkg.builder_fleet import (
    FleetConfigError,
    load_fleet_config,
    parse_fleet_config,
    worker_argv,
)


def test_served_set_helper():
    from cvcpkg.orgs import served_set

    # Home is always first; extras appended, de-duplicated; '' is public.
    assert served_set("", None) == [""]
    assert served_set("cvc", []) == ["cvc"]
    assert served_set("", ["cvc"]) == ["", "cvc"]
    assert served_set("cvc", ["", "cvc"]) == ["cvc", ""]  # home not duplicated
    assert served_set("cvc", ["cypca", "cypca"]) == ["cvc", "cypca"]


def test_parse_multi_server_with_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("TOK_PROD", "ptok")
    monkeypatch.setenv("TOK_DEV", "dtok")
    cfg = parse_fleet_config(
        {
            "name": "catx-03",
            "max_jobs": 4,
            "work_dir": "/var/lib/cvcpkg-builder",
            "labels": ["ramdisk"],
            "servers": [
                {"server": "https://cvcpkg.org/", "token_env": "TOK_PROD", "serve": ["", "cvc"]},
                {
                    "server": "https://pkg.tx.wtf",
                    "token_env": "TOK_DEV",
                    "serve": ["", "cvc"],
                    "max_jobs": 2,
                },
            ],
        }
    )
    assert cfg.name == "catx-03"
    assert [s.host for s in cfg.servers] == ["cvcpkg.org", "pkg.tx.wtf"]
    prod, dev = cfg.servers
    # Per-server name derived from fleet name + host; trailing slash stripped.
    assert prod.name == "catx-03-cvcpkg-org"
    assert prod.server == "https://cvcpkg.org"
    assert prod.token == "ptok"
    assert prod.serve == ("", "cvc")
    assert prod.max_jobs == 4  # inherits fleet default
    assert dev.max_jobs == 2  # per-server override
    # work_dir gets a per-server subdirectory; labels inherit fleet default.
    # (Path is OS-native, so compare the leaf name rather than a POSIX suffix.)
    assert Path(prod.work_dir).name == "cvcpkg-org"
    assert prod.labels == ("ramdisk",)


def test_token_literal_and_missing_env(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    cfg = parse_fleet_config({"servers": [{"server": "https://x", "token": "lit", "serve": [""]}]})
    assert cfg.servers[0].token == "lit"
    with pytest.raises(FleetConfigError, match="token_env"):
        parse_fleet_config({"servers": [{"server": "https://x", "token_env": "NOPE"}]})
    with pytest.raises(FleetConfigError, match="token"):
        parse_fleet_config({"servers": [{"server": "https://x", "serve": [""]}]})


def test_serve_normalization_and_default(monkeypatch):
    monkeypatch.setenv("T", "t")
    # string coerced to list; default is public-only; duplicates removed.
    a = parse_fleet_config({"servers": [{"server": "https://x", "token": "t", "serve": "cvc"}]})
    assert a.servers[0].serve == ("cvc",)
    b = parse_fleet_config({"servers": [{"server": "https://x", "token": "t"}]})
    assert b.servers[0].serve == ("",)
    c = parse_fleet_config(
        {"servers": [{"server": "https://x", "token": "t", "serve": ["cvc", "cvc", ""]}]}
    )
    assert c.servers[0].serve == ("cvc", "")


def test_structural_errors():
    with pytest.raises(FleetConfigError, match="non-empty 'servers'"):
        parse_fleet_config({"servers": []})
    with pytest.raises(FleetConfigError, match="missing 'server'"):
        parse_fleet_config({"servers": [{"token": "t"}]})
    with pytest.raises(FleetConfigError, match="duplicate server"):
        parse_fleet_config(
            {
                "servers": [
                    {"server": "https://x", "token": "t"},
                    {"server": "https://x/", "token": "t"},
                ]
            }
        )


def test_worker_argv_maps_served_set_to_org_and_serve():
    from cvcpkg.builder_fleet import FleetServer

    fs = FleetServer(
        server="https://cvcpkg.org",
        token="secret",
        serve=("", "cvc"),
        name="w1",
        max_jobs=3,
        work_dir="/w/prod",
        labels=("ramdisk",),
    )
    argv = worker_argv(fs)
    # serve[0] is the home --org; the rest become --serve.
    assert argv[:2] == ["builder", "run"]
    assert "--org" in argv and argv[argv.index("--org") + 1] == ""
    assert "--serve" in argv and argv[argv.index("--serve") + 1] == "cvc"
    assert argv[argv.index("--max-jobs") + 1] == "3"
    assert argv[argv.index("--work-dir") + 1] == "/w/prod"
    # pidfile is a Path join → OS-native separators; compare as paths.
    assert Path(argv[argv.index("--pidfile") + 1]) == Path("/w/prod") / "cvcpkg-builder.pid"
    assert argv[argv.index("--label") + 1] == "ramdisk"


def test_load_from_yaml_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TOK", "y")
    p = tmp_path / "fleet.yaml"
    p.write_text(
        textwrap.dedent(
            """
            name: unit-fleet
            servers:
              - server: https://a.example
                token_env: TOK
                serve: ["", "cvc"]
            """
        )
    )
    cfg = load_fleet_config(p)
    assert cfg.name == "unit-fleet"
    assert cfg.servers[0].serve == ("", "cvc")
    with pytest.raises(FleetConfigError, match="not found"):
        load_fleet_config(tmp_path / "missing.yaml")


def test_fleet_cli_dry_run_masks_token(tmp_path, monkeypatch):
    pytest.importorskip("click")
    from click.testing import CliRunner

    from cvcpkg.cli._builder import builder_fleet

    monkeypatch.setenv("TOK", "supersecret")
    p = tmp_path / "fleet.yaml"
    p.write_text(
        "name: f\nservers:\n  - server: https://a.example\n    token_env: TOK\n    serve: ['', cvc]\n"
    )
    res = CliRunner().invoke(builder_fleet, ["--config", str(p), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "supersecret" not in res.output  # token is masked
    assert "***" in res.output
    assert "a.example" in res.output
    assert "serves ['', 'cvc']" in res.output
