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
    # tomllib is 3.11+ stdlib; import it lazily so this module still imports
    # under Python 3.10 (the classifier is unit-tested there — only lock parsing,
    # which is never exercised on 3.10, needs it).
    import tomllib

    with open(lock_path, "rb") as fh:
        lock = tomllib.load(fh)
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


def classify(wheels: list[dict], interps: list[str]) -> str:
    """Classify a package by its wheel set:

    * ``pure`` — a ``*-none-any.whl`` (py3-none-any, …) exists: platform- and
      interpreter-independent. One recipe, noarch, fanned into every
      interpreter's site-packages.
    * ``abi3`` — no pure wheel, but the extension ships stable-ABI (``abi3``)
      wheels and no per-version ``cpNN-cpNN`` build: one binary serves every
      interpreter from its floor upward. One recipe, copy-fanned like noarch
      (minus free-threaded).
    * ``cext`` — a distinct ``cpNN-cpNN`` binary per interpreter: one recipe
      carrying every ABI's wheel, each installed into its own interpreter
      (binary fan-out).
    """
    if any(w["filename"].endswith("none-any.whl") for w in wheels):
        return "pure"
    return "abi3" if _is_abi3(wheels, interps) else "cext"


def _is_abi3(wheels: list[dict], interps: list[str]) -> bool:
    """True when, for the interpreters we target, the extension is stable-ABI
    (abi3) rather than one build per version.  Judged on linux-x86_64: an
    ``abi3`` wheel present and no exact non-free-threaded ``cpNN-cpNN`` wheel for
    any target interpreter.

    Many stable-ABI packages *also* ship a per-version wheel for the free-
    threaded build (``cp313-cp313t``) and for newer Pythons (``cp314``) — those
    don't make the package per-version for *our* targets, so they're ignored."""
    lx = [w["filename"] for w in wheels if wheel_matches_platform(w["filename"], "linux-x86_64")]
    if not lx:
        return False
    has_abi3 = any("-abi3-" in fn for fn in lx)
    has_perver = any(re.search(rf"-cp{i}-cp{i}-", fn) for fn in lx for i in interps)
    return has_abi3 and not has_perver


def pure_wheel(wheels: list[dict]) -> dict:
    return next(w for w in wheels if w["filename"].endswith("none-any.whl"))


def abi3_wheel_for(wheels: list[dict], platform: str) -> dict | None:
    """The stable-ABI (abi3) wheel for *platform* (manylinux over musllinux)."""
    cands = [
        w
        for w in wheels
        if "-abi3-" in w["filename"] and wheel_matches_platform(w["filename"], platform)
    ]
    cands.sort(key=lambda w: ("musllinux" in w["filename"]))
    return cands[0] if cands else None


def cext_wheel_for(wheels: list[dict], interp: str, platform: str) -> dict | None:
    """Exact ``cpNN-cpNN`` per-version wheel for an interpreter+platform
    (manylinux over musllinux).  Stable-ABI packages are handled separately by
    abi3_wheel_for, so this deliberately does not fall back to abi3."""
    cp = f"cp{interp}"
    cands = [
        w
        for w in wheels
        if re.search(rf"-{cp}-{cp}-", w["filename"])  # exact, non-free-threaded
        and wheel_matches_platform(w["filename"], platform)
    ]
    cands.sort(key=lambda w: ("musllinux" in w["filename"]))
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
        meta[base] = {**info, "wheels": wheels, "kind": classify(wheels, interps), "license": lic}

    # Every package now maps to a single recipe named after its base (pure,
    # abi3, and per-version C-ext alike), so a dependency is just its bare name
    # when it is in the runtime closure.
    counts = {"pure": 0, "abi3": 0, "cext": 0}
    for base, m in sorted(meta.items()):
        dep_bases = [
            norm(d) for d, spec in m["deps"].items() if marker_ok(spec) and norm(d) in meta
        ]
        kind = m["kind"]
        if kind == "pure":
            _emit_pure(out, base, m, dep_bases, interps)
        elif kind == "abi3":
            _emit_abi3(out, base, m, dep_bases)
        else:
            _emit_cext_fanout(out, base, m, dep_bases, interps)
        counts[kind] += 1
    print(
        f"emitted: {counts['pure']} pure-python, {counts['abi3']} abi3, "
        f"{counts['cext']} per-version C-ext (fan-out)",
        file=sys.stderr,
    )
    _prune_stale_cpnn(out, set(meta), interps)
    return 0


def _prune_stale_cpnn(out: Path, bases: set[str], interps: list[str]) -> None:
    """Delete ``<base>-cpNN`` dirs superseded by a single-name recipe.

    Only bases in the current closure are touched, so unrelated per-interpreter
    recipes from other efforts (numpy, vtk-python, wand) are left alone.
    """
    import shutil

    removed = 0
    for base in bases:
        for i in interps:
            d = out / f"{base}-cp{i}"
            if d.is_dir():
                shutil.rmtree(d)
                removed += 1
    if removed:
        print(f"pruned {removed} stale -cpNN recipe dir(s)", file=sys.stderr)


def _common_meta(base: str, m: dict) -> str:
    return (
        f"schema_version: 1\n"
        f"recipe:\n"
        f"  name: {{name}}\n"
        f'  upstream_version: "{semver(m["version"])}"\n'
        f"  cvc_revision: {{rev}}\n\n"
        f'  maintainer: "cvcpkg group"\n'
        f'  maintainer_email: "info@cvcpkg.org"\n'
        f'  maintainer_url: "https://cvcpkg.org"\n'
        f'  homepage: https://pypi.org/project/{m["pypi_name"]}/\n'
        f'  license: "{m.get("license") or "NOASSERTION"}"\n'
        f"  tags: [python, wheel]\n"
        f"  description: >-\n"
        f'    {m["pypi_name"]} {m["version"]} — cvcpkg-provisioned Python dependency\n'
        f"    of the cvcpkg server (generated by tools/gen_python_recipes.py).\n"
    )


def _artifact_block(url: str, sha: str, indent: str) -> str:
    return f'{indent}url: {url}\n{indent}sha256: "{sha}"\n'


def _write_recipe(recipe_dir: Path, body_with_rev: str) -> None:
    """Write recipe.yaml, preserving ``cvc_revision`` idempotently.

    *body_with_rev* carries a literal ``{rev}`` where the revision goes.  A
    brand-new recipe starts at 1; an unchanged one keeps its revision (so a full
    re-run is a no-op); a changed one bumps by one.  This stops regeneration from
    resetting the revisions that drive republish (e.g. #389's noarch bump)."""
    path = recipe_dir / "recipe.yaml"
    old = path.read_text() if path.exists() else ""
    m = re.search(r"cvc_revision:\s*(\d+)", old)
    old_rev = int(m.group(1)) if m else 1
    old_norm = re.sub(r"cvc_revision:\s*\d+", "cvc_revision: {rev}", old)
    if not old:
        rev = 1
    elif old_norm == body_with_rev:
        rev = old_rev
    else:
        rev = old_rev + 1
    recipe_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(body_with_rev.replace("{rev}", str(rev)))


def _emit_pure(out, base, m, dep_bases, interps):
    d = out / base
    wheel = pure_wheel(m["wheels"])
    runtime = ["python312", *dep_bases]
    body = _common_meta(base, m).replace("{name}", base)
    body += (
        "\nsource:\n  type: python_wheel\n  artifacts:\n    any:\n"
        + _artifact_block(wheel["url"], wheel["digests"]["sha256"], "      ")
        + "\npython:\n  interpreter: python312\n  abi: cp312\n\npatches: []\n\n"
        "depends:\n  build:\n    - name: python312\n  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(runtime))
        + "\nbuild:\n  build_type_independent: true\n  matrix:\n"
        "    - platform: any\n      script: build.sh\n\n"
        # A noarch (py3-none-any) wheel is fanned out into every cvcpkg
        # interpreter's site-packages by _common/python-wheel.sh (so python3.11/
        # 3.13/... can import it), so the file globs span all versions, not just
        # the build interpreter's 3.12.
        "package:\n  files:\n"
        f"    - lib/python3.*/site-packages/{_toppkg(base)}/\n"
        f"    - lib/python3.*/site-packages/*.dist-info/\n"
    )
    _write_recipe(d, body)
    (d / "build.sh").write_text(_PURE_BUILD_SH.format(name=base, top=_toppkg(base)))
    (d / "build.sh").chmod(0o755)


def _emit_abi3(out, base, m, dep_bases):
    """A stable-ABI (abi3) extension: one recipe, one binary wheel per platform,
    copy-fanned into every non-free-threaded interpreter's site-packages exactly
    like a noarch wheel (the stable ABI is valid on 3.11+ but not free-threaded).
    """
    arts = {p: w for p in PLATFORM_TAGS if (w := abi3_wheel_for(m["wheels"], p))}
    if not arts:
        print(f"  WARN {base}: no abi3 wheels for any platform", file=sys.stderr)
        return
    d = out / base
    top = _toppkg(base)
    # Built under the floor interpreter (python311); _build_env copy-fans the
    # stable-ABI .so up into 3.12/3.13 (see _PYTHON_ABI3_FANOUT_VERSIONS).
    runtime = ["python311", *dep_bases]
    body = _common_meta(base, m).replace("{name}", base)
    body += "\nsource:\n  type: python_wheel\n  artifacts:\n"
    for platform, w in arts.items():
        body += f"    {platform}:\n" + _artifact_block(w["url"], w["digests"]["sha256"], "      ")
    body += (
        "\npython:\n  interpreter: python311\n  abi: abi3\n"
        "  manylinux_min: manylinux_2_28\n\npatches: []\n\n"
        "depends:\n  build:\n    - name: python311\n  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(runtime))
        + "\nbuild:\n  build_type_independent: true\n  matrix:\n"
        "    - platform: linux\n      script: build.sh\n"
        "    - platform: macos\n      script: build.sh\n"
        "    - platform: windows\n      script: build.ps1\n\n"
        "package:\n  files:\n"
        f"    - lib/python3.*/site-packages/{top}/\n"
        f"    - lib/python3.*/site-packages/*.dist-info/\n"
        f"    - Lib/site-packages/{top}/\n"
        f"    - Lib/site-packages/*.dist-info/\n"
    )
    _write_recipe(d, body)
    # abi3 installs one wheel + copy-fanout, same build flow as a pure recipe.
    (d / "build.sh").write_text(_PURE_BUILD_SH.format(name=base, top=top))
    (d / "build.sh").chmod(0o755)
    (d / "build.ps1").write_text(_CEXT_BUILD_PS1.format(name=base, top=top))


def _emit_cext_fanout(out, base, m, dep_bases, interps):
    """A true per-version extension: one recipe carrying every interpreter's
    ``cpNN-cpNN`` wheel.  The primary ``{platform}`` artifact is the build
    interpreter's wheel (for pack/publish identity); each extra interpreter's
    wheel is a ``{platform}-cpNN`` sibling.  The build installs each into its own
    interpreter's site-packages (cvc_pip_install_wheels_fanout)."""
    primary = "312" if "312" in interps else interps[0]
    # platform -> {interp: wheel}
    per_platform: dict[str, dict[str, dict]] = {}
    for platform in PLATFORM_TAGS:
        got = {i: w for i in interps if (w := cext_wheel_for(m["wheels"], i, platform))}
        if got:
            per_platform[platform] = got
    if not per_platform:
        print(f"  WARN {base}: no per-version cpNN wheels for any platform", file=sys.stderr)
        return
    # Interpreters we actually carry a wheel for anywhere -> build deps.
    carried = [i for i in interps if any(i in g for g in per_platform.values())]
    d = out / base
    top = _toppkg(base)
    runtime = [f"python{primary}", *dep_bases]
    body = _common_meta(base, m).replace("{name}", base)
    body += "\nsource:\n  type: python_wheel\n  artifacts:\n"
    for platform, got in per_platform.items():
        plat_primary = primary if primary in got else next(iter(got))
        # Primary interpreter under the bare key; extras under {platform}-cpNN.
        w = got[plat_primary]
        body += f"    {platform}:\n" + _artifact_block(w["url"], w["digests"]["sha256"], "      ")
        for i, w in got.items():
            if i != plat_primary:
                body += f"    {platform}-cp{i}:\n" + _artifact_block(
                    w["url"], w["digests"]["sha256"], "      "
                )
    body += (
        f"\npython:\n  interpreter: python{primary}\n  abi: cp{primary}\n"
        "  manylinux_min: manylinux_2_28\n\npatches: []\n\n"
        "depends:\n  build:\n"
        + "".join(f"    - name: python{i}\n" for i in carried)
        + "  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(runtime))
        + "\nbuild:\n  build_type_independent: true\n  matrix:\n"
        "    - platform: linux\n      script: build.sh\n"
        "    - platform: macos\n      script: build.sh\n"
        "    - platform: windows\n      script: build.ps1\n\n"
        "package:\n  files:\n"
        f"    - lib/python3.*/site-packages/{top}/\n"
        f"    - lib/python3.*/site-packages/*.dist-info/\n"
        f"    - Lib/site-packages/{top}/\n"
        f"    - Lib/site-packages/*.dist-info/\n"
    )
    _write_recipe(d, body)
    (d / "build.sh").write_text(_FANOUT_BUILD_SH.format(name=base, top=top))
    (d / "build.sh").chmod(0o755)
    (d / "build.ps1").write_text(_FANOUT_BUILD_PS1.format(name=base, top=top))


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

_CEXT_BUILD_PS1 = """# recipes/{name}/build.ps1 — install the pinned wheel (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck 'import {top}'
"""

# A per-version C-extension: install every pinned wheel present, each into the
# interpreter matching its own ABI tag, then import-check under each.
_FANOUT_BUILD_SH = """#!/usr/bin/env bash
# recipes/{name}/build.sh — install one pinned wheel per interpreter (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheels_fanout
cvc_python_check_each "import {top}"
"""

_FANOUT_BUILD_PS1 = """# recipes/{name}/build.ps1 — one wheel per interpreter (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"
Invoke-CvcPipInstallWheelsFanout
Invoke-CvcPythonCheckEach 'import {top}'
"""


if __name__ == "__main__":
    raise SystemExit(main())
