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

SOURCE MODE (hermeticity) -- a column is emitted in one of two shapes:

  * ``sdist`` (DEFAULT, preferred) -- ``source.type: tarball`` pointing at the
    PyPI **sdist** with ``strip_components: 1``; build.sh compiles the wheel
    with the prefix's own interpreter (``pip wheel --no-build-isolation
    --no-deps --no-index``) and installs it.  Nothing third-party and compiled
    enters the bundle.  This is the shape recipes/numpy-cp311 and
    recipes/h5py-cp311 already use, generalized.
  * ``wheel`` (fallback) -- the historical ``source.type: python_wheel``
    recipe that downloads a prebuilt PyPI wheel.  Reached only for a
    documented reason: the package is on PREBUILT_ONLY (CUDA binary
    redistributables, which have no buildable source at all), PyPI publishes
    no sdist for the pinned version, a required PEP-517 build backend has no
    cvcpkg recipe yet, or the operator asked for it (``--source-mode wheel`` /
    ``--pure-policy wheel``).  Every fallback is printed with its reason.

Even a ``py3-none-any`` wheel is still a third-party binary artifact, so pure
packages default to the sdist path too; ``--pure-policy wheel`` restores the
old prebuilt-noarch behaviour as an explicit, per-run switch rather than an
implicit accident.

BUILD BACKENDS: ``--no-build-isolation`` means the PEP-517 backend
(setuptools / hatchling / flit-core / poetry-core / maturin ...) must already
be importable in the build prefix.  The generator reads the sdist's
``pyproject.toml`` ``[build-system] requires`` and emits those as
``depends.build`` edges on the matching cvcpkg columns.  A backend with no
cvcpkg recipe is a HARD BLOCKER: such a package stays on its prebuilt wheel
and is reported.  ``--report-backends`` prints the full survey without
emitting anything.

Reproducible: re-run after a ``poetry lock`` bump or a seed edit.  Usage:
    python tools/gen_python_recipes.py [--out recipes] [--interpreters 311,312,313,313t]

Targeted (single package, no pruning, safe on a dirty tree):
    python tools/gen_python_recipes.py --only click
    python tools/gen_python_recipes.py --only click,idna --interpreters 311

Survey the build backends without writing anything:
    python tools/gen_python_recipes.py --report-backends
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# tomllib is 3.11+ stdlib. Both TOML readers here used to `import tomllib`
# lazily on the theory that only lock parsing needs it and that never runs on
# 3.10 — true until parse_build_requires() (the sdist build-backend survey)
# became a second reader, which the 3.10 unit tests DO exercise. The result was
# ModuleNotFoundError: No module named 'tomllib' across every 3.10 job.
# tomli is the 3.10 backport and is already in the dev environment (black,
# mypy and coverage all pull it in under a python_version < "3.11" marker).
# Kept tolerant of neither being present so the module still imports.
try:  # pragma: no cover - version shim
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def _require_tomllib():
    """The TOML reader, or a clear error naming the fix."""
    if tomllib is None:
        raise RuntimeError(
            "no TOML reader available: tomllib needs Python >= 3.11, and the "
            "tomli backport is not installed. Install tomli to run this on 3.10."
        )
    return tomllib

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

# Platforms a FROM-SOURCE column claims when the package is not noarch.  A pure
# (noarch) package is still emitted as a single ``platform: any`` column -- the
# wheel it produces is py3-none-any, so building it once is correct.
#
# The BSDs are deliberately NOT here by default: PyPI ships no wheels for them,
# so nothing in the current matrix has ever been built there and claiming them
# from a generator run would publish an unproven promise.  Opt in per run with
# ``--sdist-platforms linux,macos,windows,freebsd,netbsd,openbsd`` once a
# builder for them exists.
SDIST_PLATFORMS = ["linux", "macos", "windows"]

# ── Packages that must REMAIN prebuilt wheels ────────────────────────────────
# These are NOT source distributions in any meaningful sense: the "wheel" is a
# repackaged binary redistributable and there is nothing to compile.  Pretending
# otherwise would produce a recipe that cannot build.  Each entry carries the
# reason it is exempt from the from-source mandate.
_PREBUILT_ONLY: list[tuple[str, str]] = [
    (
        r"^nvidia-.+-cu12$",
        # nvidia-cublas-cu12, nvidia-cudnn-cu12, nvidia-nccl-cu12, ...: NVIDIA
        # publishes these as wheels wrapping CLOSED-SOURCE .so blobs from the
        # CUDA redistributable tarballs. No source is published anywhere (the
        # PyPI sdist, where one exists at all, only repacks the same binaries),
        # so there is no from-source path -- only a licence to redistribute.
        "NVIDIA CUDA binary redistributable: closed-source .so blobs, no buildable source",
    ),
    (
        r"^torch$",
        # torch's wheels are a multi-GB CUDA/cuDNN/NCCL-linked build. The source
        # exists (github.com/pytorch/pytorch) but is NOT what PyPI ships as an
        # sdist, and building it needs the full CUDA toolkit + a multi-hour
        # nvcc compile that cvcpkg's builders do not have. Stays prebuilt.
        "CUDA-linked binary build; PyPI publishes no buildable sdist and the "
        "real source needs the full CUDA toolkit",
    ),
    (
        r"^triton$",
        # triton ships a prebuilt LLVM + its own compiler runtime inside the
        # wheel; the PyPI artifact is a binary redistributable in the same sense
        # as the nvidia-* family.
        "bundles a prebuilt LLVM toolchain; PyPI artifact is a binary redistributable",
    ),
]


def prebuilt_only_reason(base: str) -> str | None:
    """Why *base* must stay a prebuilt wheel, or None if it may be source-built."""
    for pattern, reason in _PREBUILT_ONLY:
        if re.match(pattern, base):
            return reason
    return None


# Build requirements that resolve to a NATIVE cvcpkg recipe (a CLI on PATH),
# not to a per-interpreter ``-cpNNN`` column.  meson is the live example:
# meson-python's build-system.requires names ``meson``, and cvcpkg ships that as
# the native ``meson`` recipe, so the edge must not be rewritten to meson-cp311.
_NATIVE_BUILD_REQ = {"meson", "ninja", "cmake", "patchelf", "pkg-config"}

# Build requirements that are never emitted as a dependency edge: they are part
# of the interpreter recipe itself, or are pip's own plumbing.
_IMPLICIT_BUILD_REQ = {"pip"}

# A source distribution with no pyproject.toml is a legacy setup.py project;
# PEP 517's documented fallback backend is setuptools.build_meta:__legacy__,
# whose requirements are exactly these.
_LEGACY_BUILD_REQ = ["setuptools", "wheel"]


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
        # pyproject_metadata/__init__.py does 'import packaging.requirements' at
        # import time, so the import check fails without it in the prefix.
        "deps": ["packaging"],
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
    # ── PEP-517 build backends ──────────────────────────────────────────────
    # --no-build-isolation means the backend must already be importable in the
    # build prefix, so an unpackaged backend forces its dependents onto a
    # prebuilt wheel. These six were the entire blocker list for the plotting/
    # imaging/schema stack below; packaging them converts those from prebuilt
    # to from-source. All are pure-Python (pybind11 and cppy ship headers).
    "calver": {
        "version": "2022.6.26",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["calver/", "calver-*.dist-info/"],
        "check": "import calver",
    },
    "trove-classifiers": {
        # Calendar-versioned. Pinned to a THREE-component release: cvcpkg's
        # validator requires an orderable SemVer upstream_version, and the
        # usual YYYY.M.D.HH form has four components (validate.py rejects it).
        "version": "2024.10.16",
        "license": "Apache-2.0",
        "deps": ["calver"],
        "files": ["trove_classifiers/", "trove_classifiers-*.dist-info/"],
        "check": "import trove_classifiers",
    },
    "hatchling": {
        "version": "1.27.0",
        "license": "MIT",
        "deps": ["packaging", "pathspec", "pluggy", "trove-classifiers"],
        "files": ["hatchling/", "hatchling-*.dist-info/"],
        "check": "import hatchling",
    },
    "hatch-vcs": {
        "version": "0.4.0",
        "license": "MIT",
        "deps": ["hatchling", "setuptools-scm"],
        "files": ["hatch_vcs/", "hatch_vcs-*.dist-info/"],
        "check": "import hatch_vcs",
    },
    "hatch-fancy-pypi-readme": {
        "version": "24.1.0",
        "license": "MIT",
        "deps": ["hatchling"],
        "files": ["hatch_fancy_pypi_readme/", "hatch_fancy_pypi_readme-*.dist-info/"],
        "check": "import hatch_fancy_pypi_readme",
    },
    "flit-core": {
        "version": "3.10.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["flit_core/", "flit_core-*.dist-info/"],
        "check": "import flit_core",
    },
    "cppy": {
        "version": "1.3.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["cppy/", "cppy-*.dist-info/"],
        "check": "import cppy",
    },
    "pybind11": {
        "version": "2.13.6",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["pybind11/", "pybind11-*.dist-info/"],
        "check": "import pybind11",
    },
    # ── plotting / imaging / schema stack ───────────────────────────────────
    # pillow is the load-bearing one: grl_snam_dbg.ingest.scene_buffers reads
    # the scene navmask.png through PIL, so the DBG planner cannot ingest a
    # scene without it. matplotlib and imageio are the grl_snam demo/capture
    # path; jsonschema validates movement_bundle.v1 against the contract
    # schema in grl_snam_dbg.scripts.export_movement_bundle.
    "pillow": {
        "version": "11.1.0",
        "license": "MIT-CMU",
        "deps": [],
        "files": ["PIL/", "pillow-*.dist-info/"],
        "check": "from PIL import Image; Image.new('L', (2, 2))",
    },
    "cycler": {
        "version": "0.12.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["cycler/", "cycler-*.dist-info/"],
        "check": "import cycler",
    },
    "pyparsing": {
        "version": "3.2.1",
        "license": "MIT",
        "deps": [],
        "files": ["pyparsing/", "pyparsing-*.dist-info/"],
        "check": "import pyparsing",
    },
    "kiwisolver": {
        "version": "1.4.8",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["kiwisolver/", "kiwisolver-*.dist-info/"],
        "check": "import kiwisolver",
    },
    "fonttools": {
        "version": "4.55.3",
        "license": "MIT",
        "deps": [],
        "files": ["fontTools/", "fonttools-*.dist-info/"],
        "check": "import fontTools",
    },
    "contourpy": {
        "version": "1.3.1",
        "license": "BSD-3-Clause",
        "deps": ["numpy"],
        "files": ["contourpy/", "contourpy-*.dist-info/"],
        "check": "import contourpy",
    },
    "matplotlib": {
        "version": "3.10.0",
        "license": "PSF-2.0",
        "deps": [
            "contourpy", "cycler", "fonttools", "kiwisolver", "numpy",
            "packaging", "pillow", "pyparsing", "python-dateutil",
        ],
        "files": ["matplotlib/", "mpl_toolkits/", "pylab.py", "matplotlib-*.dist-info/"],
        # Agg, not a GUI backend: the build fleet is headless and so is the
        # offscreen capture path this exists for.
        "check": "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot",
    },
    "imageio": {
        "version": "2.36.1",
        "license": "BSD-2-Clause",
        "deps": ["numpy", "pillow"],
        "files": ["imageio/", "imageio-*.dist-info/"],
        "check": "import imageio",
    },
    # jsonschema and its deps (attrs, pyrsistent) come from poetry.lock, which
    # pins 4.17.3 — the pre-referencing/rpds-py line. The lock shadows any seed
    # here ("lock wins"), so seeding them would be dead weight; they are pulled
    # into the emitted set by name instead.
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


def sdist_from(files: list[dict]) -> dict | None:
    """The source distribution among a release's ``urls[]`` entries.

    PyPI marks it with ``packagetype == "sdist"``.  A release has at most one,
    but old ones occasionally carry both a .tar.gz and a .zip; prefer the
    tarball (cvcpkg's tarball fetcher handles both, .tar.gz is the norm)."""
    sdists = [f for f in files if f.get("packagetype") == "sdist"]
    sdists.sort(key=lambda f: not f["filename"].endswith(".tar.gz"))
    return sdists[0] if sdists else None


def fetch_pypi(name: str, version: str) -> tuple[list[dict], dict | None, str]:
    """(wheels, sdist-or-None, license) for one pinned release."""
    url = PYPI.format(name=name, version=version)
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - pinned host
        data = json.load(r)
    files = data.get("urls", [])
    wheels = [f for f in files if f["filename"].endswith(".whl")]
    return wheels, sdist_from(files), _spdx(data.get("info", {}))


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


# ── PEP-517 build backends ───────────────────────────────────────────────────
# --no-build-isolation means pip does NOT create a throwaway venv and download
# the backend: whatever ``[build-system] requires`` names must already be
# importable by the interpreter running the build.  cvcpkg supplies that from
# the build prefix (depends.build -> CVC_BUILD_PREFIX, bridged onto sys.path by
# the generated build.sh), so the requires list has to become real dependency
# edges.  We read it out of the sdist itself rather than guessing: PyPI's JSON
# API does not expose build-system metadata.

_REQ_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def req_name(req: str) -> str:
    """Normalized distribution name of a PEP-508 requirement string.
    ``'setuptools_scm[toml] >= 7, < 10'`` -> ``'setuptools-scm'``."""
    m = _REQ_NAME.match(req.split(";", 1)[0])
    return norm(m.group(1)) if m else ""


def req_applies(req: str, interp: str) -> bool:
    """Does a build requirement's environment marker hold for this column?

    Only ``python_version`` comparisons are evaluated -- the shape that
    actually gates build requirements (``tomli >= 1.0.0; python_version <
    "3.11"``).  Anything else (``platform_python_implementation != 'PyPy'``)
    is kept: we always build on CPython, and keeping an extra edge is safe
    while dropping a needed one is not."""
    _, _, marker = req.partition(";")
    if not marker.strip() or " or " in marker:
        return True
    return marker_ok({"markers": marker.strip()}, interp)


def parse_build_requires(pyproject_text: str) -> list[str] | None:
    """``[build-system] requires`` from a pyproject.toml, or None if the file
    declares no build-system table (PEP 517 falls back to setuptools then)."""
    table = _require_tomllib().loads(pyproject_text).get("build-system") or {}
    reqs = table.get("requires")
    return list(reqs) if reqs is not None else None


def download_sdist(sdist: dict, cache_dir: Path) -> Path:
    """Fetch (and cache) an sdist so its pyproject.toml can be inspected.

    Cached by filename under *cache_dir*; a regeneration re-reads from disk
    instead of re-downloading ~90 tarballs."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    blob = cache_dir / sdist["filename"]
    if not blob.is_file():
        tmp = blob.with_suffix(blob.suffix + ".part")
        urllib.request.urlretrieve(sdist["url"], tmp)  # noqa: S310 - pinned host
        tmp.replace(blob)
    return blob


def _sdist_pyproject(blob: Path) -> str | None:
    """The top-level pyproject.toml inside an sdist archive, or None."""

    def _is_root_pyproject(name: str) -> bool:
        # sdists are '<name>-<version>/...'; only the project root counts (a
        # vendored subpackage's pyproject.toml is not the build config).
        return name.count("/") == 1 and name.endswith("/pyproject.toml")

    if blob.name.endswith(".zip"):
        with zipfile.ZipFile(blob) as z:
            for n in z.namelist():
                if _is_root_pyproject(n):
                    return z.read(n).decode("utf-8", "replace")
        return None
    with tarfile.open(blob) as t:
        for m in t.getmembers():
            if _is_root_pyproject(m.name):
                fh = t.extractfile(m)
                return fh.read().decode("utf-8", "replace") if fh else None
    return None


def sdist_build_requires(sdist: dict, cache_dir: Path) -> list[str]:
    """Raw ``build-system.requires`` strings for an sdist (legacy fallback
    applied when the project has no pyproject.toml / no build-system table)."""
    text = _sdist_pyproject(download_sdist(sdist, cache_dir))
    reqs = parse_build_requires(text) if text is not None else None
    return list(_LEGACY_BUILD_REQ) if reqs is None else reqs


def backend_edges(reqs: list[str], interp: str) -> list[str]:
    """Build requirements -> cvcpkg recipe names for one interpreter column.

    A per-interpreter python package becomes its ``-cpNNN`` column (that is the
    only build of it that the column's interpreter can import); a native tool
    (meson, ninja) stays a bare recipe name; pip is dropped (the interpreter
    recipe ships it)."""
    out: list[str] = []
    for req in reqs:
        name = req_name(req)
        if not name or name in _IMPLICIT_BUILD_REQ or not req_applies(req, interp):
            continue
        out.append(name if name in _NATIVE_BUILD_REQ else f"{name}-cp{interp}")
    return list(dict.fromkeys(out))


def available_recipes(out: Path, universe: set[str] = frozenset()) -> set[str]:
    """Recipe names cvcpkg can actually resolve an edge to: directories under
    *out* plus the columns this run itself emits.

    Used to decide whether a package's backend exists.  A backend that is not
    here is a hard blocker -- the from-source build would fail at
    ``--no-build-isolation`` import time, so the package stays prebuilt and is
    reported instead of silently emitting a dangling edge."""
    names = {d.name for d in out.iterdir() if d.is_dir()} if out.is_dir() else set()
    return names | set(universe)


def missing_backends(edges: list[str], available: set[str]) -> list[str]:
    return [e for e in edges if e not in available]


def source_mode_for(
    base: str,
    *,
    kind: str,
    has_sdist: bool,
    missing: list[str],
    pure_policy: str,
    forced: str = "auto",
) -> tuple[str, str]:
    """Decide ``sdist`` vs ``wheel`` for a package, with the reason.

    The order is the policy, and it is deliberately explicit rather than an
    accumulation of special cases:

      1. PREBUILT_ONLY wins over everything, including --source-mode sdist:
         those packages have no source to build.
      2. --source-mode pins the rest (an operator override, reported as such).
      3. No sdist on PyPI -> nothing to build from.
      4. Pure (noarch) packages follow --pure-policy; the DEFAULT is sdist,
         because a py3-none-any wheel is still a third-party binary artifact.
      5. A missing build-backend recipe blocks the source build.
      6. Otherwise: build from source."""
    reason = prebuilt_only_reason(base)
    if reason:
        return "wheel", reason
    if forced == "wheel":
        return "wheel", "--source-mode wheel"
    if not has_sdist:
        return "wheel", "PyPI publishes no sdist for this version"
    if forced == "sdist":
        return "sdist", "--source-mode sdist"
    if kind == "pure" and pure_policy == "wheel":
        return "wheel", "--pure-policy wheel (noarch package)"
    if missing:
        return "wheel", f"build backend not packaged: {', '.join(sorted(missing))}"
    return "sdist", "built from the PyPI sdist"


def load_runtime_packages(lock_path: Path) -> dict[str, dict]:
    with open(lock_path, "rb") as fh:
        lock = _require_tomllib().load(fh)
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


def column_abi(m: dict, interp: str, arts: dict[str, dict], mode: str = "wheel") -> str:
    """The recipe's ``python.abi`` for a column: the exact cpNN[t] tag, or
    ``abi3`` when every selected *prebuilt* wheel is stable-ABI.

    A from-source column is always the exact tag: we compile against THIS
    column's interpreter, so whatever stable-ABI wheels upstream happens to
    publish says nothing about what our build produces."""
    if mode == "sdist":
        return f"cp{interp}"
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


def resolve_source_modes(
    meta: dict[str, dict],
    cols: dict[str, list[str]],
    *,
    out: Path,
    interps: list[str],
    cache: Path,
    pure_policy: str,
    forced: str,
    platforms: list[str],
) -> dict[str, list[str]]:
    """Annotate every package in *meta* with its source mode + reason.

    Sets ``mode``/``reason`` on each entry, and ``build_requires`` for the
    from-source ones.  Returns ``{missing-backend-recipe: [packages it blocks]}``
    so a run reports its blockers instead of emitting a dangling edge.

    Backends are resolved on the FIRST viable column only.  A build requirement
    gated on ``python_version < "3.11"`` (tomli, typing-extensions backports)
    drops out per column at edge-rendering time; the availability question is
    the same across columns because a cvcpkg python package is either published
    for the whole matrix or pruned from it."""
    available = available_recipes(out, {f"{b}-cp{i}" for b, c in cols.items() for i in c})
    blockers: dict[str, list[str]] = {}
    for base, m in sorted(meta.items()):
        m["platforms"] = platforms
        probe = cols[base][0] if cols[base] else interps[0]
        reqs: list[str] = []
        missing: list[str] = []
        # Only pay for the sdist download when the answer can still be "sdist".
        if m["sdist"] and not prebuilt_only_reason(base) and forced != "wheel":
            try:
                reqs = sdist_build_requires(m["sdist"], cache)
            except Exception as e:  # noqa: BLE001
                m["mode"], m["reason"] = "wheel", f"sdist unreadable: {e}"
                print(f"  wheel {base}: {m['reason']}", file=sys.stderr)
                continue
            missing = missing_backends(backend_edges(reqs, probe), available)
        m["build_requires"] = reqs
        m["mode"], m["reason"] = source_mode_for(
            base,
            kind=m["kind"],
            has_sdist=bool(m["sdist"]),
            missing=missing,
            pure_policy=pure_policy,
            forced=forced,
        )
        for b in missing:
            blockers.setdefault(b, []).append(base)
        if m["mode"] == "wheel":
            print(f"  PREBUILT {base}: {m['reason']}", file=sys.stderr)
    return blockers


# Every recipe this generator writes carries this in its description; nothing
# hand-written does.  It is the ownership test for both regeneration and pruning.
GENERATOR_MARKER = "generated by tools/gen_python_recipes.py"

# ``source.type: python_wheel`` as a real YAML field -- the shape every recipe
# this generator emitted BEFORE the marker existed.  Matched line-anchored
# against a comment-stripped copy of the recipe, never as a bare substring: see
# _declares_python_wheel for why that distinction is load-bearing.
_SOURCE_TYPE_WHEEL = re.compile(r"^[^\S\n]*type:[^\S\n]*python_wheel[^\S\n]*$", re.MULTILINE)
# A YAML comment: '#' at line start or after whitespace.  Requiring the leading
# boundary keeps a URL fragment ('...#egg=x') from being eaten as a comment.
_YAML_COMMENT = re.compile(r"(?:^|\s)#.*$")


def _declares_python_wheel(text: str) -> bool:
    """True when a recipe's ``source.type`` really IS ``python_wheel``.

    Deliberately NOT ``"python_wheel" in text``.  Every hand-converted
    from-source recipe explains itself with a comment reading "From-source
    sdist (NOT source.type python_wheel): ..." -- a *denial* that a substring
    test reads as a declaration.  cffi, markupsafe, pyyaml, greenlet, numpy and
    h5py all carry that sentence, so the substring form handed _prune_stale a
    licence to delete exactly the hand-written recipes it was written to spare.
    """
    body = "\n".join(_YAML_COMMENT.sub("", line) for line in text.splitlines())
    return bool(_SOURCE_TYPE_WHEEL.search(body))


def is_prunable(recipe_dir: Path) -> bool:
    """May _prune_stale DELETE *recipe_dir*?

    True only for a recipe this generator actually wrote: one carrying the
    marker, or -- for columns emitted before the marker existed -- one whose
    ``source.type`` really is ``python_wheel``.  A hand-written recipe is
    neither, and a directory with no recipe.yaml is nobody's to delete.

    The DELETION counterpart of is_generator_owned (the overwrite test).  The
    two differ on purpose: a directory that does not exist yet is *writable*
    (it is a new column) but obviously not deletable.  Preservation from
    overwrite is not preservation from deletion -- a recipe needs both.
    """
    y = recipe_dir / "recipe.yaml"
    if not y.is_file():
        return False
    text = y.read_text(encoding="utf-8")
    return GENERATOR_MARKER in text or _declares_python_wheel(text)


def is_generator_owned(recipe_dir: Path) -> bool:
    """May this run overwrite *recipe_dir*?

    True for a directory that does not exist yet (a new column) or whose
    recipe.yaml carries the generator marker.  False for a HAND-WRITTEN recipe:
    numpy/h5py/cffi-style from-source conversions declare native-library edges
    (libffi, hdf5, openblas), rpath passes and per-platform gates that
    ``[build-system] requires`` cannot reveal, so regenerating over them would
    silently drop those edges and produce a recipe that builds against the
    system's copy of the library — exactly what cvcpkg exists to avoid."""
    y = recipe_dir / "recipe.yaml"
    return not y.is_file() or GENERATOR_MARKER in y.read_text(encoding="utf-8")


def _report_blockers(blockers: dict[str, list[str]]) -> None:
    if not blockers:
        print("build backends: all required backends have cvcpkg recipes", file=sys.stderr)
        return
    print(
        f"\nMISSING BUILD-BACKEND RECIPES ({len(blockers)}) — each blocks a "
        f"from-source conversion:",
        file=sys.stderr,
    )
    for backend, users in sorted(blockers.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(
            f"  {backend:32s} blocks {len(users):2d}: {', '.join(sorted(users))}", file=sys.stderr
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default="poetry.lock")
    ap.add_argument("--out", default="recipes")
    ap.add_argument("--interpreters", default=",".join(INTERPRETERS))
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated base names: emit ONLY these packages and skip "
        "pruning (targeted, reviewable regeneration of one recipe)",
    )
    ap.add_argument(
        "--source-mode",
        choices=["auto", "sdist", "wheel"],
        default="auto",
        help="auto (default): build from the sdist whenever the backend is "
        "packaged; sdist: force from-source even without a packaged backend "
        "(the build will fail until it exists); wheel: the old prebuilt-wheel "
        "recipes. PREBUILT_ONLY packages ignore this.",
    )
    ap.add_argument(
        "--pure-policy",
        choices=["sdist", "wheel"],
        default="sdist",
        help="what to do with noarch (py3-none-any) packages. sdist (default): "
        "build them from source too — a py3-none-any wheel is still a "
        "third-party binary artifact. wheel: keep downloading the noarch wheel.",
    )
    ap.add_argument(
        "--sdist-platforms",
        default=",".join(SDIST_PLATFORMS),
        help="platforms a compiled from-source column claims (noarch columns "
        "stay 'any'). BSDs are opt-in: nothing has been built there yet.",
    )
    ap.add_argument(
        "--sdist-cache",
        default="",
        help="directory for downloaded sdists (default: <out>/../.sdist-cache). "
        "They are read for [build-system] requires and never shipped; their "
        "*.tar.gz / *.zip are already gitignored.",
    )
    ap.add_argument(
        "--report-backends",
        action="store_true",
        help="survey every package's build backend and report which have no "
        "cvcpkg recipe; write nothing.",
    )
    ap.add_argument(
        "--overwrite-hand-written",
        action="store_true",
        help="regenerate columns whose recipe.yaml lacks the generator marker "
        "(cffi, greenlet, numpy, ... — hand-converted from-source recipes that "
        "carry native-library edges this generator cannot infer). Off by "
        "default: a regeneration must not silently undo a hand conversion.",
    )
    args = ap.parse_args()
    interps = args.interpreters.split(",")
    out = Path(args.out)
    only = {norm(n) for n in args.only.split(",") if n.strip()}
    cache = Path(args.sdist_cache) if args.sdist_cache else out.parent / ".sdist-cache"

    pkgs = load_runtime_packages(Path(args.lock))
    print(f"runtime packages: {len(pkgs)} + {len(SEED_PACKAGES)} seeds", file=sys.stderr)
    skipped: list[str] = []

    # Pass 1: fetch wheels + sdist + classify every package.  The FULL universe
    # is always resolved, even for --only: dep mapping and column pruning are
    # global, so a partial universe would emit a wrong dep list.
    meta: dict[str, dict] = {}
    for base, info in sorted(pkgs.items()):
        try:
            wheels, sdist, lic = fetch_pypi(info["pypi_name"], info["version"])
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {base} {info['version']}: PyPI fetch failed: {e}", file=sys.stderr)
            skipped.append(base)
            continue
        if not wheels:
            # Column VIABILITY is still wheel-derived even in sdist mode (see
            # the note by compute_columns' call site), so a package with no
            # wheel anywhere would silently get zero columns and prune every
            # dependent.  Fail loudly instead: an sdist-only package needs the
            # hand-written treatment numpy/h5py get.
            print(f"  SKIP {base}: no wheels (sdist-only) at {info['version']}", file=sys.stderr)
            skipped.append(base)
            continue
        lic = _LICENSE_OVERRIDE.get(base, lic)
        meta[base] = {
            **info,
            "wheels": wheels,
            "sdist": sdist,
            "kind": classify(wheels, interps),
            "license": lic,
        }
    for base, seed in sorted(SEED_PACKAGES.items()):
        if base in meta:
            print(f"  seed {base} shadowed by poetry.lock; lock wins", file=sys.stderr)
            continue
        try:
            wheels, sdist, lic = fetch_pypi(base, seed["version"])
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
            "sdist": sdist,
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

    if unknown := only - set(meta):
        print(
            f"FATAL: --only names unknown package(s): {', '.join(sorted(unknown))}", file=sys.stderr
        )
        return 1

    cols = compute_columns(meta, interps)

    # A column with NO wheel anywhere is pruned above.  That is deliberately
    # unchanged by sdist mode: cvcpkg only claims interpreter columns upstream
    # itself publishes a build for, so switching provenance never silently
    # widens the published matrix.
    survey = only if (only and not args.report_backends) else set(meta)
    blockers = resolve_source_modes(
        {b: m for b, m in meta.items() if b in survey},
        cols,
        out=out,
        interps=interps,
        cache=cache,
        pure_policy=args.pure_policy,
        forced=args.source_mode,
        platforms=[p for p in args.sdist_platforms.split(",") if p],
    )
    _report_blockers(blockers)
    if args.report_backends:
        for base, m in sorted(meta.items()):
            if "mode" in m:
                print(f"{m['mode']:6s} {base:28s} {m['reason']}")
        return 0

    counts = {"pure": 0, "abi3": 0, "cext": 0}
    modes = {"sdist": 0, "wheel": 0}
    emitted = kept = 0
    for base, m in sorted(meta.items()):
        if only and base not in only:
            continue
        for interp in cols[base]:
            d = out / f"{base}-cp{interp}"
            if not args.overwrite_hand_written and not is_generator_owned(d):
                print(f"  keep {d.name}: hand-written, not regenerated", file=sys.stderr)
                kept += 1
                continue
            _emit_column(out, base, m, interp, meta, cols)
            emitted += 1
        if cols[base]:
            counts[m["kind"]] += 1
            modes[m["mode"]] += 1
    npkgs = len(only) if only else len(meta)
    print(
        f"emitted {emitted} column recipes across {npkgs} packages "
        f"({counts['pure']} pure, {counts['abi3']} abi3, {counts['cext']} per-version; "
        f"{modes['sdist']} from source, {modes['wheel']} prebuilt)"
        + (f"; kept {kept} hand-written column(s)" if kept else ""),
        file=sys.stderr,
    )
    if only:
        # Targeted run: the rest of the universe was never emitted, so pruning
        # would delete recipes this run simply did not touch.
        print("targeted run (--only): skipping the stale/orphan sweep", file=sys.stderr)
    else:
        _prune_stale(out, meta, cols, interps)
    return 0


def _prune_stale(out: Path, meta: dict, cols: dict[str, list[str]], interps: list[str]) -> None:
    """Delete superseded recipe dirs for managed bases: the bare-name recipe
    (replaced by the column matrix) and columns no longer viable.

    Only dirs the generator owns are touched (is_prunable) — a real
    ``source.type: python_wheel`` (the historical shape) or the generator marker
    in the description (which every emitted recipe carries, including the
    from-source ``tarball`` ones).  So a same-named native recipe (the C++
    ``protobuf``) is never clobbered, and hand-written from-source columns
    (cffi-cp311, pyyaml-cp311, numpy-cp311, ...) survive because they carry
    neither — including the ones whose base IS in the managed set, whose only
    protection is this test."""
    import shutil

    marker = GENERATOR_MARKER
    removed = 0
    for base in meta:
        candidates = [out / base] + [out / f"{base}-cp{i}" for i in interps]
        keep = {out / f"{base}-cp{i}" for i in cols[base]}
        for d in candidates:
            if d in keep or not d.is_dir():
                continue
            if not is_prunable(d):
                print(f"  keep {d.name}: not a generator-owned recipe", file=sys.stderr)
                continue
            shutil.rmtree(d)
            removed += 1
    # Orphan sweep: a package that left poetry.lock (or a deleted seed) must
    # not leave generator-owned column dirs behind — they would keep
    # validating and publishing forever. Only dirs whose description carries
    # the generator marker are touched; hand-written -cpNNN recipes (numpy,
    # torch, wand, ...) never carry it.
    suffixes = tuple(f"-cp{i}" for i in interps)
    for d in sorted(out.iterdir()):
        if not d.is_dir() or not d.name.endswith(suffixes):
            continue
        base = re.sub(r"-cp3[0-9]{2}t?$", "", d.name)
        if base in meta:
            continue
        y = d / "recipe.yaml"
        if y.is_file() and marker in y.read_text(encoding="utf-8"):
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
    old = path.read_text(encoding="utf-8") if path.exists() else ""
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
    # Explicit LF: these files are shell scripts and YAML consumed on POSIX
    # builders; a regeneration run on Windows must not emit CRLF.
    path.write_text(body_with_rev.replace("{rev}", str(rev)), encoding="utf-8", newline="\n")


def sdist_platforms(kind: str, plats: list[str]) -> list[tuple[str, str]]:
    """(platform, script) matrix entries for a from-source column.

    A pure package still builds ONCE: the wheel it produces is py3-none-any, so
    the column stays ``platform: any`` exactly as the prebuilt-noarch one did --
    only the artifact's provenance changes.  A compiled package needs a build
    per platform, and windows needs PowerShell.

    The pure column also gets an explicit windows entry. A matrix entry names
    ONE script and ``any`` names build.sh, which cannot run on a Windows
    builder -- so a pure package was advertised as buildable everywhere and was
    in fact unbuildable from source there. The payload is noarch; the recipe
    was not. That gap is what left setuptools (and therefore every PEP-517
    backend, and therefore pillow and numpy) unbuildable on Windows."""
    if kind == "pure":
        return [("any", "build.sh"), ("windows", "build.ps1")]
    return [(p, "build.ps1" if p == "windows" else "build.sh") for p in plats]


def _emit_column(out, base, m, interp, meta, cols):
    """Emit one recipe: <base>-cp<interp>.

    ``m["mode"]`` selects the shape: ``sdist`` (from-source tarball, the
    default) or ``wheel`` (prebuilt PyPI wheel, the documented fallback).
    ``m["reason"]`` is recorded in the recipe so the choice is auditable from
    the recipe alone, not just from a regeneration log."""
    name = f"{base}-cp{interp}"
    d = out / name
    mode = m.get("mode", "wheel")
    arts = wheel_for_column(m, interp)
    abi = column_abi(m, interp, arts, mode)
    ver = col_version(interp)
    kind = m["kind"]
    dep_bases = deps_for_column(m, interp, set(meta))
    # Runtime deps are ALSO build deps: the post-install `import <pkg>` check
    # imports the package for real, and many packages eagerly import a runtime
    # dep at module load (sqlalchemy -> typing_extensions), so it must be staged
    # in the build prefix or the check fails.
    deps = [f"python{interp}", *(f"{b}-cp{interp}" for b in dep_bases)]
    # From-source columns additionally need the PEP-517 backend importable at
    # build time (--no-build-isolation); those are build-only edges.
    backends = backend_edges(m.get("build_requires", []), interp) if mode == "sdist" else []

    if mode == "sdist":
        flavor = {
            "pure": "pure-Python, built from the PyPI sdist",
            "abi3": f"C extensions compiled for cp{interp} from the PyPI sdist",
            "cext": f"C extensions compiled for cp{interp} from the PyPI sdist",
        }[kind]
    else:
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
    source_note = (
        f"    Built FROM SOURCE: cvcpkg fetches and sha256-verifies the PyPI sdist,\n"
        f"    and build.sh compiles the wheel with the prefix's own CPython {ver} —\n"
        f"    no third-party prebuilt binary enters the bundle.\n"
        if mode == "sdist"
        else f"    Prebuilt PyPI wheel (NOT built from source): {m.get('reason', 'unknown')}.\n"
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
        f"  tags: [python, {'source' if mode == 'sdist' else 'wheel'}]\n"
        f"  description: >-\n"
        f'    {m["pypi_name"]} {m["version"]} for CPython {ver} — the cp{interp} column of\n'
        # "wheel matrix" is the historical wording; keeping it on the prebuilt
        # path holds the diff of an unconverted recipe to the single added
        # reason line below, instead of churning every description.
        f"    cvcpkg's per-interpreter {'' if mode == 'sdist' else 'wheel '}matrix ({flavor});\n"
        f"    generated by tools/gen_python_recipes.py.\n"
        f"{source_note}"
        f"{free_threaded_note}"
    )

    if mode == "sdist":
        sd = m["sdist"]
        # source.type tarball (NOT python_wheel): cvcpkg fetches + verifies the
        # sdist and extracts it to CVC_SOURCE_DIR; strip_components drops the
        # '<name>-<version>/' wrapper every sdist carries.
        body += (
            "\n# From-source sdist, sha256 as published by PyPI for this exact version.\n"
            "source:\n  type: tarball\n"
            + _artifact_block(sd["url"], sd["digests"]["sha256"], "  ")
            + "  strip_components: 1\n"
        )
    else:
        body += "\nsource:\n  type: python_wheel\n  artifacts:\n"
        for key in ("any", *PLATFORM_TAGS):
            if key in arts:
                w = arts[key]
                body += f"    {key}:\n" + _artifact_block(
                    w["url"], w["digests"]["sha256"], "      "
                )

    body += f"\npython:\n  interpreter: python{interp}\n  abi: {abi}\n"
    # manylinux_min pins the glibc floor of a DOWNLOADED wheel; a from-source
    # build's floor is the builder's, so the field is meaningless there.
    if kind != "pure" and mode != "sdist":
        body += "  manylinux_min: manylinux_2_28\n"
    body += "\npatches: []\n\n"

    body += "depends:\n  build:\n" + "".join(f"    - name: {b}\n" for b in dict.fromkeys(deps))
    if backends:
        body += (
            "    # PEP-517 build backend, from this sdist's [build-system] requires.\n"
            "    # --no-build-isolation means pip will NOT fetch it, so it has to be\n"
            "    # in the build prefix already (build.sh bridges it onto sys.path).\n"
        ) + "".join(
            f"    - name: {b}\n" for b in dict.fromkeys(backends) if b not in dict.fromkeys(deps)
        )
    body += "  runtime:\n" + "".join(f"    - name: {r}\n" for r in dict.fromkeys(deps))
    if mode == "sdist":
        # pip and the compiler run ON the builder, so the interpreter is a host
        # tool as well as a dependency (see recipes/numpy-cp311).
        body += f"  host_tools:\n    - python{interp}\n"

    if base in SCRIPT_PACKAGES:
        # Console scripts collide across columns in bin/; the provides slot
        # declares the exclusion and doubles as the bare-name virtual for
        # `cvcpkg install <base>`.
        body += f"\nprovides:\n  - {base}\n"

    if mode == "sdist":
        plats = sdist_platforms(kind, m.get("platforms", SDIST_PLATFORMS))
    elif kind == "pure":
        plats = [("any", "build.sh")]
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

    windows = any(p == "windows" for p, _ in plats) or (
        mode != "sdist" and any(k.startswith("windows") for k in arts)
    )
    body += "\npackage:\n  files:\n"
    for g in _files_globs(m, base, interp, windows):
        body += f"    - {g}\n"

    floor = _RESURRECTED_FLOOR.get(base, 1) if not interp.endswith("t") else 1
    _write_recipe(d, body, floor=floor)

    check = m.get("check") or f"import {_toppkg(base)}"
    sh = _BUILD_SH_SDIST if mode == "sdist" else _BUILD_SH
    (d / "build.sh").write_text(
        sh.format(
            name=name,
            check=_sh_dq(check),
            abi=abi,
            interpreter=f"python{interp}",
            dist=m["pypi_name"],
            version=m["version"],
            backends=", ".join(backends) or "(none declared)",
        ),
        encoding="utf-8",
        newline="\n",
    )
    (d / "build.sh").chmod(0o755)
    ps1 = d / "build.ps1"
    if windows and mode == "sdist":
        ps1.write_text(
            _BUILD_PS1_SDIST.format(
                name=name, check=_ps1_sq(check), dist=m["pypi_name"], version=m["version"]
            ),
            encoding="utf-8",
            newline="\n",
        )
    elif windows and kind != "pure":
        ps1.write_text(
            _BUILD_PS1.format(name=name, check=_ps1_sq(check)), encoding="utf-8", newline="\n"
        )
    elif ps1.exists():
        ps1.unlink()


def _files_globs(m, base, interp, windows: bool) -> list[str]:
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
    if windows:
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


def _ps1_sq(s: str) -> str:
    """Escape for a PowerShell SINGLE-quoted literal: the only escape is '' .

    Without this a check containing an apostrophe — ``Image.new('L', (2, 2))``,
    ``matplotlib.use('Agg')`` — closes the literal early and the generated
    build.ps1 is a syntax error, which surfaces as a baffling parse failure
    partway through the build rather than as a bad check.
    """
    return s.replace("'", "''")


def _sh_dq(s: str) -> str:
    """Escape for a bash DOUBLE-quoted string (backslash, quote, $, backtick)."""
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        s = s.replace(a, b)
    return s


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

# From-source build script.  Modelled on recipes/h5py-cp311/build.sh minus the
# library-specific parts (HDF5_DIR, rpath rewriting): a pure-Python or plain
# setuptools/C-extension sdist needs nothing but the interpreter, the backend on
# sys.path, and pip.  A package that links a cvcpkg NATIVE library (numpy ->
# openblas, h5py -> hdf5) must NOT use this generated script: it needs an
# $ORIGIN/@loader_path rpath pass so the extension resolves the library out of
# the merged prefix.  Those stay hand-written.
_BUILD_SH_SDIST = """#!/usr/bin/env bash
# recipes/{name}/build.sh — build {dist} {version} FROM SOURCE (generated).
#
# WHY FROM SOURCE: a PyPI wheel is somebody else's compiled artifact, linked
# against libraries we did not build.  cvcpkg fetches and sha256-verifies the
# SDIST (source.type: tarball) instead, and this script compiles the wheel with
# the prefix's own interpreter, then installs it — so the bundle contains only
# things cvcpkg built.  pip still produces a real wheel (dist-info/RECORD/
# METADATA), so a consumer's later `pip install <other>` coexists and pip's
# resolver sees this package as satisfied.
#
# BUILD BACKEND: {backends}
# --no-build-isolation means pip does NOT download the PEP-517 backend into a
# throwaway venv (that would be both non-hermetic and impossible offline): the
# backend must ALREADY be importable.  It is declared in recipe.yaml as a
# depends.build edge, so it is staged into CVC_BUILD_PREFIX — a different prefix
# from the one cvc_python_exe's interpreter imports.  Step 2's PYTHONPATH bridge
# is what makes it importable; without it the build falls back to isolation and
# fails offline.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Platform toolchain/env.  Sourced conditionally: a noarch (platform: any)
# column can be claimed by a builder whose platform ships no env-*.sh.
_CVC_ENV="${{SCRIPT_DIR}}/../_common/env-${{CVC_PLATFORM:-linux}}.sh"
# shellcheck disable=SC1090
[ -f "${{_CVC_ENV}}" ] && . "${{_CVC_ENV}}"
# shellcheck disable=SC1091
. "${{SCRIPT_DIR}}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${{CVC_PYTHON_ABI:={abi}}}"
: "${{CVC_PYTHON_INTERPRETER:={interpreter}}}"
PY_EXE="$(cvc_python_exe)"
echo "{name}: building with ${{PY_EXE}}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
_D="${{CVC_PYTHON_ABI#cp}}"; _D="${{_D%t}}"
_PYMM="${{_D:0:1}}.${{_D:1}}"                 # cp311 -> 3.11
if [ -n "${{CVC_BUILD_PREFIX:-}}" ]; then
    _BP_SITE="${{CVC_BUILD_PREFIX}}/lib/python${{_PYMM}}/site-packages"
    export PYTHONPATH="${{_BP_SITE}}${{PYTHONPATH:+:${{PYTHONPATH}}}}"
fi

# ── 3. Build the wheel from the extracted sdist ─────────────────────────────
# --no-deps: transitive deps are cvcpkg recipes, resolved by the depends graph.
# --no-index: no network resolution — this is what makes air-gapped builds work.
WHEELHOUSE="${{CVC_BUILD_DIR:-${{CVC_SOURCE_DIR}}}}/wheelhouse"
mkdir -p "${{WHEELHOUSE}}"
"${{PY_EXE}}" -m pip wheel \\
    --no-build-isolation \\
    --no-deps \\
    --no-index \\
    --no-cache-dir \\
    --wheel-dir "${{WHEELHOUSE}}" \\
    "${{CVC_SOURCE_DIR}}"

# --no-deps means the wheelhouse holds exactly one wheel: ours.
WHEEL="$(find "${{WHEELHOUSE}}" -maxdepth 1 -name '*.whl' -print -quit)"
[ -n "${{WHEEL}}" ] || {{ echo "{name}: no wheel produced under ${{WHEELHOUSE}}" >&2; exit 1; }}
echo "{name}: built $(basename "${{WHEEL}}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
# stage_bundle ships the whole CVC_INSTALL_DIR tree, so installing --prefix into
# an empty dir with --no-deps is what keeps the bundle to just this package.
"${{PY_EXE}}" -m pip install \\
    --no-index \\
    --no-deps \\
    --no-compile \\
    --ignore-installed \\
    --prefix "${{CVC_INSTALL_DIR}}" \\
    "${{WHEEL}}"

# ── 5. Verify the staged package actually imports ──────────────────────────
# Drop the build-prefix bridge first: the check must exercise the RUNTIME
# closure, not accidentally import a build-only backend.
unset PYTHONPATH
cvc_python_check "{check}"
"""

_BUILD_PS1_SDIST = """# recipes/{name}/build.ps1 — build {dist} {version} FROM SOURCE (generated).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "{name}: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {{
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) {{ "$sp;$env:PYTHONPATH" }} else {{ $sp }}
}}

$root = if ($env:CVC_BUILD_DIR) {{ $env:CVC_BUILD_DIR }} else {{ $env:CVC_SOURCE_DIR }}
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) {{ throw "{name}: pip wheel failed ($LASTEXITCODE)" }}

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) {{ throw "{name}: no wheel produced under $wheelhouse" }}
Write-Output "{name}: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) {{ throw "{name}: pip install failed ($LASTEXITCODE)" }}

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck '{check}'
"""


if __name__ == "__main__":
    raise SystemExit(main())
