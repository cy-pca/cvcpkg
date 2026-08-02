#!/usr/bin/env python3
"""Generate per-interpreter cvcpkg recipes for Python wheel packages.

Every Python package cvcpkg publishes is a *matrix* of per-interpreter
recipes -- ``<name>-cp311 / -cp312 / -cp313 / -cp313t`` -- one column per
CPython recipe cvcpkg ships (python311, python312, python313, python313t).
A column depends on ITS interpreter and on its dependencies' matching
columns, nothing else, so the dependency graph is honest: installing
``click-cp313t`` drags in python313t (and colorama-cp313t), never a second
interpreter.  This replaces the bare-name + copy-fanout model (#389/#390),
whose graph pinned every pure wheel to python312 and could not express a
free-threaded column at all.

Wheel selection per column:

  * pure  (``py3-none-any``)  -> the same wheel in every column, installed
    into that column's interpreter site-packages only (``platform: any``,
    built once).
  * abi3  (stable ABI)        -> the abi3 wheel serves every
    non-free-threaded column at or above its cpNN floor; the cp313t column
    exists only if an exact ``cp313-cp313t`` wheel exists (the
    free-threaded build does not implement the stable ABI).
  * cext  (``cpNN-cpNN[t]``)  -> the exact wheel for that column, per
    platform; the column exists only where wheels exist.

A column is emitted only when EVERY python dependency also has that column
(transitive pruning): a package without a cp313t wheel prunes the cp313t
column of everything above it, so the published matrix is exactly what
works -- nothing is promised for an interpreter it cannot import under.

Columns of a package that ships console scripts declare
``provides: [<base>]``: the bin/ entry points collide across columns, so
the provides slot makes the exclusion explicit -- and lets
``cvcpkg install <base>`` resolve a column via the resolver's virtual
names.  Script-less library columns coexist freely (their payloads live in
disjoint ``lib/pythonX.Y[t]/site-packages`` trees).

Sources: ``poetry.lock`` (the cvcpkg server's runtime closure) plus the
SEED_PACKAGES table below (hand-curated build tools and libraries that are
not server deps: pytest, black, sympy, setuptools, cython, ...).

Reproducible: re-run after a ``poetry lock`` bump or a seed edit.  Usage:
    python tools/gen_python_recipes.py [--out recipes] [--interpreters 311,312,313,313t]
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
    "macos-x86_64": ["macosx"],  # + x86_64 or universal2
    "macos-arm64": ["macosx"],  # + arm64 or universal2
    "windows-x86_64": ["win_amd64", "win-amd64"],
}
# One column per interpreter recipe cvcpkg ships; "313t" is the free-threaded
# (no-GIL) build. Extending the matrix to a future python314 is one entry here
# plus a regeneration -- existing columns are untouched.
INTERPRETERS = ["311", "312", "313", "313t"]
PYPI = "https://pypi.org/pypi/{name}/{version}/json"


# Packages whose wheels install console scripts into bin/ (base -> script
# names). Sibling columns clobber those entry points, so each column declares
# ``provides: [<base>]`` -- a mutually-exclusive slot (see recipe-schema
# `provides`). Curated: PyPI metadata does not expose entry points without
# downloading the wheel. A miss here degrades to an undeclared same-version
# bin/ overlap (last extract wins), which file_conflicts.py can audit offline.
SCRIPT_PACKAGES = {
    "alembic": ["alembic"],
    "black": ["black", "blackd"],
    "charset-normalizer": ["normalizer"],
    "cython": ["cython", "cythonize", "cygdb"],
    "fastapi": ["fastapi"],
    "httpx": ["httpx"],
    "idna": ["idna"],
    "invoke": ["invoke", "inv"],
    "jmespath": ["jp.py"],
    "mako": ["mako-render"],
    "pygments": ["pygmentize"],
    "pytest": ["pytest", "py.test"],
    "sympy": ["isympy"],
    "tqdm": ["tqdm"],
    "uvicorn": ["uvicorn"],
    "wheel": ["wheel"],
}

# Base names whose -cp311/-cp312/-cp313 columns were already published to
# cvcpkg.org before #390 collapsed them to bare names (all at +cvc.1,
# 2026-07-24/25). Their resurrected recipes must start ABOVE the published
# revision or a same-revision publish with different contents 409s / gets
# skipped by --skip-existing.
_RESURRECTED_FLOOR = {
    base: 2
    for base in (
        "asyncpg",
        "bcrypt",
        "cffi",
        "cryptography",
        "google-crc32c",
        "greenlet",
        "markupsafe",
        "pydantic-core",
        "pynacl",
        "pyyaml",
    )
}

# Hand-curated packages that are not in the server's poetry.lock closure but
# are published as cvcpkg recipes (build tools, torch's noarch deps, dev
# tools). deps are base names resolved within this file's package universe;
# files/check override the generated defaults where the wheel's layout is not
# just <module>/ + dist-info. Versions are pinned like poetry.lock pins:
# bumping one is a deliberate edit here.
SEED_PACKAGES = {
    "black": {
        "version": "26.5.1",
        "license": "MIT",
        "deps": ["click", "mypy-extensions", "packaging", "pathspec", "platformdirs", "pytokens"],
        "files": ["black/", "blackd/", "blib2to3/", "_black_version.py", "black-*.dist-info/"],
        "check": "import black",
    },
    "cython": {
        "version": "3.1.6",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["Cython/", "cython.py", "pyximport/", "cython-*.dist-info/"],
        "check": "import Cython",
    },
    "filelock": {
        "version": "3.16.1",
        "license": "Unlicense",
        "deps": [],
        "files": ["filelock/", "filelock-*.dist-info/"],
        "check": "import filelock",
    },
    "fsspec": {
        "version": "2024.10.0",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["fsspec/", "fsspec-*.dist-info/"],
        "check": "import fsspec",
    },
    "iniconfig": {
        "version": "2.3.0",
        "license": "MIT",
        "deps": [],
        "files": ["iniconfig/", "iniconfig-*.dist-info/"],
        "check": "import iniconfig",
    },
    "jinja2": {
        "version": "3.1.4",
        "license": "BSD-3-Clause",
        "deps": ["markupsafe"],
        "files": ["jinja2/", "jinja2-*.dist-info/"],
        "check": "import jinja2",
    },
    "meson-python": {
        "version": "0.18.0",
        "license": "MIT",
        "deps": ["packaging", "pyproject-metadata"],
        "files": ["mesonpy/", "meson_python-*.dist-info/"],
        "check": "import mesonpy",
    },
    "mpmath": {
        "version": "1.3.0",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["mpmath/", "mpmath-*.dist-info/"],
        "check": "import mpmath",
    },
    "mypy-extensions": {
        "version": "1.1.0",
        "license": "MIT",
        "deps": [],
        "files": ["mypy_extensions.py", "mypy_extensions-*.dist-info/"],
        "check": "import mypy_extensions",
    },
    "networkx": {
        "version": "3.4.2",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["networkx/", "networkx-*.dist-info/"],
        "check": "import networkx",
    },
    "packaging": {
        "version": "24.2",
        "license": "Apache-2.0 OR BSD-2-Clause",
        "deps": [],
        "files": ["packaging/", "packaging-*.dist-info/"],
        "check": "import packaging",
    },
    "pathspec": {
        "version": "1.1.1",
        "license": "MPL-2.0",
        "deps": [],
        "files": ["pathspec/", "pathspec-*.dist-info/"],
        "check": "import pathspec",
    },
    "pkgconfig": {
        "version": "1.5.5",
        "license": "MIT",
        "deps": [],
        "files": ["pkgconfig/", "pkgconfig-*.dist-info/"],
        "check": "import pkgconfig",
    },
    "platformdirs": {
        "version": "4.11.0",
        "license": "MIT",
        "deps": [],
        "files": ["platformdirs/", "platformdirs-*.dist-info/"],
        "check": "import platformdirs",
    },
    "pluggy": {
        "version": "1.6.0",
        "license": "MIT",
        "deps": [],
        "files": ["pluggy/", "pluggy-*.dist-info/"],
        "check": "import pluggy",
    },
    "pygments": {
        "version": "2.20.0",
        "license": "BSD-2-Clause",
        "deps": [],
        "files": ["pygments/", "pygments-*.dist-info/"],
        "check": "import pygments",
    },
    "pyproject-metadata": {
        "version": "0.9.1",
        "license": "MIT",
        "deps": [],
        "files": ["pyproject_metadata/", "pyproject_metadata-*.dist-info/"],
        "check": "import pyproject_metadata",
    },
    "pytest": {
        "version": "9.1.1",
        "license": "MIT",
        "deps": ["iniconfig", "packaging", "pluggy", "pygments"],
        "files": ["_pytest/", "pytest/", "py.py", "pytest-*.dist-info/"],
        "check": "import pytest",
    },
    "pytokens": {
        "version": "0.4.1",
        "license": "MIT",
        "deps": [],
        "files": ["pytokens/", "pytokens-*.dist-info/"],
        "check": "import pytokens",
    },
    "setuptools": {
        "version": "80.9.0",
        "license": "MIT",
        "deps": [],
        "files": ["setuptools/", "pkg_resources/", "_distutils_hack/", "setuptools-*.dist-info/"],
        "check": "import setuptools",
    },
    "sympy": {
        "version": "1.13.3",
        "license": "BSD-3-Clause",
        "deps": ["mpmath"],
        "files": ["sympy/", "isympy.py", "sympy-*.dist-info/"],
        "check": "import sympy",
    },
    "wheel": {
        "version": "0.45.1",
        "license": "MIT",
        "deps": [],
        "files": ["wheel/", "wheel-*.dist-info/"],
        "check": "import wheel",
    },
}


def norm(name: str) -> str:
    """PyPI-normalize a distribution name -> cvcpkg recipe base name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def semver(version: str) -> str:
    """The orderable numeric prefix of a PEP440 version for upstream_version.
    e.g. '2.9.0.post0' -> '2.9.0', '1.0.0rc1' -> '1.0.0'. The pinned wheel URL
    (the exact locked build) is unaffected — this is just the display version."""
    return re.match(r"\d+(?:\.\d+)*", version).group(0)


def col_version(interp: str) -> str:
    """Interpreter column -> the X.Y[t] version dir it installs under.
    '311' -> '3.11', '313t' -> '3.13t'."""
    digits = interp[:-1] if interp.endswith("t") else interp
    return f"{digits[0]}.{digits[1:]}" + ("t" if interp.endswith("t") else "")


def col_digits(interp: str) -> str:
    """'313t' -> '313' (the python-tag digits, without the ABI's t suffix)."""
    return interp[:-1] if interp.endswith("t") else interp


def wheel_matches_platform(fn: str, platform: str) -> bool:
    """True if wheel filename *fn* targets cvcpkg *platform*.  macOS
    ``universal2`` wheels satisfy both macos columns."""
    lo = fn.lower()
    if platform == "linux-x86_64":
        return ("manylinux" in lo or "musllinux" in lo) and "x86_64" in lo
    if platform == "linux-arm64":
        return ("manylinux" in lo or "musllinux" in lo) and ("aarch64" in lo or "arm64" in lo)
    if platform == "macos-x86_64":
        return "macosx" in lo and ("x86_64" in lo or "universal2" in lo)
    if platform == "macos-arm64":
        return "macosx" in lo and ("arm64" in lo or "universal2" in lo)
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


_MARKER_CMP = re.compile(r'python_version\s*(<=|>=|<|>|==|!=)\s*"(\d+(?:\.\d+)*)"')


def marker_ok(dep_spec, interp: str) -> bool:
    """Evaluate a dependency's python_version marker for one column.

    Only conjunctive python_version comparisons are evaluated (the shapes
    poetry.lock actually contains, e.g. tomli's ``python_version < "3.11"``);
    any other marker content keeps the dependency (conservative).  The
    free-threaded column evaluates as its base version (3.13t -> 3.13)."""
    if not isinstance(dep_spec, dict):
        return True
    # An optional (extras-gated) dependency is not a hard edge: the consumer
    # that requires the extra (msal -> pyjwt[crypto]) also carries its own
    # direct dependency on the extra's payload, so the closure stays correct
    # and a pure package is not pruned from a column by an extra it never
    # needs (pyjwt-cp313t exists even though cryptography has no cp313t).
    if dep_spec.get("optional") or "extra ==" in dep_spec.get("markers", ""):
        return False
    marker = dep_spec.get("markers", "")
    if not marker or " or " in marker:
        return True
    ver = tuple(int(p) for p in col_version(interp).rstrip("t").split("."))
    for op, rhs in _MARKER_CMP.findall(marker):
        want = tuple(int(p) for p in rhs.split("."))
        # PEP 440: 3.11 == 3.11.0 — zero-pad both sides for the ordered
        # comparisons so a marker like < "3.11.0" excludes the 3.11 column.
        n = max(len(ver), len(want))
        vp, wp = ver + (0,) * (n - len(ver)), want + (0,) * (n - len(want))
        ok = {
            "<": vp < wp,
            "<=": vp <= wp,
            ">": vp > wp,
            ">=": vp >= wp,
            "==": vp == wp,
            "!=": vp != wp,
        }[op]
        if not ok:
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
      interpreter-independent.  Every column installs the same wheel.
    * ``abi3`` — no pure wheel, but the extension ships stable-ABI (``abi3``)
      wheels and no per-version ``cpNN-cpNN`` build for our non-free-threaded
      targets: one binary serves every non-free-threaded column.
    * ``cext`` — a distinct ``cpNN-cpNN[t]`` binary per interpreter.

    Classification is per-package bookkeeping; wheel selection is per column
    (see wheel_for_column), so an abi3 package with an extra exact cp313t
    wheel still gets its free-threaded column.
    """
    # A none-any wheel wins even when binary wheels also exist (black,
    # charset-normalizer, cython, protobuf, pytokens, sqlalchemy, tomli):
    # the pure fallback trades speed (no mypyc / C speedups / upb) for a
    # build-once noarch artifact and full-column coverage incl. cp313t.
    # Flip a package to its binary wheels only as a deliberate, per-package
    # decision (a PREFER_BINARY set would slot in here).
    if any(w["filename"].endswith("none-any.whl") for w in wheels):
        return "pure"
    return "abi3" if _is_abi3(wheels, interps) else "cext"


def _is_abi3(wheels: list[dict], interps: list[str]) -> bool:
    """True when, for the non-free-threaded interpreters we target, the
    extension is stable-ABI (abi3) rather than one build per version.  Judged
    on linux-x86_64: an ``abi3`` wheel present and no exact non-free-threaded
    ``cpNN-cpNN`` wheel for any target interpreter."""
    targets = [i for i in interps if not i.endswith("t")]
    lx = [w["filename"] for w in wheels if wheel_matches_platform(w["filename"], "linux-x86_64")]
    if not lx:
        return False
    has_abi3 = any("-abi3-" in fn for fn in lx)
    has_perver = any(re.search(rf"-cp{i}-cp{i}-", fn) for fn in lx for i in targets)
    return has_abi3 and not has_perver


def pure_wheel(wheels: list[dict]) -> dict:
    return next(w for w in wheels if w["filename"].endswith("none-any.whl"))


def abi3_wheel_for(wheels: list[dict], platform: str, interp: str) -> dict | None:
    """The stable-ABI wheel serving column *interp* on *platform* (manylinux
    over musllinux).  Only non-free-threaded columns at or above the wheel's
    cpNN floor qualify: the free-threaded build has no stable ABI."""
    if interp.endswith("t"):
        return None
    cands = []
    for w in wheels:
        m = re.search(r"-cp(\d+)-abi3-", w["filename"])
        if not m or not wheel_matches_platform(w["filename"], platform):
            continue
        if int(m.group(1)) <= int(col_digits(interp)):
            cands.append(w)
    cands.sort(key=lambda w: ("musllinux" in w["filename"]))
    return cands[0] if cands else None


def cext_wheel_for(wheels: list[dict], interp: str, platform: str) -> dict | None:
    """Exact per-version wheel for a column+platform (manylinux over
    musllinux).  The ABI tag is what distinguishes the free-threaded build:
    cp313's wheel is ``-cp313-cp313-``, cp313t's is ``-cp313-cp313t-``."""
    pat = rf"-cp{col_digits(interp)}-cp{interp}-"
    cands = [
        w
        for w in wheels
        if re.search(pat, w["filename"]) and wheel_matches_platform(w["filename"], platform)
    ]
    cands.sort(key=lambda w: ("musllinux" in w["filename"]))
    return cands[0] if cands else None


def wheel_for_column(m: dict, interp: str) -> dict[str, dict]:
    """platform-key -> wheel for one column of one package.

    * pure package: ``{"any": <the none-any wheel>}`` for every column.
    * binary package: for each platform, the exact ``cpNN[t]`` wheel first,
      else (non-free-threaded columns only) the abi3 wheel.  Empty dict when
      the column has no wheel anywhere -> the column is not emitted.
    """
    if m["kind"] == "pure":
        return {"any": pure_wheel(m["wheels"])}
    arts: dict[str, dict] = {}
    for platform in PLATFORM_TAGS:
        w = cext_wheel_for(m["wheels"], interp, platform) or abi3_wheel_for(
            m["wheels"], platform, interp
        )
        if w:
            arts[platform] = w
    return arts


def column_abi(m: dict, interp: str, arts: dict[str, dict]) -> str:
    """The recipe's ``python.abi`` for a column: the exact cpNN[t] tag, or
    ``abi3`` when every selected wheel is stable-ABI."""
    if m["kind"] != "pure" and arts and all("-abi3-" in w["filename"] for w in arts.values()):
        return "abi3"
    return f"cp{interp}"


def deps_for_column(m: dict, interp: str, universe: set[str]) -> list[str]:
    """Base names of *m*'s python deps that apply to this column."""
    if m.get("seed"):
        return [d for d in m["deps"] if d in universe]
    return [
        norm(d) for d, spec in m["deps"].items() if marker_ok(spec, interp) and norm(d) in universe
    ]


def compute_columns(meta: dict[str, dict], interps: list[str]) -> dict[str, list[str]]:
    """Viable columns per base: a column needs a wheel AND every python dep
    viable in that column, to a fixpoint.  Pruning is reported to stderr so a
    silently missing column is visible in the regeneration log."""
    universe = set(meta)
    cols: dict[str, list[str]] = {}
    why: dict[tuple[str, str], str] = {}
    for base, m in meta.items():
        cols[base] = []
        for i in interps:
            if wheel_for_column(m, i):
                cols[base].append(i)
            else:
                why[(base, i)] = "no compatible wheel"
    changed = True
    while changed:
        changed = False
        for base, m in meta.items():
            for i in list(cols[base]):
                missing = [d for d in deps_for_column(m, i, universe) if i not in cols[d]]
                if missing:
                    cols[base].remove(i)
                    why[(base, i)] = f"dep(s) lack the column: {', '.join(missing)}"
                    changed = True
    for (base, i), reason in sorted(why.items()):
        print(f"  prune {base}-cp{i}: {reason}", file=sys.stderr)
    return cols


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default="poetry.lock")
    ap.add_argument("--out", default="recipes")
    ap.add_argument("--interpreters", default=",".join(INTERPRETERS))
    args = ap.parse_args()
    interps = args.interpreters.split(",")
    out = Path(args.out)

    pkgs = load_runtime_packages(Path(args.lock))
    print(f"runtime packages: {len(pkgs)} + {len(SEED_PACKAGES)} seeds", file=sys.stderr)
    skipped: list[str] = []

    # Pass 1: fetch wheels + classify every package (needed for dep mapping).
    meta: dict[str, dict] = {}
    for base, info in sorted(pkgs.items()):
        try:
            wheels, lic = fetch_pypi(info["pypi_name"], info["version"])
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {base} {info['version']}: PyPI fetch failed: {e}", file=sys.stderr)
            skipped.append(base)
            continue
        if not wheels:
            print(f"  SKIP {base}: no wheels (sdist-only) at {info['version']}", file=sys.stderr)
            skipped.append(base)
            continue
        lic = _LICENSE_OVERRIDE.get(base, lic)
        meta[base] = {**info, "wheels": wheels, "kind": classify(wheels, interps), "license": lic}
    for base, seed in sorted(SEED_PACKAGES.items()):
        if base in meta:
            print(f"  seed {base} shadowed by poetry.lock; lock wins", file=sys.stderr)
            continue
        try:
            wheels, lic = fetch_pypi(base, seed["version"])
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP seed {base} {seed['version']}: PyPI fetch failed: {e}", file=sys.stderr)
            skipped.append(base)
            continue
        if not wheels:
            print(f"  SKIP seed {base}: no wheels at {seed['version']}", file=sys.stderr)
            skipped.append(base)
            continue
        meta[base] = {
            "pypi_name": base,
            "version": seed["version"],
            "deps": seed["deps"],
            "wheels": wheels,
            "kind": classify(wheels, interps),
            "license": seed.get("license", lic),
            "files": seed.get("files"),
            "check": seed.get("check"),
            "seed": True,
        }

    if skipped:
        # A skipped package silently disappears from every dependent's dep
        # list and from pruning decisions — a transient PyPI 500 must not
        # masquerade as a legitimate regeneration. Full-regen tool: any skip
        # is fatal, nothing is emitted.
        print(
            f"FATAL: {len(skipped)} package(s) could not be resolved: "
            f"{', '.join(sorted(skipped))} — nothing emitted.",
            file=sys.stderr,
        )
        return 1

    cols = compute_columns(meta, interps)

    counts = {"pure": 0, "abi3": 0, "cext": 0}
    emitted = 0
    for base, m in sorted(meta.items()):
        for interp in cols[base]:
            _emit_column(out, base, m, interp, meta, cols)
            emitted += 1
        if cols[base]:
            counts[m["kind"]] += 1
    print(
        f"emitted {emitted} column recipes across {len(meta)} packages "
        f"({counts['pure']} pure, {counts['abi3']} abi3, {counts['cext']} per-version)",
        file=sys.stderr,
    )
    _prune_stale(out, meta, cols, interps)
    return 0


def _prune_stale(out: Path, meta: dict, cols: dict[str, list[str]], interps: list[str]) -> None:
    """Delete superseded recipe dirs for managed bases: the bare-name recipe
    (replaced by the column matrix) and columns no longer viable.

    Only dirs whose recipe.yaml is a ``python_wheel`` recipe are touched, so a
    same-named native recipe (the C++ ``protobuf``) is never clobbered, and
    unmanaged per-interpreter recipes (numpy, vtk-python, wand, ...) are
    untouched because their bases are not in the managed set."""
    import shutil

    removed = 0
    for base in meta:
        candidates = [out / base] + [out / f"{base}-cp{i}" for i in interps]
        keep = {out / f"{base}-cp{i}" for i in cols[base]}
        for d in candidates:
            if d in keep or not d.is_dir():
                continue
            y = d / "recipe.yaml"
            if not y.is_file() or "python_wheel" not in y.read_text():
                print(f"  keep {d.name}: not a python_wheel recipe", file=sys.stderr)
                continue
            shutil.rmtree(d)
            removed += 1
    # Orphan sweep: a package that left poetry.lock (or a deleted seed) must
    # not leave generator-owned column dirs behind — they would keep
    # validating and publishing forever. Only dirs whose description carries
    # the generator marker are touched; hand-written -cpNNN recipes (numpy,
    # torch, wand, ...) never carry it.
    marker = "generated by tools/gen_python_recipes.py"
    suffixes = tuple(f"-cp{i}" for i in interps)
    for d in sorted(out.iterdir()):
        if not d.is_dir() or not d.name.endswith(suffixes):
            continue
        base = re.sub(r"-cp3[0-9]{2}t?$", "", d.name)
        if base in meta:
            continue
        y = d / "recipe.yaml"
        if y.is_file() and marker in y.read_text():
            shutil.rmtree(d)
            removed += 1
            print(f"  orphan {d.name}: base '{base}' left the closure", file=sys.stderr)
    if removed:
        print(f"pruned {removed} superseded recipe dir(s)", file=sys.stderr)


def _artifact_block(url: str, sha: str, indent: str) -> str:
    return f'{indent}url: {url}\n{indent}sha256: "{sha}"\n'


def _write_recipe(recipe_dir: Path, body_with_rev: str, floor: int = 1) -> None:
    """Write recipe.yaml, preserving ``cvc_revision`` idempotently.

    *body_with_rev* carries a literal ``{rev}`` where the revision goes.  A
    brand-new recipe starts at *floor* (>1 for names already published at
    +cvc.1 before #390 deleted their dirs); an unchanged one keeps its
    revision; a changed one bumps by one.  This stops regeneration from
    resetting the revisions that drive republish."""
    path = recipe_dir / "recipe.yaml"
    old = path.read_text() if path.exists() else ""
    m = re.search(r"cvc_revision:\s*(\d+)", old)
    old_rev = int(m.group(1)) if m else 1
    old_norm = re.sub(r"cvc_revision:\s*\d+", "cvc_revision: {rev}", old)
    if not old:
        rev = floor
    elif old_norm == body_with_rev:
        rev = old_rev
    else:
        rev = max(old_rev + 1, floor)
    recipe_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(body_with_rev.replace("{rev}", str(rev)))


def _emit_column(out, base, m, interp, meta, cols):
    """Emit one recipe: <base>-cp<interp>."""
    name = f"{base}-cp{interp}"
    d = out / name
    arts = wheel_for_column(m, interp)
    abi = column_abi(m, interp, arts)
    ver = col_version(interp)
    kind = m["kind"]
    dep_bases = deps_for_column(m, interp, set(meta))
    # Runtime deps are ALSO build deps: the post-install `import <pkg>` check
    # imports the package for real, and many packages eagerly import a runtime
    # dep at module load (sqlalchemy -> typing_extensions), so it must be staged
    # in the build prefix or the check fails.
    deps = [f"python{interp}", *(f"{b}-cp{interp}" for b in dep_bases)]

    flavor = {
        "pure": "pure-Python wheel, same artifact in every column",
        "abi3": "stable-ABI abi3 wheel",
        "cext": f"cp{interp} C-extension wheel",
    }[kind if abi != "abi3" else "abi3"]
    free_threaded_note = (
        "    The cp313t column installs under the free-threaded (no-GIL)\n"
        "    interpreter and its import check runs with the GIL disabled.\n"
        if interp.endswith("t")
        else ""
    )
    body = (
        f"schema_version: 1\n"
        f"recipe:\n"
        f"  name: {name}\n"
        f'  upstream_version: "{semver(m["version"])}"\n'
        f"  cvc_revision: {{rev}}\n\n"
        f'  maintainer: "cvcpkg group"\n'
        f'  maintainer_email: "info@cvcpkg.org"\n'
        f'  maintainer_url: "https://cvcpkg.org"\n'
        f'  homepage: https://pypi.org/project/{m["pypi_name"]}/\n'
        f'  license: "{m.get("license") or "NOASSERTION"}"\n'
        f"  tags: [python, wheel]\n"
        f"  description: >-\n"
        f'    {m["pypi_name"]} {m["version"]} for CPython {ver} — the cp{interp} column of\n'
        f"    cvcpkg's per-interpreter wheel matrix ({flavor});\n"
        f"    generated by tools/gen_python_recipes.py.\n"
        f"{free_threaded_note}"
    )

    body += "\nsource:\n  type: python_wheel\n  artifacts:\n"
    for key in ("any", *PLATFORM_TAGS):
        if key in arts:
            w = arts[key]
            body += f"    {key}:\n" + _artifact_block(w["url"], w["digests"]["sha256"], "      ")

    body += f"\npython:\n  interpreter: python{interp}\n  abi: {abi}\n"
    if kind != "pure":
        body += "  manylinux_min: manylinux_2_28\n"
    body += "\npatches: []\n\n"

    body += (
        "depends:\n  build:\n"
        + "".join(f"    - name: {b}\n" for b in dict.fromkeys(deps))
        + "  runtime:\n"
        + "".join(f"    - name: {r}\n" for r in dict.fromkeys(deps))
    )

    if base in SCRIPT_PACKAGES:
        # Console scripts collide across columns in bin/; the provides slot
        # declares the exclusion and doubles as the bare-name virtual for
        # `cvcpkg install <base>`.
        body += f"\nprovides:\n  - {base}\n"

    if kind == "pure":
        matrix = "    - platform: any\n      script: build.sh\n"
    else:
        plats = []
        if any(k.startswith("linux") for k in arts):
            plats.append(("linux", "build.sh"))
        if any(k.startswith("macos") for k in arts):
            plats.append(("macos", "build.sh"))
        if any(k.startswith("windows") for k in arts):
            plats.append(("windows", "build.ps1"))
        matrix = "".join(f"    - platform: {p}\n      script: {s}\n" for p, s in plats)
    body += "\nbuild:\n  build_type_independent: true\n  matrix:\n" + matrix

    body += "\npackage:\n  files:\n"
    for g in _files_globs(m, base, interp, arts):
        body += f"    - {g}\n"

    floor = _RESURRECTED_FLOOR.get(base, 1) if not interp.endswith("t") else 1
    _write_recipe(d, body, floor=floor)

    check = m.get("check") or f"import {_toppkg(base)}"
    (d / "build.sh").write_text(_BUILD_SH.format(name=name, check=check))
    (d / "build.sh").chmod(0o755)
    ps1 = d / "build.ps1"
    if kind != "pure" and any(k.startswith("windows") for k in arts):
        ps1.write_text(_BUILD_PS1.format(name=name, check=check))
    elif ps1.exists():
        ps1.unlink()


def _files_globs(m, base, interp, arts) -> list[str]:
    """package.files for one column.  The payload lands only under this
    column's interpreter dir, so the globs pin the exact version dir."""
    ver = col_version(interp)
    prefix = f"lib/python{ver}/site-packages/"
    if m.get("files"):  # seed override: paths relative to site-packages
        globs = [prefix + f for f in m["files"]]
    else:
        globs = [prefix + f"{_toppath(base)}/", prefix + "*.dist-info/"]
    for script in SCRIPT_PACKAGES.get(base, ()):
        globs.append(f"bin/{script}")
    if any(k.startswith("windows") for k in arts):
        globs += [f"Lib/site-packages/{_toppath(base)}/", "Lib/site-packages/*.dist-info/"]
    return globs


# Import module != distribution name for many packages. Used by the post-install
# `import <module>` check, so it must be the *real* importable module — for the
# google.* namespace packages that means the dotted subpackage, not the
# dist-name-with-underscores (which is not importable and fails the check).
_TOPPKG = {
    "pyyaml": "yaml",
    "sqlalchemy": "sqlalchemy",
    "mako": "mako",
    "typing-extensions": "typing_extensions",
    "python-multipart": "multipart",
    "python-dateutil": "dateutil",
    "pynacl": "nacl",
    "protobuf": "google.protobuf",
    "proto-plus": "proto",
    "google-crc32c": "google_crc32c",
    "google-auth": "google.auth",
    "google-api-core": "google.api_core",
    "google-resumable-media": "google.resumable_media",
    "googleapis-common-protos": "google.rpc",
    "google-cloud-core": "google.cloud",
    "google-cloud-storage": "google.cloud.storage",
    "azure-storage-blob": "azure",
    "azure-identity": "azure",
    "azure-core": "azure",
    "pyjwt": "jwt",
}


def _toppkg(base: str) -> str:
    """Importable module name for the post-install check (may be dotted)."""
    return _TOPPKG.get(base, base.replace("-", "_"))


def _toppath(base: str) -> str:
    """Installed directory for package.files globs — the module as a path, so a
    dotted namespace module (``google.protobuf``) globs ``google/protobuf/``."""
    return _toppkg(base).replace(".", "/")


_BUILD_SH = """#!/usr/bin/env bash
# recipes/{name}/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "{check}"
"""

_BUILD_PS1 = """# recipes/{name}/build.ps1 — install the pinned wheel (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck '{check}'
"""


if __name__ == "__main__":
    raise SystemExit(main())
