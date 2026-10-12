"""`license_acceptance:` -- vendor terms the user, not cvcpkg, accepts before a build.

recipes/msvc lays out Microsoft's compiler and Windows SDK from Microsoft's own
packages on the installing machine (`redistributable: false`), and Microsoft's
licenses have to be accepted to do that.  cvcpkg asks on a terminal and
otherwise refuses unless told explicitly (--accept-license NAME, or the
recipe's CVCPKG_ACCEPT_<NAME>_LICENSE=1); it never accepts on its own.  These
tests pin the recipe model and schema, the gate, `cvcpkg install`'s use of it,
the msvc recipe and its lock, and scripts/msvc_lock.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cvcpkg.builder import LicenseAcceptance, Recipe
from cvcpkg.errors import InstallError

REPO = Path(__file__).resolve().parents[2]
ENV = "CVCPKG_ACCEPT_VENDORKIT_LICENSE"
URLS = ("https://vendor.example/eula", "https://vendor.example/sdk-license")
GATE_YAML = f"""license_acceptance:
  title: "the Vendor EULA"
  urls:
    - {URLS[0]}
    - {URLS[1]}
  env: {ENV}
"""


def _write_recipe(root: Path, name: str, *, gate: str = GATE_YAML) -> Path:
    rdir = root / name
    rdir.mkdir(parents=True)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
  license: LicenseRef-Vendor
redistributable: false
{gate}source:
  type: none
build:
  matrix:
    - platform: linux
      script: build.sh
package:
  files:
    - bin/
"""
    )
    (rdir / "build.sh").write_text("#!/bin/sh\nexit 0\n")
    return rdir


def _gate() -> LicenseAcceptance:
    return LicenseAcceptance(urls=list(URLS), env=ENV, title="the Vendor EULA")


# ── recipe model and schema ─────────────────────────────────────


class TestRecipeModel:
    def test_parsed(self, tmp_path):
        gate = Recipe.load(_write_recipe(tmp_path, "vendorkit")).license_acceptance
        assert gate == _gate()

    def test_absent_means_no_gate(self, tmp_path):
        assert Recipe.load(_write_recipe(tmp_path, "zlib", gate="")).license_acceptance is None

    def test_schema(self, tmp_path):
        from cvcpkg.validation import validate_recipe_dir

        assert validate_recipe_dir(_write_recipe(tmp_path, "ok")) == []
        for i, bad in enumerate(
            (
                GATE_YAML.replace(ENV, "ACCEPT_IT"),  # env must be CVCPKG_ACCEPT_*_LICENSE
                GATE_YAML.replace("https://vendor.example/eula", "http://vendor.example/eula"),
                "license_acceptance:\n  urls: []\n  env: CVCPKG_ACCEPT_X_LICENSE\n",
                "license_acceptance:\n  env: CVCPKG_ACCEPT_X_LICENSE\n",
            )
        ):
            errors = validate_recipe_dir(_write_recipe(tmp_path, f"bad{i}", gate=bad))
            assert any("license_acceptance" in e for e in errors), (bad, errors)


# ── the gate ────────────────────────────────────────────────────


class TestRequireLicenseAcceptance:
    def _require(self, **kw):
        from cvcpkg.installer import require_license_acceptance

        environ = kw.pop("environ", {})
        return require_license_acceptance("vendorkit", _gate(), environ=environ, **kw), environ

    @pytest.mark.parametrize("value", ["1", "yes", "Y", "true", " 1 "])
    def test_env_var(self, value):
        how, env = self._require(environ={ENV: value})
        assert how == "env" and env[ENV] == "1"

    @pytest.mark.parametrize("value", ["", "0", "no", "false"])
    def test_env_var_that_does_not_say_yes(self, value):
        with pytest.raises(InstallError):
            self._require(environ={ENV: value})

    def test_flag_records_it_for_the_build(self):
        how, env = self._require(accepted=("vendorkit",))
        assert how == "flag" and env == {ENV: "1"}

    def test_flag_for_another_component_does_not_count(self):
        with pytest.raises(InstallError):
            self._require(accepted=("otherkit",))

    def test_prompt_yes(self):
        asked: list[str] = []
        how, env = self._require(interactive=True, confirm=lambda q: asked.append(q) or True)
        assert how == "prompt" and env == {ENV: "1"}
        assert all(u in asked[0] for u in URLS) and "the Vendor EULA" in asked[0]

    def test_prompt_no(self):
        env: dict[str, str] = {}
        with pytest.raises(InstallError, match="not accepted"):
            self._require(interactive=True, confirm=lambda q: False, environ=env)
        assert env == {}

    def test_never_asks_without_a_terminal(self):
        def _ask(q):
            raise AssertionError("prompted without a terminal")

        env: dict[str, str] = {}
        with pytest.raises(InstallError) as exc:
            self._require(interactive=False, confirm=_ask, environ=env)
        msg = str(exc.value)
        assert all(u in msg for u in URLS)
        assert "--accept-license vendorkit" in msg and f"{ENV}=1" in msg
        assert env == {}

    def test_license_gated_components(self, tmp_path):
        from cvcpkg.installer import license_gated_components

        root = tmp_path / "recipes"
        _write_recipe(root, "vendorkit")
        _write_recipe(root, "zlib", gate="")
        assert license_gated_components(["zlib", "vendorkit", "missing"], [root]) == [
            ("vendorkit", _gate())
        ]


# ── cvcpkg install ──────────────────────────────────────────────


def _catalog(tmp_path: Path) -> Path:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "revision": 1,
                "bundles": [
                    {
                        "name": "zlib",
                        "version": "1.3.1+cvc.1",
                        "platform": "linux",
                        "arch": "x86_64",
                        "build_type": "release",
                        "link": "shared",
                        "sha256": "abc",
                        "size_bytes": 1,
                        "archive_url": "https://cvcpkg.org/a/zlib.tar.gz",
                    }
                ],
            }
        )
    )
    return catalog


class TestInstallGate:
    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        """Invoke `cvcpkg install vendorkit ...`; returns (result, builds) where
        builds records the gate's env var as each build saw it."""
        from click.testing import CliRunner

        from cvcpkg import installer
        from cvcpkg.cli import cli

        # Restored afterwards; the gate sets it in os.environ on acceptance.
        monkeypatch.setenv(ENV, "")
        root = tmp_path / "recipes"
        _write_recipe(root, "vendorkit")
        catalog = _catalog(tmp_path)
        builds: list[tuple[str, str]] = []

        def _build(name, *a, **kw):
            import os

            builds.append((name, os.environ.get(ENV, "")))

        monkeypatch.setattr(installer, "build_from_source_fallback", _build)

        def _run(*extra: str, input: str | None = None, env: dict | None = None):
            res = CliRunner().invoke(
                cli,
                [
                    "install",
                    "vendorkit",
                    "--catalog",
                    str(catalog),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                    "--recipes-dir",
                    str(root),
                    "--no-default-recipes",
                    "--prefix",
                    str(tmp_path / "prefix"),
                    *extra,
                ],
                input=input,
                env=env,
            )
            return res, builds

        return _run

    def test_refuses_without_acceptance(self, run):
        res, builds = run()
        assert res.exit_code != 0
        assert "--accept-license vendorkit" in res.output and URLS[0] in res.output
        assert builds == []

    def test_accept_license_flag(self, run):
        res, builds = run("--accept-license", "vendorkit")
        assert res.exit_code == 0, res.output
        assert builds == [("vendorkit", "1")]
        assert "license accepted (--accept-license)" in res.output

    def test_env_var(self, run):
        res, builds = run(env={ENV: "1"})
        assert res.exit_code == 0, res.output
        assert builds == [("vendorkit", "1")]

    def test_prompt_on_a_terminal(self, run, monkeypatch):
        from cvcpkg.cli import _install

        tty = SimpleNamespace(isatty=lambda: True)
        monkeypatch.setattr(_install, "sys", SimpleNamespace(stdin=tty, stdout=tty))
        res, builds = run(input="n\n")
        assert res.exit_code != 0 and "not accepted" in res.output and builds == []
        res, builds = run(input="y\n")
        assert res.exit_code == 0, res.output
        assert URLS[1] in res.output and builds == [("vendorkit", "1")]

    def test_install_deps_takes_the_flag(self):
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        for cmd in ("install", "install-deps"):
            res = CliRunner().invoke(cli, [cmd, "--help"])
            assert "--accept-license" in res.output, cmd


# ── the msvc recipe and its lock ────────────────────────────────

_MSVC = REPO / "recipes" / "msvc"
_PAYLOAD = re.compile(
    r"^(?P<kind>vsix|msi|cab) (?P<sha>[0-9a-f]{64}) (?P<size>\d+) (?P<url>https://\S+) (?P<name>.+)$"
)


def _msvc_lock() -> tuple[dict[str, list[str]], list[dict]]:
    data: dict[str, list[str]] = {}
    rows: list[dict] = []
    for line in (_MSVC / "msvc.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        m = _PAYLOAD.match(line)
        if m:
            rows.append(m.groupdict())
            continue
        key, *values = line.split()
        assert key in ("release", "license", "toolset", "redist", "sdk"), line
        data.setdefault(key, []).extend(values)
    return data, rows


class TestMsvcRecipe:
    def test_recipe_validates_and_is_gated(self):
        from cvcpkg.validation import validate_recipe_dir

        assert validate_recipe_dir(_MSVC) == []
        recipe = Recipe.load(_MSVC)
        assert recipe.redistributable is False
        assert recipe.license_acceptance is not None
        assert recipe.license_acceptance.env == "CVCPKG_ACCEPT_MSVC_LICENSE"
        assert [m.platform for m in recipe.build_matrix] == ["windows"]

    def test_lock_matches_the_recipe(self):
        recipe = Recipe.load(_MSVC)
        data, rows = _msvc_lock()
        assert data["toolset"][0] == recipe.upstream_version
        assert data["license"] == recipe.license_acceptance.urls
        kinds = {k: [r for r in rows if r["kind"] == k] for k in ("vsix", "msi", "cab")}
        assert all(kinds.values())
        toolset = ".".join(recipe.upstream_version.split(".")[:2])
        assert all(r["name"].startswith(f"Microsoft.VC.{toolset}.") for r in kinds["vsix"])
        names = {r["name"] for r in kinds["vsix"]}
        assert any(n.endswith(".Tools.HostX64.TargetX64.base") for n in names)
        assert any(n.endswith(".CRT.x64.Store.base") for n in names)  # the /MD libs
        assert all(r["name"].endswith(".msi") for r in kinds["msi"])
        assert all(re.fullmatch(r"[0-9a-fA-F]{32}\.cab", r["name"]) for r in kinds["cab"])
        assert all(
            r["url"].startswith("https://download.visualstudio.microsoft.com/") for r in rows
        )
        assert len({r["sha"] for r in rows}) == len(rows)
        header = (_MSVC / "msvc.lock").read_text()
        for art in recipe.source.artifacts.values():
            assert art["url"] in header and art["sha256"] in header

    def test_build_script_needs_no_compiler_and_checks_the_gate(self):
        script = (_MSVC / "build.ps1").read_text()
        # env-windows.ps1 throws when no MSVC is installed: the one case this
        # recipe exists for.
        assert not re.search(r"^\s*\.\s.*env-windows", script, re.M)
        assert "CVCPKG_ACCEPT_MSVC_LICENSE" in script


# ── scripts/msvc_lock.py ────────────────────────────────────────


@pytest.fixture
def lockgen():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import msvc_lock

        yield msvc_lock
    finally:
        sys.path.remove(str(REPO / "scripts"))


def _vsix(paths: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", "{}")
        for p in paths:
            z.writestr(p, b"x")
    return buf.getvalue()


def _fake_vs(lockgen, *, drop: str = "", extra_stem: str = ""):
    """A channel manifest plus a fetch() serving its vsman and payloads."""
    blobs: dict[str, bytes] = {}

    def payload(url: str, data: bytes, name: str) -> dict:
        blobs[url] = data
        return {
            "fileName": name,
            "url": url,
            "sha256": hashlib.sha256(data).hexdigest().upper(),
            "size": len(data),
        }

    stem = "Microsoft.VC.14.44.17.14"
    packages = []
    for suffix in lockgen.MSVC_PACKAGES:
        if suffix == drop:
            continue
        if suffix == "CRT.Headers.base":
            data = _vsix(["Contents/VC/Tools/MSVC/14.44.35207/include/vector"])
        elif suffix == "CRT.Redist.X64.base":
            data = _vsix(["Contents/VC/Redist/MSVC/14.44.35112/x64/a.dll"])
        else:
            data = suffix.encode()
        pid = f"{stem}.{suffix}"
        packages.append(
            {
                "id": pid,
                "version": "14.44.35229",
                "language": "en-US" if suffix.endswith("Res.base") else None,
                "payloads": [
                    payload(f"https://download.visualstudio.microsoft.com/{pid}", data, "p.vsix")
                ],
            }
        )
    if extra_stem:
        packages.append(
            {"id": f"{extra_stem}.Tools.HostX64.TargetX64.base", "version": "1", "payloads": []}
        )
    sdk_payloads = []
    cab = "0123456789abcdef0123456789abcdef.cab"
    for msi in lockgen.SDK_MSIS:
        sdk_payloads.append(
            payload(
                f"https://download.visualstudio.microsoft.com/{msi}",
                b"..Media.." + cab.encode(),
                f"Installers\\{msi}",
            )
        )
    sdk_payloads.append(
        payload(f"https://download.visualstudio.microsoft.com/{cab}", b"cab", f"Installers\\{cab}")
    )
    packages.append(
        {"id": "Win11SDK_10.0.26100", "version": "10.0.26100.15", "payloads": sdk_payloads}
    )
    vsman = json.dumps({"packages": packages}).encode()
    channel = {
        "info": {
            "manifestName": "VisualStudio.17.Release",
            "productDisplayVersion": "17.14.41 (x)",
            "buildVersion": "17.14.1",
        },
        "channelItems": [
            {
                "id": "Microsoft.VisualStudio.Manifests.VisualStudio",
                # Deliberately not the served file's hash: see render().
                "payloads": [
                    {"url": "https://vs.example/VisualStudio.vsman", "sha256": "00" * 32, "size": 1}
                ],
            },
            {
                "id": "Microsoft.VisualStudio.Product.BuildTools",
                "localizedResources": [
                    {"language": "en-us", "license": "https://vs.example/license"}
                ],
            },
        ],
    }
    blobs["https://vs.example/VisualStudio.vsman"] = vsman
    return json.dumps(channel).encode(), blobs.__getitem__


class TestMsvcLockScript:
    def test_msi_cabs(self, lockgen):
        msi = (
            b"\x00Media\x00" + b"A" * 32 + b".cab" + b"\x00" + b"f" * 32 + b".cab" + b"notacab.cab"
        )
        assert lockgen.msi_cabs(msi) == ["A" * 32 + ".cab", "f" * 32 + ".cab"]

    def test_vsix_dirs(self, lockgen):
        z = _vsix(
            ["Contents/VC/Tools/MSVC/14.44.1/a", "Contents/VC/Tools/MSVC/14.44.1/b/c", "other/x"]
        )
        assert lockgen.vsix_dirs(z, "VC/Tools/MSVC") == {"14.44.1"}

    def test_render(self, lockgen):
        channel, fetch = _fake_vs(lockgen)
        text = lockgen.render(
            "14.44", "26100", channel, fetch=fetch, channel_url="https://vs.example/channel"
        )
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert lines[:6] == [
            "release 17.14.41 17.14.1",
            "license https://vs.example/license",
            f"license {lockgen.SDK_LICENSE_URL}",
            "toolset 14.44.35207 14.44.35229",
            "redist 14.44.35112",
            "sdk 10.0.26100.0 10.0.26100.15",
        ]
        payloads = [_PAYLOAD.match(ln) for ln in lines[6:]]
        assert all(payloads)
        kinds = [m["kind"] for m in payloads]
        assert kinds.count("vsix") == len(lockgen.MSVC_PACKAGES)
        assert kinds.count("msi") == len(lockgen.SDK_MSIS)
        assert kinds.count("cab") == 1
        assert all(m["sha"] == m["sha"].lower() for m in payloads)
        assert "# Channel: https://vs.example/channel" in text

    def test_missing_package(self, lockgen):
        channel, fetch = _fake_vs(lockgen, drop="CRT.x64.Store.base")
        with pytest.raises(SystemExit, match="CRT.x64.Store.base"):
            lockgen.render("14.44", "26100", channel, fetch=fetch)

    def test_ambiguous_toolset(self, lockgen):
        channel, fetch = _fake_vs(lockgen, extra_stem="Microsoft.VC.14.44.17.13")
        with pytest.raises(SystemExit, match="expected one compiler package"):
            lockgen.render("14.44", "26100", channel, fetch=fetch)

    def test_payload_checksum_is_enforced(self, lockgen):
        channel, fetch = _fake_vs(lockgen)

        def _tampered(url: str) -> bytes:
            data = fetch(url)
            return data + b"!" if url.endswith(".msi") else data

        with pytest.raises(SystemExit, match="sha256"):
            lockgen.render("14.44", "26100", channel, fetch=_tampered)
