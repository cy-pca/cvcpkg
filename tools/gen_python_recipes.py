#!/usr/bin/env python3
"""Generate cvcpkg recipes for the cvcpkg server's Python dependency closure.

Reads poetry.lock (main/runtime group only), resolves each package's wheels
from PyPI at the LOCKED version, and emits cvcpkg recipes:

  * pure-Python (``py3-none-any`` wheel) -> one recipe ``<name>``,
    ``platform: any`` (arch noarch), a single ``any`` artifact.
  * C-extension (per-platform ``cpNN`` wheels) -> recipes
    ``<name>-cp311 / -cp312 / -cp313``, each with per-platform artifacts,
    mirroring the numpy-cpNNN recipes.

Dependencies are mapped to the correct recipe name(s) (a pure-Python dep keeps
its bare name; a C-extension dep gets the consuming interpreter's suffix).

Reproducible: re-run after a `poetry lock` bump. Usage:
    python tools/gen_python_recipes.py [--out recipes] [--interpreters 311,312,313]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

# cvcpkg's five concrete build platforms and the wheel-tag fragments that map to
# each. First match wins.
PLATFORM_TAGS = {
    "linux-x86_64": ["manylinux", "musllinux"],  # + must contain x86_64
    "linux-arm64": ["manylinux", "musllinux"],  # + aarch64
    "macos-x86_64": ["macosx"],  # + x86_64
    "macos-arm64": ["macosx"],  # + arm64
    "windows-x86_64": ["win_amd64", "win-amd64"],
}
INTERPRETERS = ["311", "312", "313"]
PYPI = "https://pypi.org/pypi/{name}/{version}/json"


def norm(name: str) -> str:
    """PyPI-normalize a distribution name -> cvcpkg recipe base name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def semver(version: str) -> str:
    """The orderable numeric prefix of a PEP440 version for upstream_version.
    e.g. '2.9.0.post0' -> '2.9.0', '1.0.0rc1' -> '1.0.0'. The pinned wheel URL
    (the exact locked build) is unaffected — this is just the display version."""
    return re.match(r"\d+(?:\.\d+)*", version).group(0)


def wheel_matches_platform(fn: str, platform: str) -> bool:
    """True if wheel filename *fn* targets cvcpkg *platform*."""
    lo = fn.lower()
    if platform == "linux-x86_64":
        return ("manylinux" in lo or "musllinux" in lo) and "x86_64" in lo
    if platform == "linux-arm64":
        return ("manylinux" in lo or "musllinux" in lo) and ("aarch64" in lo or "arm64" in lo)
    if platform == "macos-x86_64":
        return "macosx" in lo and "x86_64" in lo
    if platform == "macos-arm64":
        return "macosx" in lo and "arm64" in lo
    if platform == "windows-x86_64":
        return "win_amd64" in lo or "win-amd64" in lo
    return False


def fetch_pypi(name: str, version: str) -> tuple[list[dict], str]:
    url = PYPI.format(name=name, version=version)
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - pinned host
        data = json.load(r)
    wheels = [f for f in data.get("urls", []) if f["filename"].endswith(".whl")]
    return wheels, _spdx(data.get("info", {}))


# Authoritative overrides where PyPI metadata is absent/ambiguous (NOASSERTION
# or a non-SPDX raw string). Keyed by normalized recipe base name.
_LICENSE_OVERRIDE = {
    "azure-core": "MIT",
    "azure-identity": "MIT",
    "azure-storage-blob": "MIT",
    "google-crc32c": "Apache-2.0",
    "protobuf": "BSD-3-Clause",
    "paramiko": "LGPL-2.1-or-later",
}


def _spdx(info: dict) -> str:
    """Best-effort SPDX-ish license from PyPI metadata."""
    lic = (info.get("license_expression") or "").strip()
    if lic:
        return lic
    for c in info.get("classifiers", []):
        if c.startswith("License :: OSI Approved :: "):
            tail = c.rsplit("::", 1)[-1].strip()
            m = {
                "MIT License": "MIT",
                "BSD License": "BSD-3-Clause",
                "Apache Software License": "Apache-2.0",
                "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
                "Python Software Foundation License": "PSF-2.0",
                "ISC License (ISCL)": "ISC",
                "The Unlicense (Unlicense)": "Unlicense",
                "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
            }
            if tail in m:
                return m[tail]
    lic = (info.get("license") or "").strip().splitlines()[0] if info.get("license") else ""
    return lic[:60] or "NOASSERTION"


def marker_ok(dep_spec) -> bool:
    """Keep a dependency unless its marker excludes every interpreter we target
    (e.g. tomli's `python_version < "3.11"`)."""
    if not isinstance(dep_spec, dict):
        return True
    marker = dep_spec.get("markers", "")
    if 'python_version < "3.11"' in marker or 'python_version < "3.10"' in marker:
        return False
    return True


def load_runtime_packages(lock_path: Path) -> dict[str, dict]:
    lock = tomllib.load(open(lock_path, "rb"))
    out = {}
    for p in lock["package"]:
        groups = p.get("groups") or ["main"]
        if "main" not in groups:
            continue  # dev-only
        out[norm(p["name"])] = {
            "pypi_name": p["name"],
            "version": p["version"],
            "deps": p.get("dependencies") or {},
        }
    return out


def classify(wheels: list[dict]) -> str:
    """'pure' if any platform-independent wheel exists (abi none, platform any),
    else 'cext'. A ``*-none-any.whl`` (py3-none-any, py2.py3-none-any, …) is the
    definitive pure-Python marker."""
    return "pure" if any(w["filename"].endswith("none-any.whl") for w in wheels) else "cext"


def pure_wheel(wheels: list[dict]) -> dict:
    return next(w for w in wheels if w["filename"].endswith("none-any.whl"))


def cext_wheel_for(wheels: list[dict], interp: str, platform: str) -> dict | None:
    """Best cpNN wheel for an interpreter+platform (prefer abi cpNN, allow abi3)."""
    cp = f"cp{interp}"
    cands = [
        w
        for w in wheels
        if (cp in w["filename"] or "abi3" in w["filename"])
        and wheel_matches_platform(w["filename"], platform)
    ]
    # Prefer an exact cpNN abi over abi3, and manylinux over musllinux.
    cands.sort(key=lambda w: (("abi3" in w["filename"]), ("musllinux" in w["filename"])))
    return cands[0] if cands else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default="poetry.lock")
    ap.add_argument("--out", default="recipes")
    ap.add_argument("--interpreters", default=",".join(INTERPRETERS))
    args = ap.parse_args()
    interps = args.interpreters.split(",")
    out = Path(args.out)

    pkgs = load_runtime_packages(Path(args.lock))
    print(f"runtime packages: {len(pkgs)}", file=sys.stderr)

    # Pass 1: fetch wheels + classify every package (needed for dep-name mapping).
    meta: dict[str, dict] = {}
    for base, info in sorted(pkgs.items()):
        try:
            wheels, lic = fetch_pypi(info["pypi_name"], info["version"])
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {base} {info['version']}: PyPI fetch failed: {e}", file=sys.stderr)
            continue
        if not wheels:
            print(f"  SKIP {base}: no wheels (sdist-only) at {info['version']}", file=sys.stderr)
            continue
        lic = _LICENSE_OVERRIDE.get(base, lic)
        meta[base] = {**info, "wheels": wheels, "kind": classify(wheels), "license": lic}

    def dep_recipe_names(dep_base: str, consuming_interp: str | None) -> list[str]:
        """Map a dependency base name to its recipe name(s)."""
        if dep_base not in meta:
            return []  # not in the runtime closure (optional/extra/marker-excluded)
        if meta[dep_base]["kind"] == "pure":
            return [dep_base]
        if consuming_interp:  # a C-ext consumer binds its own interpreter's peer
            return [f"{dep_base}-cp{consuming_interp}"]
        return [f"{dep_base}-cp{i}" for i in interps]

    counts = {"pure": 0, "cext": 0}
    for base, m in sorted(meta.items()):
        dep_bases = [
            norm(d) for d, spec in m["deps"].items() if marker_ok(spec) and norm(d) in meta
        ]
        if m["kind"] == "pure":
            _emit_pure(out, base, m, dep_bases, dep_recipe_names, interps)
            counts["pure"] += 1
        else:
            for i in interps:
                _emit_cext(out, base, m, i, dep_bases, dep_recipe_names)
            counts["cext"] += 1
    print(f"emitted: {counts['pure']} pure-python, {counts['cext']} C-ext (x{len(interps)})",
          file=sys.stderr)
    return 0


def _common_meta(base: str, m: dict) -> str:
    return (
        f'schema_version: 1\n'
        f'recipe:\n'
        f'  name: {{name}}\n'
        f'  upstream_version: "{semver(m["version"])}"\n'
        f'  cvc_revision: 1\n'
        f'  maintainer: "cvcpkg group"\n'
        f'  maintainer_email: "info@cvcpkg.org"\n'
        f'  maintainer_url: "https://cvcpkg.org"\n'
        f'  homepage: https://pypi.org/project/{m["pypi_name"]}/\n'
        f'  license: "{m.get("license") or "NOASSERTION"}"\n'
        f'  tags: [python, wheel]\n'
        f'  description: >-\n'
        f'    {m["pypi_name"]} {m["version"]} — cvcpkg-provisioned Python dependency\n'
        f'    of the cvcpkg server (generated by tools/gen_python_recipes.py).\n'
    )


def _artifact_block(url: str, sha: str, indent: str) -> str:
    return f'{indent}url: {url}\n{indent}sha256: "{sha}"\n'


def _emit_pure(out, base, m, dep_bases, dep_names, interps):
    d = out / base
    d.mkdir(parents=True, exist_ok=True)
    wheel = pure_wheel(m["wheels"])
    runtime = ["python312"]
    for db in dep_bases:
        runtime += dep_names(db, None)
    body = _common_meta(base, m).replace("{name}", base)
    body += (
        "\nsource:\n  type: python_wheel\n  artifacts:\n    any:\n"
        + _artifact_block(wheel["url"], wheel["digests"]["sha256"], "      ")
        + "\npython:\n  interpreter: python312\n  abi: cp312\n\npatches: []\n\n"
        "depends:\n  build:\n    - name: python312\n  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(runtime))
        + "\nbuild:\n  build_type_independent: true\n  matrix:\n"
        "    - platform: any\n      script: build.sh\n\n"
        "package:\n  files:\n"
        f"    - lib/python3.12*/site-packages/{_toppkg(base)}/\n"
        f"    - lib/python3.12*/site-packages/*.dist-info/\n"
    )
    (d / "recipe.yaml").write_text(body)
    (d / "build.sh").write_text(_PURE_BUILD_SH.format(name=base, top=_toppkg(base)))
    (d / "build.sh").chmod(0o755)


def _emit_cext(out, base, m, interp, dep_bases, dep_names):
    name = f"{base}-cp{interp}"
    arts = {}
    for platform in PLATFORM_TAGS:
        w = cext_wheel_for(m["wheels"], interp, platform)
        if w:
            arts[platform] = w
    if not arts:
        print(f"  WARN {name}: no cp{interp} wheels for any platform", file=sys.stderr)
        return
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    runtime = [f"python{interp}"]
    for db in dep_bases:
        runtime += dep_names(db, interp)
    body = _common_meta(base, m).replace("{name}", name)
    body += "\nsource:\n  type: python_wheel\n  artifacts:\n"
    for platform, w in arts.items():
        body += f"    {platform}:\n" + _artifact_block(
            w["url"], w["digests"]["sha256"], "      "
        )
    pyver = f"3.{interp[1:]}" if interp.startswith("3") else f"3.{interp}"
    pyver = f"3.{interp[-2:].lstrip('0') or interp}"  # 311->11? fix below
    pyver = {"311": "3.11", "312": "3.12", "313": "3.13"}[interp]
    body += (
        f"\npython:\n  interpreter: python{interp}\n  abi: cp{interp}\n"
        "  manylinux_min: manylinux_2_28\n\npatches: []\n\n"
        f"depends:\n  build:\n    - name: python{interp}\n  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(runtime))
        + "\nbuild:\n  build_type_independent: true\n  matrix:\n"
        "    - platform: linux\n      script: build.sh\n"
        "    - platform: macos\n      script: build.sh\n"
        "    - platform: windows\n      script: build.ps1\n\n"
        "package:\n  files:\n"
        f"    - lib/python{pyver}*/site-packages/{_toppkg(base)}/\n"
        f"    - lib/python{pyver}*/site-packages/*.dist-info/\n"
        f"    - Lib/site-packages/{_toppkg(base)}/\n"
        f"    - Lib/site-packages/*.dist-info/\n"
    )
    (d / "recipe.yaml").write_text(body)
    (d / "build.sh").write_text(_CEXT_BUILD_SH.format(name=name, top=_toppkg(base)))
    (d / "build.sh").chmod(0o755)
    (d / "build.ps1").write_text(_CEXT_BUILD_PS1.format(name=name, top=_toppkg(base)))


# Import name != distribution name for a few packages.
_TOPPKG = {
    "pyyaml": "yaml",
    "sqlalchemy": "sqlalchemy",
    "mako": "mako",
    "typing-extensions": "typing_extensions",
    "python-multipart": "multipart",
    "python-dateutil": "dateutil",
    "google-cloud-storage": "google",
    "azure-storage-blob": "azure",
    "azure-identity": "azure",
    "azure-core": "azure",
    "pyjwt": "jwt",
    "google-crc32c": "google_crc32c",
    "proto-plus": "proto",
}


def _toppkg(base: str) -> str:
    return _TOPPKG.get(base, base.replace("-", "_"))


_PURE_BUILD_SH = """#!/usr/bin/env bash
# recipes/{name}/build.sh — install the pinned pure-Python wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import {top}"
"""

_CEXT_BUILD_SH = """#!/usr/bin/env bash
# recipes/{name}/build.sh — install the pinned cpNN wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import {top}"
"""

_CEXT_BUILD_PS1 = """# recipes/{name}/build.ps1 — install the pinned cpNN wheel (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck 'import {top}'
"""


if __name__ == "__main__":
    raise SystemExit(main())
