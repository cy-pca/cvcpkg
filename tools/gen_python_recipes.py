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

# Per-package platform overrides for compiled (``_PREFER_BINARY``) from-source
# columns.  SDIST_PLATFORMS above is the conservative default and deliberately
# omits the BSDs, because a GENERATED compiled package has never been proven to
# build there.  cython is the one exception, and for a concrete reason: it is
# the cythonizer the HAND-WRITTEN from-source columns of numpy, pyyaml and h5py
# invoke at build time, and those recipes DO build on all three BSDs (their
# native deps — openblas, yaml, hdf5 — already do).  A build dependency that
# does not cover a platform its dependent declares is a phantom closure edge
# that scripts/validate_all_recipes.py rejects, so cython must cover every
# native platform those consumers claim.  The claim is real, not aspirational:
# cython builds from its sdist with nothing but the interpreter + setuptools +
# wheel, and python311/312/313/313t already build on all six native platforms
# (windows included — python313t ships a real --disable-gil PCbuild), so there
# is a genuine build behind every entry.  Keyed by base name like the other
# per-package tables (_PREFER_BINARY, SCRIPT_PACKAGES, _LICENSE_OVERRIDE); the
# list is uniform across the four interpreter columns because sdist_platforms()
# is interpreter-independent — every column of a package shares one matrix.
_SDIST_PLATFORMS_OVERRIDE: dict[str, list[str]] = {
    "cython": ["linux", "macos", "windows", "freebsd", "netbsd", "openbsd"],
}

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

# Packages whose published sdist cannot build as shipped -- a packaging defect
# upstream, not a missing backend.  They stay on their (pure) prebuilt wheel
# until upstream fixes the sdist; the reason is printed and recorded in the
# recipe like every other fallback.
_UNBUILDABLE_SDIST: dict[str, str] = {
    # setup.py opens README.md for long_description, but the sdist ships only
    # README.rst, so every metadata build dies with FileNotFoundError.
    "ccxt": "PyPI sdist cannot build: setup.py reads README.md, which the sdist does not ship",
}


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

# Build backends whose OWN runtime deps must be staged into the build prefix.
# --no-build-isolation requires the backend importable, and importing it imports
# its runtime deps — but staging a backend as a build edge does NOT pull that
# closure in (a package's runtime closure is assembled only for ITS runtime).
# So a backend that carries runtime deps dies mid-build unless they ride along:
#   * hatchling -> pathspec/packaging/pluggy/trove-classifiers  (pyinstaller-cp313
#     failed with "No module named 'pathspec'" on the dev cluster)
#   * cffi 2.0  -> pycparser  (it dropped its vendored cffi/_pycparser copy;
#     cryptography-cp313 failed with "No module named 'pycparser'")
# For each of these that lands in a column's build closure, _emit_column stages
# its TRANSITIVE runtime closure as build-only edges.  Deliberately a curated set
# (not every backend): backends whose consumers build fine today — setuptools,
# flit-core (no runtime deps), meson-python (whose noarch closure is already
# resolved) — are left untouched rather than churned.  cffi is also reached as a
# plain runtime dep whose import-check runs cdef (weasyprint), so the walk is
# seeded from a runtime-dep cffi too.
_BACKEND_RUNTIME_CLOSURE = {"cffi", "hatchling"}

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
    "aioimaplib": ["imap-testing-server"],
    "alembic": ["alembic"],
    "autopep8": ["autopep8"],
    "black": ["black", "blackd"],
    "charset-normalizer": ["normalizer"],
    "cython": ["cython", "cythonize", "cygdb"],
    "email-validator": ["email_validator"],
    "fabric": ["fab"],
    "faker": ["faker"],
    "fastapi": ["fastapi"],
    "httpx": ["httpx"],
    "idna": ["idna"],
    "invoke": ["invoke", "inv"],
    "ipython": ["ipython", "ipython3"],
    "jmespath": ["jp.py"],
    "mako": ["mako-render"],
    "pycodestyle": ["pycodestyle"],
    "pydle": ["pydle", "pydle-irccat"],
    "rosbags": ["rosbags-convert"],
    "pygments": ["pygmentize"],
    "pytest": ["pytest", "py.test"],
    "scikit-build-core": ["scikit-build", "scikit-build-core"],
    "sympy": ["isympy"],
    "tqdm": ["tqdm"],
    "versioneer": ["versioneer"],
    # NEW at 2026.6.1.19: setup.py gained a console_scripts entry point the old
    # 2024.10.16 pin did not have, so moving the pin silently started shipping
    # bin/trove-classifiers in every column.  package.files is declarative only
    # (stage_bundle copies the whole install tree), so the script ships either
    # way; listing it here is what turns an undeclared last-extract-wins bin/
    # overlap into a declared, mutually-exclusive provides slot.
    "trove-classifiers": ["trove-classifiers"],
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
# Prefix for the import checks of packages that load jaxlib (jax, optax,
# equinox).  jaxlib 0.8.2's x86 build needs AVX and refuses to import without
# it (lat has none), so `avx` gates every step that imports jax; elsewhere the
# check imports and computes.  _NO_AVX is what the check prints when it skips.
_AVX_GUARD = (
    "import os, importlib.metadata as md; "
    "_NO_AVX = 'not imported: no AVX on this CPU'; "
    "avx = 'avx' in (open('/proc/cpuinfo').read().split() "
    "if os.path.exists('/proc/cpuinfo') else ['avx']); "
)

SEED_PACKAGES = {
    "altgraph": {
        "version": "0.17.5",
        "license": "MIT",
        "deps": [],
        "files": ["altgraph/", "altgraph-*.dist-info/"],
        "check": "import altgraph",
    },
    # hatchling (PyInstaller's PEP-517 backend) is defined once, in the
    # build-backends section below — a second entry here was a merge artifact
    # that Python's dict literal silently collapsed (last key wins).
    "macholib": {
        "version": "1.16.4",
        "license": "MIT",
        "deps": ["altgraph"],
        "files": ["macholib/", "macholib-*.dist-info/"],
        "check": "import macholib",
    },
    "poetry-core": {
        # grl-snam's PEP-517 backend (CVC-Lab/GRL-SNAM builds with
        # poetry.core.masonry.api): a BUILD dep of the grl-snam-cp31X column
        # recipes, which pip-install their sdist with --no-build-isolation.
        # Self-hosting backend (its own [build-system] requires is empty and
        # it builds itself via backend-path), so it terminates the backend
        # chain. The wheel ships only the `poetry/core/` PEP-420 namespace
        # subpackage (no poetry/__init__.py) — files/check derive from the
        # _TOPPKG entry ("poetry.core").
        "version": "2.4.1",
        "license": "MIT",
        "deps": [],
    },
    "pefile": {
        "version": "2024.8.26",
        "license": "MIT",
        "deps": [],
        "files": ["pefile.py", "peutils.py", "ordlookup/", "pefile-*.dist-info/"],
        "check": "import pefile",
    },
    "pyinstaller-hooks-contrib": {
        "version": "2026.6",
        "license": "Apache-2.0 OR GPL-2.0-or-later",
        "deps": ["packaging", "setuptools"],
        "files": [
            "_pyinstaller_hooks_contrib/",
            "pyinstaller_hooks_contrib-*.dist-info/",
        ],
        "check": "import _pyinstaller_hooks_contrib",
    },
    "pywin32-ctypes": {
        "version": "0.2.3",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["win32ctypes/", "pywin32_ctypes-*.dist-info/"],
        "check": "import win32ctypes",
    },
    # --- scipy's build stack --------------------------------------------
    # pythran compiles scipy's ahead-of-time-annotated Python to C++, and
    # scipy's meson looks for it during configure.  All four are BUILD deps
    # of scipy rather than anything a user imports.
    "gast": {
        "version": "0.7.0",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["gast/", "gast-*.dist-info/"],
        "check": "import gast",
    },
    "beniget": {
        "version": "0.5.0",
        "license": "BSD-3-Clause",
        "deps": ["gast"],
        "files": ["beniget/", "beniget-*.dist-info/"],
        "check": "import beniget",
    },
    "ply": {
        "version": "3.11",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["ply/", "ply-*.dist-info/"],
        "check": "import ply",
    },
    "pythran": {
        "version": "0.18.1",
        "license": "BSD-3-Clause",
        "deps": ["beniget", "gast", "numpy", "ply", "setuptools"],
        "files": ["pythran/", "omp/", "pythran-*.dist-info/"],
        "check": "import pythran",
    },
    # --- Flask, for the platoon-sim scene viewer ---------------------------
    # scene_viewer/viewer_app.py is a Flask server, and the platoon-sim
    # web demo is recorded from it. click and jinja2 already arrive via poetry.lock.
    # All pure Python.
    "itsdangerous": {
        "version": "2.2.0",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["itsdangerous/", "itsdangerous-*.dist-info/"],
        "check": "import itsdangerous",
    },
    "blinker": {
        "version": "1.9.0",
        "license": "MIT",
        "deps": [],
        "files": ["blinker/", "blinker-*.dist-info/"],
        "check": "import blinker",
    },
    "werkzeug": {
        "version": "3.1.8",
        "license": "BSD-3-Clause",
        "deps": ["markupsafe"],
        "files": ["werkzeug/", "werkzeug-*.dist-info/"],
        "check": "import werkzeug",
    },
    "flask": {
        "version": "3.1.3",
        "license": "BSD-3-Clause",
        "deps": ["blinker", "click", "itsdangerous", "jinja2", "markupsafe", "werkzeug"],
        "files": ["flask/", "flask-*.dist-info/"],
        "check": "import flask",
    },
    # Not part of scipy: geometry-scene-gen imports trimesh eagerly from
    # scene_gen/__init__, so the movement-bundle export needs it in the
    # prefix.  Pure Python over numpy.
    "trimesh": {
        "version": "5.0.0",
        "license": "MIT",
        "deps": ["numpy"],
        "files": ["trimesh/", "trimesh-*.dist-info/"],
        "check": "import trimesh",
    },
    # trove-classifiers is defined once, in the build-backends section below
    # (with its calver dep) — a duplicate here was a merge artifact that
    # Python's dict literal silently collapsed (last key wins).
    "black": {
        "version": "26.5.1",
        "license": "MIT",
        "deps": ["click", "mypy-extensions", "packaging", "pathspec", "platformdirs", "pytokens"],
        "files": ["black/", "blackd/", "blib2to3/", "_black_version.py", "black-*.dist-info/"],
        "check": "import black",
    },
    "cython": {
        # >= 3.2.0 is a hard floor from scipy's meson.build ("SciPy requires
        # Cython >= 3.2.0"), which fails configure rather than degrading.
        "version": "3.2.9",
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
        "version": "3.1.6",
        "license": "BSD-3-Clause",
        "deps": ["markupsafe"],
        "files": ["jinja2/", "jinja2-*.dist-info/"],
        "check": "import jinja2",
    },
    "meson-python": {
        "version": "0.18.0",
        "license": "MIT",
        "deps": ["packaging", "pyproject-metadata"],
        # Not in [build-system] requires (upstream expects ninja on PATH or the
        # PyPI ninja wheel): meson-python builds itself with meson, which runs
        # ninja.  cvcpkg 2.5.4 stopped staging meson's own build deps, so the
        # Windows column failed "Could not find ninja version 1.8.2 or newer"
        # (job 23029).  Its consumers get both from the rule in column_edges.
        "build_deps": ["ninja"],
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
        "version": "3.6.1",
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
        # NOT in [build-system] requires, and deliberately declared anyway.
        # setuptools validates project.classifiers against trove_classifiers
        # only when that package happens to be IMPORTABLE: absent, it skips
        # validation and the build passes; present-and-stale, it hard-fails.
        # The fleet builder shares one prefix between jobs, so a sibling
        # recipe's trove-classifiers leaked in and decided this build's
        # outcome — pytest 9.1.1 declares "Programming Language :: Python ::
        # 3.15", which the old 2024.10.16 pin rejected.  Declaring the edge
        # makes the input explicit: the resolver stages the CURRENT
        # trove-classifiers and the DAG orders it before this column, instead
        # of the result depending on what else the builder happened to build.
        "build_deps": ["trove-classifiers"],
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
        # THE classifier database: hatchling imports it unconditionally, and
        # setuptools' pyproject validation uses it whenever it is importable.
        # A stale copy fails any sdist declaring a classifier newer than the
        # pin — 2024.10.16 predates "Programming Language :: Python :: 3.15"
        # and so blocked black (hatchling) and pytest (setuptools) outright.
        # Keep this current; it is a data package, not an API surface.
        #
        # Calendar-versioned. This was pinned to a THREE-component release
        # because a four-component YYYY.M.D.HH does not parse as SemVer, but
        # 2024.10.16 is the LAST such release upstream ever made — every
        # release since is four-component, so the pin could never move again.
        # semver() now encodes the fourth component as a pre-release
        # (2026.6.1.19 -> 2026.6.1-19), which parses and sorts correctly.
        "version": "2026.6.1.19",
        "license": "Apache-2.0",
        "deps": ["calver"],
        "files": ["trove_classifiers/", "trove_classifiers-*.dist-info/"],
        "check": "import trove_classifiers",
    },
    "hatchling": {
        # Match master's pin (added there by 7ec083b, PyInstaller-from-source,
        # which needed the same backend). Two branches packaging the same
        # backend at different versions is a pointless conflict; the newer one
        # wins.
        "version": "1.31.0",
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
        # 3.12+: 3.10.1 predates PEP 639 and rejects a string `license` field
        # ("license field should be <class 'dict'>, not <class 'str'>"), which
        # is what typing-extensions 4.15 and other modern sdists now declare.
        "version": "3.12.0",
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
    "scikit-build-core": {
        # coincurve's PEP-517 build drives its CMake project through
        # scikit-build-core (as a hatch build hook), so the hand-written
        # coincurve-cp31X columns stage this as a build edge.  Pure Python.
        "version": "1.1.1",
        "license": "Apache-2.0",
        "deps": ["packaging", "pathspec"],
        # hatch-vcs (a [build-system] requirement) imports setuptools_scm, and
        # staging hatch-vcs as a build edge does not stage its runtime deps.
        "build_deps": ["setuptools-scm", "setuptools"],
        "files": ["scikit_build_core/", "scikit_build_core-*.dist-info/"],
        "check": "import scikit_build_core.build",
    },
    "nanobind": {
        # drjit 1.5.0 and mitsuba 3.9.1 both pin nanobind==2.14.0 in their
        # [build-system] requires: under scikit-build their CMake runs
        # `python -m nanobind --cmake_dir` and find_package()s THIS copy, not
        # the ext/nanobind submodule.  The wheel is header/source-only
        # (py3-none-any), with tsl::robin_map vendored under ext/.
        "version": "2.14.0",
        "license": "BSD-3-Clause",
        "deps": [],
        # scikit-build-core configures and installs nanobind's CMake project
        # even though nothing compiles, so it needs cmake and ninja (which it
        # asks for through get_requires_for_build_wheel, never consulted under
        # --no-build-isolation), and it imports packaging and pathspec, its own
        # runtime deps, which a backend build edge does not stage.
        "build_deps": ["cmake", "ninja", "packaging", "pathspec"],
        "files": ["nanobind/", "*.dist-info/"],
        "check": (
            "import os, nanobind; "
            "assert os.path.isfile(os.path.join(nanobind.cmake_dir(), 'nanobind-config.cmake')); "
            "assert os.path.isfile(os.path.join(nanobind.include_dir(), 'nanobind', 'nanobind.h')); "
            "assert os.path.isdir(os.path.join(os.path.dirname(nanobind.__file__), "
            "'ext', 'robin_map', 'include', 'tsl'))"
        ),
    },
    # ── plotting / imaging / schema stack ───────────────────────────────────
    # pillow is the load-bearing one: the scene-buffer ingestion path reads
    # the scene navmask.png raster through PIL, so a planner cannot ingest a
    # scene without it. matplotlib and imageio are the grl_snam demo/capture
    # path; jsonschema validates movement_bundle.v1 exports against the
    # contract schema.
    # NOTE: the pillow-cp31X recipes on disk are HAND-CONVERTED (no generator
    # marker): they declare native-library edges (zlib, libjpeg-turbo, tiff,
    # freetype, libwebp + their flat closures), pass require/disable feature
    # config-settings so codec support cannot silently vanish, and run an
    # $ORIGIN rpath pass — none of which this generator can infer (see
    # is_generator_owned).  The seed stays because matplotlib/imageio resolve
    # their `pillow` dep edges against this universe; a regeneration prints
    # "keep pillow-cp31X: hand-written" instead of overwriting.
    "pillow": {
        "version": "12.3.0",
        "license": "MIT-CMU",
        "deps": [],
        # Capped: the hand conversion covers exactly these columns.  Without
        # the cap a full regeneration would see 12.3.0's cp313t wheels, emit a
        # GENERATED pillow-cp313t (no native edges, silently codec-less) and
        # let matplotlib/imageio claim cp313t columns over it.  Extend the cap
        # only together with a hand-written, verified pillow-cp313t.
        "interpreters": ["311", "312", "313"],
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
        # 4.63.0, up from 4.55.3: weasyprint >= 67.0 floors at
        # fonttools[woff]>=4.59.2, and pinning weasyprint back to dodge that
        # would have frozen the whole contract-PDF set a year behind.  Nothing
        # else in the catalog objects — matplotlib asks for >=4.22.0, the
        # columns are unchanged (cp311/312/313; there is no cp313t either
        # way), and the licence is still MIT.
        "version": "4.63.0",
        "license": "MIT",
        "deps": [],
        "files": ["fontTools/", "fonttools-*.dist-info/"],
        "check": "import fontTools",
    },
    # contourpy is plain generated output again.  It was hand-converted for two
    # revisions because meson's dependency('pybind11') could not find the staged
    # pybind11 — bin/pybind11-config had a dead ephemeral-prefix shebang, so the
    # config-tool probe failed and build.sh had to hand-feed the .pc dir and the
    # header dir.  Relocatable console-script shebangs (pybind11 2.13.6+cvc.3)
    # fixed that at the source: the build prefix's bin/ is first on PATH, so
    # meson's Pybind11ConfigToolDependency resolves with no help from a recipe.
    "contourpy": {
        "version": "1.3.1",
        "license": "BSD-3-Clause",
        "deps": ["numpy"],
        # Capped like pillow: a cp313t column would dangle on pybind11-cp313t,
        # which is not published.  Extend only alongside that column.
        "interpreters": ["311", "312", "313"],
        "files": ["contourpy/", "contourpy-*.dist-info/"],
        "check": "import contourpy",
    },
    # NOTE: the matplotlib-cp31X recipes on disk are HAND-CONVERTED (no
    # generator marker): they link cvcpkg's freetype + qhull via
    # -Dsystem-freetype/-Dsystem-qhull (the default meson wraps DOWNLOAD both,
    # impossible on offline builders), resolve the staged pybind11 the way
    # contourpy does, stamp an $ORIGIN RUNPATH and verify a real Agg render.
    # The seed stays for dep-edge resolution and column viability.
    "matplotlib": {
        "version": "3.10.0",
        "license": "PSF-2.0",
        "deps": [
            "contourpy",
            "cycler",
            "fonttools",
            "kiwisolver",
            "numpy",
            "packaging",
            "pillow",
            "pyparsing",
            "python-dateutil",
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
    # ── RL stack ────────────────────────────────────────────────────────────
    # gymnasium is the RL environment API the grl-snam line of work builds
    # its training environments on, alongside pillow above.  All three are
    # pure Python; gymnasium's compiled leg is numpy, a hand-written column.
    "cloudpickle": {
        "version": "3.1.2",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["cloudpickle/", "cloudpickle-*.dist-info/"],
        # A real closure round-trip, not just an import: pickling live
        # functions is the one thing gymnasium needs cloudpickle FOR
        # (vectorized-env workers serialize the env constructor).
        "check": (
            "import cloudpickle, pickle; "
            "f = pickle.loads(cloudpickle.dumps(lambda x: x + 1)); "
            "assert f(1) == 2"
        ),
    },
    "farama-notifications": {
        # License is MIT per the sdist's LICENSE file and the PyPI trove
        # classifier; the metadata's `license` field itself is empty, which
        # _spdx would otherwise report as NOASSERTION.
        "version": "0.0.6",
        "license": "MIT",
        "deps": [],
        "files": ["farama_notifications/", "farama_notifications-*.dist-info/"],
        "check": "import farama_notifications",
    },
    "gymnasium": {
        "version": "1.3.0",
        "license": "MIT",
        # The unconditional requires-dist set; the extras (atari, box2d,
        # mujoco, ...) are optional env families this closure does not need.
        "deps": ["cloudpickle", "farama-notifications", "numpy", "typing-extensions"],
        "files": ["gymnasium/", "gymnasium-*.dist-info/"],
        # Constructing a Box space exercises the numpy path RL spaces
        # exercise, beyond a bare import.
        "check": "import gymnasium; gymnasium.spaces.Box(-1.0, 1.0, (3,))",
    },
    # ── LieSPHGP (CVC-Lab/LieSPHGP requirements.txt) ────────────────────────
    # The port-Hamiltonian GP research code libcvc's navigator work is ported
    # from.  Its JAX models need jax/jaxlib/optax/equinox; the dataset tools need
    # pandas and rosbags.  Everything here is the non-JAX part of that closure
    # plus the pure-Python leaves the JAX packages stand on.  Pins follow the
    # numpy rule: the newest release that still covers every ABI cvcpkg ships.
    "absl-py": {
        # optax's only non-JAX runtime dependency.
        "version": "2.5.1",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["absl/", "absl_py-*.dist-info/"],
        "check": "from absl import flags, logging; logging.set_verbosity(logging.INFO)",
    },
    "opt-einsum": {
        # jax's contraction-order planner.  numpy is an optional backend, not a
        # dependency, so the check plans from shapes alone.
        "version": "3.4.0",
        "license": "MIT",
        "deps": [],
        "files": ["opt_einsum/", "opt_einsum-*.dist-info/"],
        "check": (
            "import opt_einsum; "
            "path, info = opt_einsum.contract_path('ij,jk,kl->il', (2, 3), (3, 4), (4, 5), "
            "shapes=True); assert len(path) == 2"
        ),
    },
    "wadler-lindig": {
        # jaxtyping's and equinox's pretty-printer.
        "version": "0.1.7",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["wadler_lindig/", "wadler_lindig-*.dist-info/"],
        "check": "import wadler_lindig as wl; assert wl.pformat([1, 2]) == '[1, 2]'",
    },
    "jaxtyping": {
        # Despite the name it does not import jax: the annotations work for
        # numpy too, which is what lets it build and check before jaxlib exists.
        "version": "0.3.11",
        "license": "MIT",
        "deps": ["wadler-lindig"],
        "files": ["jaxtyping/", "jaxtyping-*.dist-info/"],
        "check": "from jaxtyping import Float, Shaped; assert Float and Shaped",
    },
    "ruamel-yaml": {
        # rosbags reads ROS 2 metadata.yaml through it.  0.19 dropped the
        # ruamel.yaml.clib requirement (the C loader is an extra now), so the
        # package is pure Python.
        "version": "0.19.1",
        "license": "MIT",
        "deps": [],
        "files": ["ruamel/", "ruamel_yaml-*.dist-info/"],
        "check": (
            "import io; from ruamel.yaml import YAML; y = YAML(); "
            "d = y.load('a: [1, 2]\\n'); s = io.StringIO(); y.dump(d, s); "
            "assert s.getvalue() == 'a: [1, 2]\\n', s.getvalue()"
        ),
    },
    "versioneer": {
        # Build-only: pandas' [build-system] requires names versioneer[toml].
        # The [toml] extra is tomli on Python < 3.11, i.e. nothing here.
        "version": "0.29",
        "license": "Unlicense",
        "deps": [],
        "files": ["versioneer.py", "versioneer-*.dist-info/"],
        "check": "import versioneer; assert versioneer.get_versions",
    },
    # jax, optax, equinox: pure Python on top of the PREBUILT jaxlib columns
    # (hand-written, see HANDWRITTEN_DEP_BASES: PyPI ships no jaxlib sdist).
    # 0.8.2 is what LieSPHGP's Phase A ran.  jaxlib 0.8.2 refuses to import on a
    # CPU without AVX (lat), so each check verifies the installed version there
    # and imports only where it can; `avx` gates every step that imports jax.
    "jax": {
        "version": "0.8.2",
        "license": "Apache-2.0",
        "deps": ["jaxlib", "ml-dtypes", "numpy", "opt-einsum", "scipy"],
        "files": ["jax/", "jax-*.dist-info/"],
        # A dense solve: jaxlib's CPU LAPACK kernels come from scipy's
        # cython_lapack, so this exercises cvcpkg's scipy column too.
        "check": (
            _AVX_GUARD + "assert md.version('jax') == '0.8.2', md.version('jax'); "
            "jnp = __import__('jax.numpy', fromlist=['numpy']) if avx else None; "
            "a = jnp.array([[3.0, 1.0], [1.0, 2.0]]) if avx else None; "
            "b = jnp.array([9.0, 8.0]) if avx else None; "
            "assert (not avx) or bool(jnp.allclose(a @ jnp.linalg.solve(a, b), b)); "
            "print('jax', md.version('jax'), 'solve OK' if avx else _NO_AVX)"
        ),
    },
    "optax": {
        # optax.contrib.prodigy is what LieSPHGP trains with.  0.2.8 dropped
        # the chex dependency.
        "version": "0.2.8",
        "license": "Apache-2.0",
        "deps": ["absl-py", "jax", "jaxlib", "numpy"],
        "files": ["optax/", "optax-*.dist-info/"],
        "check": (
            _AVX_GUARD + "assert md.version('optax') == '0.2.8', md.version('optax'); "
            "contrib = __import__('optax.contrib', fromlist=['prodigy']) if avx else None; "
            "assert (not avx) or callable(contrib.prodigy); "
            "optax = __import__('optax') if avx else None; "
            "jnp = __import__('jax.numpy', fromlist=['numpy']) if avx else None; "
            "tx = optax.adam(0.1) if avx else None; "
            "u = tx.update(jnp.ones(3), tx.init(jnp.zeros(3)))[0] if avx else None; "
            "assert (not avx) or float(jnp.sum(u)) < 0; "
            "print('optax', md.version('optax'), 'adam + prodigy OK' if avx else _NO_AVX)"
        ),
    },
    "equinox": {
        # Used by LieSPHGP's NeuralSDE baseline only.
        "version": "0.13.8",
        "license": "Apache-2.0",
        "deps": ["jax", "jaxtyping", "typing-extensions", "wadler-lindig"],
        "files": ["equinox/", "equinox-*.dist-info/"],
        "check": (
            _AVX_GUARD + "assert md.version('equinox') == '0.13.8', md.version('equinox'); "
            "eqx = __import__('equinox') if avx else None; "
            "jax = __import__('jax') if avx else None; "
            "jnp = __import__('jax.numpy', fromlist=['numpy']) if avx else None; "
            "m = eqx.nn.Linear(2, 3, key=jax.random.PRNGKey(0)) if avx else None; "
            "assert (not avx) or m(jnp.ones(2)).shape == (3,); "
            "print('equinox', md.version('equinox'), 'Linear OK' if avx else _NO_AVX)"
        ),
    },
    "ml-dtypes": {
        # bfloat16 / float8 / int4 numpy dtypes; jax and jaxlib both import
        # it.  0.5.4, not 0.6.0: 0.6 has no cp313t build.  The sdist vendors the
        # Eigen headers it compiles against (third_party/eigen), so the build
        # needs no native cvcpkg library.
        "version": "0.5.4",
        "license": "Apache-2.0",
        "deps": ["numpy"],
        "files": ["ml_dtypes/", "ml_dtypes-*.dist-info/"],
        "check": (
            "import numpy as np, ml_dtypes; "
            "a = np.array([1.5, -2.0], dtype=ml_dtypes.bfloat16); "
            "assert (a.astype(np.float32) == np.array([1.5, -2.0], np.float32)).all(); "
            "assert ml_dtypes.finfo(ml_dtypes.float8_e4m3fn).max == 448"
        ),
    },
    "rosbags": {
        # ROS 1/2 bag reader/writer with no ROS install; LieSPHGP converts the
        # Marinarium recordings with it.  Its compiled legs are hand-written
        # columns linking cvcpkg's native libraries (apsw -> sqlite,
        # backports-zstd -> zstd, lz4 -> lz4; see HANDWRITTEN_DEP_BASES), and
        # neither apsw nor backports.zstd has a cp313t build, so neither does
        # this.  typing-extensions is a < 3.12 marker upstream, kept as a plain
        # edge (seed deps carry no markers).
        "version": "0.11.9",
        "license": "Apache-2.0",
        "deps": ["apsw", "backports-zstd", "lz4", "numpy", "ruamel-yaml", "typing-extensions"],
        "files": ["rosbags/", "rosbags-*.dist-info/"],
        # A ROS 2 bag written and read back: the sqlite3 storage plugin is
        # apsw, and the per-message zstd compression is backports.zstd.
        "check": (
            "import pathlib, tempfile; "
            "from rosbags.rosbag2 import CompressionFormat, CompressionMode, "
            "StoragePlugin, Writer; "
            "from rosbags.highlevel import AnyReader; "
            "from rosbags.typesys import Stores, get_typestore; "
            "ts = get_typestore(Stores.ROS2_HUMBLE); "
            "S = ts.types['std_msgs/msg/String']; "
            "d = pathlib.Path(tempfile.mkdtemp()) / 'bag'; "
            "w = Writer(d, version=8, storage_plugin=StoragePlugin.SQLITE3); "
            "w.set_compression(CompressionMode.MESSAGE, CompressionFormat.ZSTD); "
            "w.open(); "
            "c = w.add_connection('/chatter', S.__msgtype__, typestore=ts); "
            "w.write(c, 42, ts.serialize_cdr(S(data='cvcpkg'), S.__msgtype__)); "
            "w.close(); "
            "r = AnyReader([d], default_typestore=ts); r.open(); "
            "got = [r.deserialize(raw, con.msgtype).data for con, t, raw in r.messages()]; "
            "r.close(); assert got == ['cvcpkg'], got"
        ),
    },
    "pandas": {
        # 3.0.3, not 3.0.6: 3.0.4 dropped cp313t.  The unconditional
        # requires-dist is numpy + python-dateutil; tzdata is upstream's
        # Windows/emscripten fallback and carried as a plain edge, as faker's
        # is, because columns have no platform markers.
        "version": "3.0.3",
        "license": "BSD-3-Clause",
        "deps": ["numpy", "python-dateutil", "tzdata"],
        "files": ["pandas/", "pandas-*.dist-info/"],
        # What LieSPHGP's dataset tools do with it: read a CSV, sort, dedupe.
        "check": (
            "import io, pandas as pd; "
            "df = pd.read_csv(io.StringIO('t,x\\n2,b\\n1,a\\n1,a\\n'))"
            ".sort_values('t').drop_duplicates(subset='t'); "
            "assert df['t'].tolist() == [1, 2] and df['x'].tolist() == ['a', 'b']"
        ),
    },
    # ── Blender 5.2's bundled Python modules ──
    # Blender's own build pip-installs these into the Python it bundles
    # (build_files/build_environment/cmake/versions.cmake); cvcpkg builds
    # Blender against the prefix's python313 instead (WITH_PYTHON_INSTALL=OFF),
    # so they have to be columns of that interpreter.  The extensions add-on
    # (bl_pkg) does not register without cattrs (_bpy_internal/http/
    # downloader.py), blend_render_info.py imports zstandard, and the text
    # editor's autopep8 operator needs autopep8 + pycodestyle.  Versions are
    # Blender 5.2.3's pins.  attrs and typing-extensions (cattrs' deps) come
    # from poetry.lock.
    "cattrs": {
        # exceptiongroup is a python_version < "3.11" requirement: no column.
        "version": "25.1.1",
        "license": "MIT",
        "deps": ["attrs", "typing-extensions"],
        # cattr/ is the legacy import name, still shipped beside cattrs/.
        "files": ["cattrs/", "cattr/", "cattrs-*.dist-info/"],
        # An attrs class structured from (and unstructured back to) plain data,
        # a validation error on a bad field, and the stdlib-json preconf
        # converter round-trip.
        "check": (
            "import unittest, attrs, cattrs; "
            "from cattrs.preconf.json import make_converter; "
            "P = attrs.make_class('P', {'x': attrs.field(type=int), "
            "'tags': attrs.field(type=list[str])}); "
            "B = attrs.make_class('B', {'p': attrs.field(type=P), 'n': attrs.field(type=float)}); "
            "c = cattrs.Converter(); "
            "b = c.structure({'p': {'x': '3', 'tags': ['a']}, 'n': 1}, B); "
            "assert b == B(P(3, ['a']), 1.0), b; "
            "assert c.unstructure(b) == {'p': {'x': 3, 'tags': ['a']}, 'n': 1.0}, c.unstructure(b); "
            "unittest.TestCase().assertRaises(cattrs.ClassValidationError, c.structure, "
            "{'p': {'x': 'no', 'tags': []}, 'n': 1}, B); "
            "j = make_converter(); "
            "assert j.loads(j.dumps(b), B) == b"
        ),
    },
    "fastjsonschema": {
        # PyPI says only "BSD"; the LICENSE file is the 3-clause text.
        "version": "2.21.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["fastjsonschema/", "fastjsonschema-*.dist-info/"],
        # Compile a schema to Python, then validate: a default filled in, a
        # minimum violated, a required property missing.
        "check": (
            "import unittest, fastjsonschema; "
            "v = fastjsonschema.compile({'type': 'object', 'required': ['name'], 'properties': "
            "{'name': {'type': 'string'}, 'n': {'type': 'integer', 'minimum': 0, 'default': 7}}}); "
            "assert v({'name': 'x'}) == {'name': 'x', 'n': 7}; "
            "unittest.TestCase().assertRaisesRegex(fastjsonschema.JsonSchemaValueException, "
            "'must be bigger than or equal to 0', v, {'name': 'x', 'n': -1}); "
            "unittest.TestCase().assertRaises(fastjsonschema.JsonSchemaValueException, v, {'n': 1})"
        ),
    },
    "zstandard": {
        # Built against the libzstd 1.5.7 its sdist bundles (zstd/zstd.c), not
        # cvcpkg's zstd recipe, unlike backports-zstd:
        #   * upstream guarantees the module only with the bundled copy: it
        #     uses zstd's unstable (ZSTD_STATIC_LINKING_ONLY) API, and the
        #     load-time guard only checks libzstd >= 1.5.6, so a later cvcpkg
        #     zstd release could change those semantics and still load;
        #   * against a system libzstd it compiles without zstd's private
        #     pool.h API, which drops multi_compress_to_buffer and
        #     multi_decompress_to_buffer from the module (backend_features);
        #   * the bundled copy is compiled into the extension as one file with
        #     hidden visibility, so it does not clash with the libzstd Blender
        #     itself links;
        #   * that is the setup Blender's own bundled zstandard has, and the
        #     version is the one cvcpkg's zstd recipe ships;
        #   * --system-zstd is a setup.py argv switch the generated pip build
        #     cannot pass, so linking cvcpkg's zstd would take a hand-written
        #     recipe; backports-zstd, the one that does, builds on linux only.
        # The extension is BSD-3-Clause; the bundled zstd is BSD-3-Clause OR
        # GPL-2.0-only.  No cp313t column: upstream publishes no free-threaded
        # wheel for 0.25.0.  cffi (a [build-system] requirement) also builds
        # the optional cffi backend; the C backend is the one imported, so cffi
        # stays build-only, as upstream's `cffi` extra leaves it.
        "version": "0.25.0",
        "license": "BSD-3-Clause AND (BSD-3-Clause OR GPL-2.0-only)",
        "deps": [],
        "files": ["zstandard/", "zstandard-*.dist-info/"],
        # The C backend is in use and is the bundled 1.5.7 with the pool
        # APIs; one-shot, streaming (blend_render_info.py reads .blend files
        # through stream_reader) and multi-threaded round trips.
        "check": (
            "import io, zstandard as zstd; "
            "assert zstd.backend == 'cext', 'C backend not used: ' + zstd.backend; "
            "assert zstd.ZSTD_VERSION == (1, 5, 7), zstd.ZSTD_VERSION; "
            "assert {'multi_compress_to_buffer', 'multi_decompress_to_buffer'} "
            "<= zstd.backend_features, zstd.backend_features; "
            "data = b'cvcpkg zstandard ' * 4096; "
            "blob = zstd.ZstdCompressor(level=19).compress(data); "
            "assert len(blob) < len(data) // 50, len(blob); "
            "assert zstd.ZstdDecompressor().decompress(blob) == data; "
            "assert zstd.ZstdDecompressor().stream_reader(io.BytesIO(blob)).read() == data; "
            "mt = zstd.ZstdCompressor(level=3, threads=2).compress(data); "
            "assert zstd.ZstdDecompressor().decompress(mt) == data"
        ),
    },
    "pycodestyle": {
        # "Expat license" upstream: SPDX MIT.
        "version": "2.13.0",
        "license": "MIT",
        "deps": [],
        "files": ["pycodestyle.py", "pycodestyle-*.dist-info/"],
        # Two lines, two known errors: E401 (multiple imports) and E225.
        "check": (
            "import pycodestyle; "
            "sg = pycodestyle.StyleGuide(quiet=True); "
            "r = sg.init_report(); "
            "sg.input_file('t.py', lines=['import os, sys\\n', 'x=1\\n']); "
            "assert r.get_count() == 2 and {'E401', 'E225'} <= set(r.counters), dict(r.counters)"
        ),
    },
    "autopep8": {
        # tomli is a python_version < "3.11" requirement: no column.
        "version": "2.3.1",
        "license": "MIT",
        "deps": ["pycodestyle"],
        "files": ["autopep8.py", "autopep8-*.dist-info/"],
        # What Blender's text editor operator does: fix_code() on a snippet.
        "check": (
            "import autopep8; "
            "out = autopep8.fix_code('import os, sys\\nx=1;y = 2\\nif x :\\n  print( y )\\n'); "
            "assert out == 'import os\\nimport sys\\nx = 1\\ny = 2\\nif x:\\n    print(y)\\n', "
            "repr(out)"
        ),
    },
    # jsonschema and its deps (attrs, pyrsistent) come from poetry.lock, which
    # pins 4.17.3 — the pre-referencing/rpds-py line. The lock shadows any seed
    # here ("lock wins"), so seeding them would be dead weight; they are pulled
    # into the emitted set by name instead.
    # ── document rendering: the contract-PDF toolchain ──────────────────────
    # cvc-engagement-docs/contract/generate_contract_pdfs.py renders the
    # invoices and the progress report to UT-styled PDFs through WeasyPrint,
    # and NEITHER weasyprint NOR markdown was packaged anywhere — so the client
    # PDFs could not be regenerated at all.  Every package here has a PyPI
    # sdist, which is why this set is independent of the git+submodule source
    # type the Sionna track is blocked on.
    #
    # Pinned to the CURRENT releases (weasyprint 69.0), which required bumping
    # the fonttools seed to 4.63.0 — see its entry above for why that is safe.
    #
    # WeasyPrint is NOT pure Python at runtime.  It dropped cairo in v53 but
    # kept Pango: weasyprint/text/ffi.py dlopens libgobject-2.0, libpango-1.0,
    # libpangoft2-1.0, libharfbuzz, libharfbuzz-subset and libfontconfig at
    # import time.  cvcpkg has recipes for all of them and each declares a
    # windows platform, but they are NATIVE recipes rather than -cpNNN columns,
    # so they go in the seed's `native_deps` (emitted as plain runtime + build
    # edges), not `deps`.  Until they were declared there, `cvcpkg install
    # weasyprint-cp312` produced a prefix whose `import weasyprint` failed
    # ("cannot load library 'libpango-1.0-0'") unless pango was installed by
    # hand; builders passed the import check only because their hosts had
    # system pango.  This toolchain still installs into its own `docs-deps`
    # prefix rather than into `dbg-deps`, which already has a freetype.dll
    # while VTK links its own.
    "webencodings": {
        # 0.6.1, not the contemporaneous 0.5.1: 0.5.1 is a 2017 legacy setup.py
        # sdist with no pyproject.toml, while 0.6.1 is the same tiny API
        # revived by CourtBouillon (who also maintain tinycss2/cssselect2)
        # behind a flit_core backend.  It satisfies every floor in this set.
        "version": "0.6.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["webencodings/", "webencodings-*.dist-info/"],
        "check": "import webencodings",
    },
    "tinycss2": {
        "version": "1.5.1",
        "license": "BSD-3-Clause",
        "deps": ["webencodings"],
        "files": ["tinycss2/", "tinycss2-*.dist-info/"],
        "check": "import tinycss2",
    },
    "cssselect2": {
        "version": "0.9.0",
        "license": "BSD-3-Clause",
        "deps": ["tinycss2", "webencodings"],
        "files": ["cssselect2/", "cssselect2-*.dist-info/"],
        "check": "import cssselect2",
    },
    "tinyhtml5": {
        "version": "2.1.0",
        "license": "MIT",
        "deps": ["webencodings"],
        "files": ["tinyhtml5/", "tinyhtml5-*.dist-info/"],
        "check": "import tinyhtml5",
    },
    "pydyf": {
        "version": "0.12.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["pydyf/", "pydyf-*.dist-info/"],
        "check": "import pydyf",
    },
    "pyphen": {
        # Tri-licensed, and PyPI's `license` field is empty while none of its
        # three classifiers are in _spdx's map — so without this override the
        # recipe would be emitted as NOASSERTION.
        "version": "0.18.1",
        "license": "GPL-2.0-or-later OR LGPL-2.0-or-later OR MPL-1.1",
        "deps": [],
        "files": ["pyphen/", "pyphen-*.dist-info/"],
        "check": "import pyphen",
    },
    "weasyprint": {
        "version": "69.0",
        "license": "BSD-3-Clause",
        # fonttools carries a [woff] extra (brotli + zopfli, for WOFF2 web
        # fonts).  cvcpkg columns have no extras concept, and the contract CSS
        # uses only Helvetica/Arial — so the extra is deliberately NOT
        # materialized here, the same call the gymnasium seed makes about its
        # optional env families.  A document that pulls a WOFF2 web font is
        # what would need it.
        "deps": [
            "cffi",
            "cssselect2",
            "fonttools",
            "pillow",
            "pydyf",
            "pyphen",
            "tinycss2",
            "tinyhtml5",
        ],
        # weasyprint/text/ffi.py dlopens gobject (glib), pango + pangoft2,
        # harfbuzz (+ subset) and fontconfig.  pango's own runtime closure
        # carries the rest, but every library the ffi layer opens by name is
        # listed so the edge does not hinge on pango's dependency list.
        "native_deps": ["pango", "harfbuzz", "fontconfig", "glib"],
        "files": ["weasyprint/", "weasyprint-*.dist-info/"],
        # A real render, not a bare import: `import weasyprint` succeeds even
        # when the PDF path is broken, and emitting bytes with a %PDF header is
        # the thing the contract PDFs actually depend on.
        "check": (
            "from weasyprint import HTML; "
            "assert HTML(string='<p>x</p>').write_pdf()[:4] == b'%PDF'"
        ),
    },
    "markdown": {
        "version": "3.10.3",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["markdown/", "markdown-*.dist-info/"],
        # Round-trip rather than import: the generator script's whole use of
        # markdown is markdown.markdown(), so exercise that.
        "check": "import markdown; assert markdown.markdown('*x*') == '<p><em>x</em></p>'",
    },
    # ── txwtf / atxcf-catx runtime closure ──────────────────────────────────
    # Everything the txwtf FastAPI server (plus its optional extras) and the
    # atxcf-catx bots import, so both apps can take every runtime dependency
    # from cvcpkg.  Versions are the apps' poetry.lock pins (the newer one where
    # the two locks disagree) unless a note says why not; the apps re-lock to
    # this set.  deps are the UNCONDITIONAL requires-dist (extras are not
    # materialized), plus python_version-gated ones that apply to some column.
    # The four hand-written native-library families these lean on (coincurve,
    # pycares, maxminddb, zlib-ng) are HANDWRITTEN_DEP_BASES below.
    #
    # The aio-libs stack builds its C/Cython speedups (see _PREFER_BINARY): their
    # in-tree PEP-517 backend (frozenlist, propcache, yarl) cythonizes at build
    # time and asks for Cython only via get_requires_for_build_wheel, which
    # --no-build-isolation never consults — hence the explicit build_deps.
    "pdm-backend": {
        # PEP-517 backend of asyncer and sqlmodel.  Self-hosting (empty
        # [build-system] requires, backend-path = src), like poetry-core.  The
        # wheel ships the pdm/backend/ subpackage of a `pdm` namespace.
        "version": "2.5.0",
        "license": "MIT",
        "deps": [],
        "files": ["pdm/backend/", "*.dist-info/"],
        "check": "import pdm.backend",
    },
    "expandvars": {
        # Build-only: the aio-libs in-tree backend's [build-system] requirement.
        "version": "1.1.2",
        "license": "MIT",
        "deps": [],
        "files": ["expandvars.py", "*.dist-info/"],
        "check": (
            "from expandvars import expandvars; "
            "assert expandvars('${CVCPKG_EXPANDVARS_PROBE_UNSET:-ok}') == 'ok'"
        ),
    },
    "frozenlist": {
        "version": "1.8.0",
        "license": "Apache-2.0",
        "deps": [],
        "build_deps": ["cython"],
        "files": ["frozenlist/", "*.dist-info/"],
        "check": (
            "import frozenlist; from frozenlist import _frozenlist; "
            "assert frozenlist.FrozenList is _frozenlist.FrozenList, 'C extension not used'"
        ),
    },
    "multidict": {
        "version": "6.7.1",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["multidict/", "*.dist-info/"],
        "check": (
            "import multidict; from multidict import _multidict; "
            "assert multidict.MultiDict is _multidict.MultiDict, 'C extension not used'"
        ),
    },
    "propcache": {
        "version": "0.5.2",
        "license": "Apache-2.0",
        "deps": [],
        "build_deps": ["cython"],
        "files": ["propcache/", "*.dist-info/"],
        "check": (
            "from propcache import _helpers, _helpers_c; "
            "assert _helpers.cached_property is _helpers_c.cached_property, 'C extension not used'"
        ),
    },
    "yarl": {
        "version": "1.24.2",
        "license": "Apache-2.0",
        "deps": ["idna", "multidict", "propcache"],
        "build_deps": ["cython"],
        "files": ["yarl/", "*.dist-info/"],
        "check": (
            "import yarl; from yarl import _quoting, _quoting_c; "
            "assert _quoting._Quoter is _quoting_c._Quoter, 'C extension not used'; "
            "assert str(yarl.URL('http://h/a b').with_query(q='x y')) == 'http://h/a%20b?q=x+y'"
        ),
    },
    "aiosignal": {
        "version": "1.4.0",
        "license": "Apache-2.0",
        "deps": ["frozenlist", "typing-extensions"],
        "files": ["aiosignal/", "*.dist-info/"],
        "check": "import aiosignal",
    },
    "aiohappyeyeballs": {
        "version": "2.7.1",
        "license": "PSF-2.0",
        "deps": [],
        "files": ["aiohappyeyeballs/", "*.dist-info/"],
        "check": "import aiohappyeyeballs; assert aiohappyeyeballs.start_connection",
    },
    "aiohttp": {
        # 3.14.1 (txwtf's lock).  3.14.2/3.14.3 are later patch releases with the
        # same requirements; 3.14.4+ need yarl >= 1.25.1.
        "version": "3.14.1",
        # aiohttp itself is Apache-2.0; the vendored llhttp parser it compiles in
        # is MIT (PyPI metadata: "Apache-2.0 AND MIT").
        "license": "Apache-2.0 AND MIT",
        "deps": [
            "aiohappyeyeballs",
            "aiosignal",
            "attrs",
            "frozenlist",
            "multidict",
            "propcache",
            "typing-extensions",
            "yarl",
        ],
        "files": ["aiohttp/", "*.dist-info/"],
        "check": (
            "import aiohttp; from aiohttp import http_parser, _http_parser; "
            "assert http_parser.HttpRequestParser is http_parser.HttpRequestParserC, "
            "'C HTTP parser not built'"
        ),
    },
    "aiohttp-fast-zlib": {
        # Its zlib_ng extra is materialized as a hard dep: without zlib-ng (or
        # isal) enable() has nothing faster to swap in, and zlib-ng is the
        # reason the apps install this.
        "version": "0.3.0",
        "license": "Apache-2.0",
        "deps": ["aiohttp", "zlib-ng"],
        "files": ["aiohttp_fast_zlib/", "*.dist-info/"],
        "check": (
            "import aiohttp_fast_zlib, zlib_ng; "
            "aiohttp_fast_zlib.enable(); aiohttp_fast_zlib.disable()"
        ),
    },
    "aioitertools": {
        "version": "0.13.0",
        "license": "MIT",
        "deps": [],
        "files": ["aioitertools/", "*.dist-info/"],
        "check": "import aioitertools",
    },
    "aiobotocore": {
        # NOT the apps' 2.25.1 lock: aiobotocore pins botocore to a narrow window
        # and cvcpkg ships botocore/boto3 1.43.49.  3.9.0 is the only release
        # whose window (>=1.43.3,<1.43.57) contains 1.43.49 (3.8.0 stops at
        # <1.43.47, 3.9.1 starts at 1.43.66).  aioboto3 is NOT packaged: every
        # aioboto3 release (latest 15.5.0) pins aiobotocore 2.x, i.e. botocore
        # 1.40.x.
        "version": "3.9.0",
        "license": "Apache-2.0",
        "deps": [
            "aiohttp",
            "aioitertools",
            "botocore",
            "jmespath",
            "multidict",
            "python-dateutil",
            "wrapt",
        ],
        "files": ["aiobotocore/", "*.dist-info/"],
        "check": (
            "import aiobotocore.session; "
            "assert 's3' in aiobotocore.session.get_session().get_available_services()"
        ),
    },
    "aiodns": {
        "version": "4.0.4",
        "license": "MIT",
        "deps": ["pycares"],
        "files": ["aiodns/", "*.dist-info/"],
        "check": (
            "import asyncio, aiodns; loop = asyncio.new_event_loop(); "
            "r = aiodns.DNSResolver(loop=loop, nameservers=['127.0.0.1']); loop.close()"
        ),
    },
    "aiofiles": {
        "version": "25.1.0",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["aiofiles/", "*.dist-info/"],
        "check": "import aiofiles, aiofiles.os",
    },
    "aioimaplib": {
        # GPL-3.0-or-later (source headers: "either version 3 ... or any later").
        "version": "2.0.1",
        "license": "GPL-3.0-or-later",
        "deps": [],
        "files": ["aioimaplib/", "*.dist-info/"],
        "check": "import aioimaplib",
    },
    "aiokafka": {
        # Not locked by either app (txwtf `kafka` extra): latest release.
        "version": "0.14.0",
        "license": "Apache-2.0",
        "deps": ["async-timeout", "packaging", "typing-extensions"],
        "files": ["aiokafka/", "*.dist-info/"],
        "check": "import aiokafka; from aiokafka.record import _crecords",
    },
    "aiosmtplib": {
        "version": "5.1.2",
        "license": "MIT",
        "deps": [],
        "files": ["aiosmtplib/", "*.dist-info/"],
        "check": "import aiosmtplib",
    },
    "asyncer": {
        "version": "0.0.18",
        "license": "MIT",
        "deps": ["anyio", "sniffio", "typing-extensions"],
        "files": ["asyncer/", "*.dist-info/"],
        "check": "import asyncer",
    },
    "asyncssh": {
        # Not locked by either app (txwtf `sftp` extra).  2.23.1, not the newest
        # 2.24.x: 2.24.0 raised the floor to cryptography>=48.0.1 and cvcpkg
        # ships cryptography 48.0.0.
        "version": "2.23.1",
        "license": "EPL-2.0 OR GPL-2.0-or-later",
        "deps": ["cryptography", "typing-extensions"],
        "files": ["asyncssh/", "*.dist-info/"],
        "check": "import asyncssh; asyncssh.generate_private_key('ssh-ed25519')",
    },
    "soupsieve": {
        "version": "2.8.4",
        "license": "MIT",
        "deps": [],
        "files": ["soupsieve/", "*.dist-info/"],
        # soupsieve imports bs4 at module scope (soupsieve/__init__.py and
        # css_match.py) while beautifulsoup4 depends on soupsieve, so it cannot be
        # imported on its own; locate it without executing it.  beautifulsoup4's
        # check drives soupsieve for real (select_one).
        "check": (
            "import importlib.metadata as m, importlib.util as u; "
            "assert u.find_spec('soupsieve') and m.version('soupsieve')"
        ),
    },
    "beautifulsoup4": {
        "version": "4.15.0",
        "license": "MIT",
        "deps": ["soupsieve", "typing-extensions"],
        "files": ["bs4/", "*.dist-info/"],
        "check": (
            "from bs4 import BeautifulSoup; "
            "assert BeautifulSoup('<p>x</p>', 'html.parser').p.text == 'x'; "
            "assert BeautifulSoup('<p class=a>y</p>', 'html.parser').select_one('p.a').text == 'y'"
        ),
    },
    "ccxt": {
        # 4.5.64, NOT the apps' 4.5.66 lock: from 4.5.65 on, ccxt exact-pins its
        # whole closure (cryptography==49.0.0, certifi==2026.6.17,
        # setuptools==82.0.1, typing_extensions==4.16.0, idna==3.18, ...), which no
        # cvcpkg set can satisfy, so the apps could not re-lock.  4.5.64 is the
        # last release with >= floors.  coincurve's marker is python_version <=
        # 3.13, which every column satisfies.
        "version": "4.5.64",
        "license": "MIT",
        "deps": [
            "aiodns",
            "aiohttp",
            "certifi",
            "coincurve",
            "cryptography",
            "requests",
            "setuptools",
            "typing-extensions",
            "yarl",
        ],
        "files": ["ccxt/", "*.dist-info/"],
        "check": "import ccxt, ccxt.async_support; assert 'binance' in ccxt.exchanges",
    },
    "decorator": {
        "version": "5.3.1",
        "license": "BSD-2-Clause",
        "deps": [],
        "files": ["decorator/", "*.dist-info/"],
        "check": "import decorator",
    },
    "wrapt": {
        # 2.2.1 (atxcf-catx's lock; txwtf locks 1.17.3).  deprecated (<3) and
        # aiobotocore 3.x (<3.0.0) both accept 2.x.
        "version": "2.2.1",
        "license": "BSD-2-Clause",
        "deps": [],
        "files": ["wrapt/", "*.dist-info/"],
        "check": (
            "import wrapt; from wrapt import __wrapt__, _wrappers; "
            "assert __wrapt__._using_c_extension, 'C extension not used'; "
            "assert wrapt.FunctionWrapper is _wrappers.FunctionWrapper"
        ),
    },
    "deprecated": {
        "version": "1.3.1",
        "license": "MIT",
        "deps": ["wrapt"],
        "files": ["deprecated/", "*.dist-info/"],
        "check": "from deprecated import deprecated",
    },
    "dnspython": {
        "version": "2.8.0",
        "license": "ISC",
        "deps": [],
        "files": ["dns/", "*.dist-info/"],
        "check": "import dns.message, dns.name; dns.message.make_query('example.com', 'A')",
    },
    "email-validator": {
        "version": "2.3.0",
        "license": "Unlicense",
        "deps": ["dnspython", "idna"],
        "files": ["email_validator/", "*.dist-info/"],
        "check": (
            "from email_validator import validate_email; "
            "assert validate_email('a@example.com', check_deliverability=False).normalized"
        ),
    },
    "fabric": {
        "version": "3.2.2",
        "license": "BSD-2-Clause",
        "deps": ["decorator", "deprecated", "invoke", "paramiko"],
        "files": ["fabric/", "*.dist-info/"],
        "check": "import fabric; assert fabric.Connection",
    },
    "tzdata": {
        "version": "2026.3",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["tzdata/", "*.dist-info/"],
        "check": (
            "import importlib.resources as r; "
            "assert r.files('tzdata').joinpath('zoneinfo', 'UTC').is_file()"
        ),
    },
    "faker": {
        # tzdata is a Windows-only requirement upstream; columns carry no
        # platform markers, so it is a plain edge (a pure data package).
        "version": "40.31.0",
        "license": "MIT",
        "deps": ["tzdata"],
        "files": ["faker/", "*.dist-info/"],
        "check": "from faker import Faker; assert Faker().name()",
    },
    "flask-cors": {
        "version": "6.0.2",
        "license": "MIT",
        "deps": ["flask", "werkzeug"],
        "files": ["flask_cors/", "*.dist-info/"],
        "check": "import flask, flask_cors; flask_cors.CORS(flask.Flask('x'))",
    },
    "geoip2": {
        # Stays a PREBUILT py3-none-any wheel for now: 5.2.0's PEP-517 backend is
        # uv_build, a Rust binary with no cvcpkg recipe (source_mode_for reason 3).
        "version": "5.2.0",
        "license": "Apache-2.0",
        "deps": ["aiohttp", "maxminddb", "requests"],
        "files": ["geoip2/", "*.dist-info/"],
        "check": "import geoip2.database, geoip2.webservice",
    },
    "httptools": {
        # Self-contained C extension: compiles the llhttp + http-parser sources
        # vendored in the sdist (no cvcpkg llhttp recipe exists), MIT.
        "version": "0.8.0",
        "license": "MIT",
        "deps": [],
        "files": ["httptools/", "*.dist-info/"],
        "check": (
            "import httptools; from httptools.parser import parser; "
            "assert httptools.HttpRequestParser is parser.HttpRequestParser; "
            "assert httptools.parse_url(b'http://h:8/p?q').port == 8"
        ),
    },
    "psutil": {
        "version": "7.2.2",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["psutil/", "*.dist-info/"],
        "check": (
            "import psutil; assert psutil.cpu_count() >= 1; "
            "assert psutil.Process().memory_info().rss > 0"
        ),
    },
    "pydle": {
        "version": "1.1.0",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["pydle/", "*.dist-info/"],
        "check": "import pydle; assert pydle.Client",
    },
    "python-decouple": {
        "version": "3.8",
        "license": "MIT",
        "deps": [],
        "files": ["decouple.py", "*.dist-info/"],
        "check": "from decouple import config, Csv",
    },
    "redis": {
        # redis-py (MIT) — the client library, not the server.
        "version": "8.0.1",
        "license": "MIT",
        "deps": [],
        "files": ["redis/", "*.dist-info/"],
        "check": "import redis, redis.asyncio; redis.Redis(host='127.0.0.1', port=1)",
    },
    "requests-file": {
        "version": "3.0.1",
        "license": "Apache-2.0",
        "deps": ["requests"],
        "files": ["requests_file/", "*.dist-info/"],
        "check": "from requests_file import FileAdapter",
    },
    "restrictedpython": {
        "version": "8.4",
        "license": "ZPL-2.1",
        "deps": [],
        "files": ["RestrictedPython/", "*.dist-info/"],
        "check": (
            "from RestrictedPython import compile_restricted; "
            "compile_restricted('x = 1', '<s>', 'exec')"
        ),
    },
    "sexpdata": {
        "version": "1.0.2",
        "license": "BSD-2-Clause",
        "deps": [],
        "files": ["sexpdata.py", "*.dist-info/"],
        "check": "import sexpdata; assert sexpdata.loads('(a 1)')",
    },
    "simplejson": {
        "version": "4.1.1",
        "license": "MIT OR AFL-2.1",
        "deps": [],
        "files": ["simplejson/", "*.dist-info/"],
        "check": (
            "import simplejson; from simplejson import _speedups; "
            "assert simplejson.loads('[1]') == [1]"
        ),
    },
    "slack-sdk": {
        "version": "3.42.0",
        "license": "MIT",
        "deps": [],
        "files": ["slack_sdk/", "slack/", "*.dist-info/"],
        # Not slack_sdk.web.async_client: that imports aiohttp, an optional extra.
        "check": "import slack_sdk, slack_sdk.web; slack_sdk.WebClient(token='xoxb-cvcpkg')",
    },
    "sniffio": {
        "version": "1.3.1",
        "license": "MIT OR Apache-2.0",
        "deps": [],
        "files": ["sniffio/", "*.dist-info/"],
        "check": "import sniffio",
    },
    "sqlmodel": {
        "version": "0.0.39",
        "license": "MIT",
        "deps": ["pydantic", "sqlalchemy", "typing-extensions"],
        "files": ["sqlmodel/", "*.dist-info/"],
        "check": "from sqlmodel import SQLModel, create_engine; create_engine('sqlite://')",
    },
    "wcwidth": {
        "version": "0.8.2",
        "license": "MIT",
        "deps": [],
        "files": ["wcwidth/", "*.dist-info/"],
        "check": "import wcwidth; assert wcwidth.wcswidth('ab') == 2",
    },
    "urwid": {
        # LGPL-2.1-only.  3.0.5 (lock); 4.x is a major bump.
        "version": "3.0.5",
        "license": "LGPL-2.1-only",
        "deps": ["wcwidth"],
        "files": ["urwid/", "*.dist-info/"],
        "check": "import urwid; urwid.Text('x')",
    },
    # ── sionna-rt's runtime closure ─────────────────────────────────────────
    # sionna-rt 2.2.0 (hand-written: no PyPI sdist, built from its GitHub tag)
    # requires importlib_resources, ipywidgets and pythreejs.  The last two
    # drive only its notebook previewer, but sionna/rt/__init__.py imports
    # sionna.rt.preview eagerly, which imports ipywidgets, pythreejs and
    # IPython.display at module load: without this stack `import sionna.rt`
    # fails.  Deps are unconditional lists (seeds take no markers), so
    # ipython's win32-only colorama and non-win32 pexpect ride along
    # everywhere; both are pure Python and harmless where unused.
    "coherent-licensed": {
        # importlib_resources' (jaraco skeleton) setuptools plugin: a
        # [build-system] requirement only, packaged so it builds from source.
        "version": "0.5.2",
        "license": "MIT",
        "deps": [],
        "files": ["coherent/licensed/", "*.dist-info/"],
        "check": "import coherent.licensed",
    },
    "importlib-resources": {
        "version": "7.1.0",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["importlib_resources/", "*.dist-info/"],
        "check": "from importlib_resources import files; assert files('importlib_resources')",
    },
    "traitlets": {
        "version": "5.16.1",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["traitlets/", "*.dist-info/"],
        "check": (
            "import traitlets; "
            "A = type('A', (traitlets.HasTraits,), {'x': traitlets.Int(1)}); "
            "assert A().x == 1"
        ),
    },
    "comm": {
        "version": "0.2.3",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["comm/", "*.dist-info/"],
        "check": "import comm; comm.create_comm(target_name='cvcpkg')",
    },
    "traittypes": {
        "version": "0.2.3",
        "license": "BSD-3-Clause",
        "deps": ["traitlets"],
        "files": ["traittypes/", "*.dist-info/"],
        "check": "import traittypes",
    },
    "matplotlib-inline": {
        "version": "0.2.2",
        "license": "BSD-3-Clause",
        "deps": ["traitlets"],
        "files": ["matplotlib_inline/", "*.dist-info/"],
        # Importing it imports matplotlib (backend_inline), which it does not
        # require: IPython loads it only for %matplotlib inline.  Check the
        # installed package without importing it.
        "check": (
            "import importlib.util, importlib.metadata as md; "
            "s = importlib.util.find_spec('matplotlib_inline'); "
            "assert s is not None and s.submodule_search_locations, s; "
            "assert md.version('matplotlib-inline') == '0.2.2'"
        ),
    },
    "parso": {
        "version": "0.8.7",
        "license": "MIT",
        "deps": [],
        "files": ["parso/", "*.dist-info/"],
        "check": "import parso; assert parso.parse('x = 1').children",
    },
    "jedi": {
        "version": "0.20.1",
        "license": "MIT",
        "deps": ["parso"],
        "files": ["jedi/", "*.dist-info/"],
        "check": "import jedi; assert jedi.Script('import os\\nos.pa').complete()",
    },
    "ptyprocess": {
        "version": "0.7.0",
        "license": "ISC",
        "deps": [],
        "files": ["ptyprocess/", "*.dist-info/"],
        "check": "import ptyprocess",
    },
    "pexpect": {
        "version": "4.9.0",
        "license": "ISC",
        "deps": ["ptyprocess"],
        "files": ["pexpect/", "*.dist-info/"],
        "check": "import pexpect",
    },
    "prompt-toolkit": {
        "version": "3.0.53",
        "license": "BSD-3-Clause",
        "deps": ["wcwidth"],
        "files": ["prompt_toolkit/", "*.dist-info/"],
        "check": "import prompt_toolkit",
    },
    "executing": {
        "version": "2.3.0",
        "license": "MIT",
        "deps": [],
        "files": ["executing/", "*.dist-info/"],
        "check": "import executing",
    },
    "asttokens": {
        "version": "3.0.2",
        "license": "Apache-2.0",
        "deps": [],
        "files": ["asttokens/", "*.dist-info/"],
        "check": "import asttokens; asttokens.ASTTokens('x = 1', parse=True)",
    },
    "pure-eval": {
        "version": "0.2.4",
        "license": "MIT",
        "deps": [],
        "files": ["pure_eval/", "*.dist-info/"],
        "check": "import pure_eval",
    },
    "stack-data": {
        "version": "0.6.3",
        "license": "MIT",
        "deps": ["asttokens", "executing", "pure-eval"],
        "files": ["stack_data/", "*.dist-info/"],
        "check": "import stack_data",
    },
    "ipython-pygments-lexers": {
        "version": "1.1.1",
        "license": "BSD-3-Clause",
        "deps": ["pygments"],
        "files": ["ipython_pygments_lexers.py", "*.dist-info/"],
        "check": "import ipython_pygments_lexers",
    },
    "ipython": {
        "version": "9.17.1",
        "license": "BSD-3-Clause",
        "deps": [
            "colorama",
            "ipython-pygments-lexers",
            "jedi",
            "matplotlib-inline",
            "pexpect",
            "prompt-toolkit",
            "psutil",
            "pygments",
            "stack-data",
            "traitlets",
            "typing-extensions",
        ],
        "files": ["IPython/", "*.dist-info/"],
        "check": "import IPython; from IPython.display import display",
    },
    "widgetsnbextension": {
        "version": "4.0.16",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["widgetsnbextension/", "*.dist-info/"],
        "check": "import widgetsnbextension",
    },
    "jupyterlab-widgets": {
        "version": "3.0.17",
        "license": "BSD-3-Clause",
        "deps": [],
        "files": ["jupyterlab_widgets/", "*.dist-info/"],
        "check": "import jupyterlab_widgets",
    },
    "ipywidgets": {
        "version": "8.1.9",
        "license": "BSD-3-Clause",
        "deps": ["comm", "ipython", "jupyterlab-widgets", "traitlets", "widgetsnbextension"],
        "files": ["ipywidgets/", "*.dist-info/"],
        # A widget outside a kernel gets comm's dummy comm; sionna's viewer
        # also needs ipywidgets.embed.
        "check": (
            "import ipywidgets; from ipywidgets.embed import embed_snippet; "
            "assert ipywidgets.IntSlider(value=3).value == 3"
        ),
    },
    "ipydatawidgets": {
        "version": "4.3.5",
        "license": "BSD-3-Clause",
        "deps": ["ipywidgets", "numpy", "traittypes"],
        "files": ["ipydatawidgets/", "*.dist-info/"],
        "check": "import ipydatawidgets",
    },
    "pythreejs": {
        "version": "2.4.2",
        "license": "BSD-3-Clause",
        "deps": ["ipydatawidgets", "ipywidgets", "numpy", "traitlets"],
        "files": ["pythreejs/", "*.dist-info/"],
        "check": "import pythreejs; pythreejs.BoxGeometry(width=1, height=1, depth=1)",
    },
}

# ── Hand-written column families the generated matrix may depend on ──────────
# numpy's recipes are from-source conversions carrying native-library edges
# (openblas), rpath passes and per-platform gates (see is_generator_owned): the
# generator must never emit or prune them, but seeds DO depend on them —
# gymnasium, trimesh, pythran, contourpy, matplotlib, imageio all import numpy.
# Before this table existed those edges were dropped on the floor by
# deps_for_column's universe test, so trimesh-cp311 shipped with NO numpy edge
# and imported only when a sibling build happened to leave numpy in the shared
# prefix.  An unresolvable dependency must be loud, never silent.
#
# Only the BASE names are declared here; which columns exist is read from disk
# (recipes/<base>-cpNNN/recipe.yaml), so the truth stays with the recipes and a
# hand-written family that gains or loses a column is picked up by the next
# run.  A base listed here with no columns on disk aborts the run.
#
# coincurve, pycares, maxminddb and zlib-ng link cvcpkg native libraries
# (secp256k1, c-ares, libmaxminddb, zlib-ng) with an $ORIGIN rpath pass, so they
# are hand-written too; ccxt, aiodns, geoip2 and aiohttp-fast-zlib depend on them.
#
# apsw, backports-zstd and lz4 link cvcpkg's sqlite, zstd and lz4 the same way
# (rosbags depends on them); scipy links OpenBLAS and compiles Fortran with
# cvcpkg's gfortran (jax depends on it); jaxlib installs Google's prebuilt
# wheels (PyPI ships no sdist), hand-written like torch, and jax, optax and
# equinox build on top of it.
HANDWRITTEN_DEP_BASES = (
    "numpy",
    "coincurve",
    "pycares",
    "maxminddb",
    "zlib-ng",
    "apsw",
    "backports-zstd",
    "lz4",
    "scipy",
    "jaxlib",
)


def handwritten_columns(out: Path, interps: list[str]) -> dict[str, list[str]]:
    """base -> interpreter columns present on disk, for HANDWRITTEN_DEP_BASES."""
    return {
        base: [i for i in interps if (out / f"{base}-cp{i}" / "recipe.yaml").is_file()]
        for base in HANDWRITTEN_DEP_BASES
    }


def handwritten_families(
    out: Path, interps: list[str], generated: set[str], referenced: set[str]
) -> tuple[dict[str, list[str]], str | None, list[str]]:
    """The hand-written dep families for this run: (columns, fatal message, notes).

    Columns are facts read from disk.  A family is fatal only when something
    depends on it and it has NO recipes under *out* at all -- every edge on it
    would silently vanish.  A family that exists on disk but has no column for
    this run's interpreters (``--interpreters 313t`` against coincurve, which
    has cp311/312/313 only) stays in the map with no columns, so its
    dependents' columns for those interpreters are pruned ("dep(s) lack the
    column") exactly as for a generated package; dropping it instead would
    lose the edge.  An empty family nothing references (a synthetic --out, as
    the unit-test harness uses) is ignored.
    """
    extra = handwritten_columns(out, interps)
    on_disk = handwritten_columns(out, INTERPRETERS)
    notes: list[str] = []
    for hw_base, hw_cols in sorted(extra.items()):
        if hw_base in generated:
            return (
                extra,
                f"FATAL: {hw_base} is both a hand-written dep base and a "
                f"generated package (lock/seed) — pick one owner.",
                notes,
            )
        if hw_cols:
            notes.append(f"hand-written dep columns: {hw_base} -> {', '.join(hw_cols)} (from disk)")
        elif on_disk.get(hw_base):
            notes.append(
                f"  note: hand-written dep base '{hw_base}' has no "
                f"{'/'.join('cp' + i for i in interps)} column (on disk: "
                f"{', '.join(on_disk[hw_base])}); dependents' columns there are pruned"
            )
        elif hw_base in referenced:
            return (
                extra,
                f"FATAL: hand-written dep base '{hw_base}' has no "
                f"{hw_base}-cpNNN recipes under {out}/ but packages depend "
                f"on it — their edges cannot resolve.",
                notes,
            )
        else:
            del extra[hw_base]
            notes.append(
                f"  note: hand-written dep base '{hw_base}' has no columns "
                f"under {out}/ and nothing references it; ignored"
            )
    return extra, None, notes


def norm(name: str) -> str:
    """PyPI-normalize a distribution name -> cvcpkg recipe base name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def semver(version: str) -> str:
    """The orderable numeric prefix of a PEP440 version for upstream_version.
    e.g. '2.9.0.post0' -> '2.9.0', '1.0.0rc1' -> '1.0.0'. The pinned wheel URL
    (the exact locked build) is unaffected — this is just the display version.

    A FOURTH numeric component ('2026.6.1.19' — the YYYY.M.D.HH calver
    trove-classifiers moved to permanently after 2024.10.16, its last
    three-component release) is not SemVer: cvcpkg.semver.Version parses at
    most major.minor.patch, so validation.py rejects the minted version AND
    version_sort_key ranks it below every parseable sibling — the new recipe
    could never be selected over the very version it replaces.  Carry the
    extra components in the pre-release field instead ('2026.6.1-19').  That
    parses, and because SemVer compares pre-release identifiers numerically it
    preserves upstream's own ordering exactly:
    '2026.5.20-13' < '2026.5.20-19' < '2026.5.22-10' < '2026.6.1-19'.
    Truncating to three components would not — upstream ships more than one
    release on the same day (2026.5.20.13 and 2026.5.20.19), so two distinct
    releases would collapse to one indistinguishable upstream_version."""
    parts = re.match(r"\d+(?:\.\d+)*", version).group(0).split(".")
    tail = ".".join(parts[3:])
    return ".".join(parts[:3]) + (f"-{tail}" if tail else "")


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


# PEP 508 allows either quote style; [build-system] requires written by hand
# often use single quotes (frozenlist/propcache/yarl: "tomli; python_version <
# '3.11'"), which a double-quote-only pattern silently kept as an edge.
_MARKER_CMP = re.compile(r"""python_version\s*(<=|>=|<|>|==|!=)\s*["'](\d+(?:\.\d+)*)["']""")


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
         those packages have no source to build.  _UNBUILDABLE_SDIST (a
         published sdist that cannot build as shipped) is treated the same.
      2. --source-mode pins the rest (an operator override, reported as such).
      3. No sdist on PyPI -> nothing to build from.
      4. Pure (noarch) packages follow --pure-policy; the DEFAULT is sdist,
         because a py3-none-any wheel is still a third-party binary artifact.
      5. A missing build-backend recipe blocks the source build.
      6. Otherwise: build from source."""
    reason = prebuilt_only_reason(base) or _UNBUILDABLE_SDIST.get(base)
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


# Packages that publish a py3-none-any FALLBACK wheel but whose from-source
# sdist build compiles C extensions (mypyc / C speedups / protobuf upb).  The
# default sdist path (`--pure-policy sdist`) would emit a build-once artifact
# stamped platform=any/noarch that in fact carries a platform-specific `.so`
# — broken on every host it did not build on (cy-pca/cvcpkg #53: a
# cython-cp311 "noarch" bundle shipped cpython-311-darwin.so).  Opt these out
# of the none-any→pure shortcut so each column is classified compiled and
# published per-platform.  This is the deliberate per-package decision the
# note below describes.
#
# The aio-libs C/Cython speedups (frozenlist, multidict, propcache, yarl,
# aiohttp), wrapt and simplejson are the same shape: a py3-none-any fallback
# wheel on PyPI, but an sdist build that compiles the extension by default.
# They are compiled here (per platform) rather than forced pure: aiohttp's
# request path runs through all of them, and each seed's check asserts the
# compiled module is the one in use.
_PREFER_BINARY = {
    "aiohttp",
    "cython",
    "frozenlist",
    "multidict",
    "propcache",
    "protobuf",
    "simplejson",
    "sqlalchemy",
    "wrapt",
    "yarl",
}

# The other way out of the same trap, for an extension that is OPTIONAL and
# that the sdist's own build can be told to skip: keep the build-once noarch
# column, and export the switch into every matrix entry so the build actually
# produces the py3-none-any wheel.  Without it the `platform: any` build compiles
# the extension on whichever builder claims the job and ships that host's `.so`
# as noarch (#53 again).  Prefer this over _PREFER_BINARY when the extension is
# only a speedup: a pure column also covers the platforms SDIST_PLATFORMS omits.
_PURE_BUILD_ENV: dict[str, dict[str, str]] = {
    # setup.py: BUILD_EXTENSION=no drops websockets.speedups (frame masking in C;
    # the pure-Python fallback is the same API).
    "websockets": {"BUILD_EXTENSION": "no"},
    # setup.py: PYRSISTENT_SKIP_EXTENSION (any value) drops pvectorc, the C
    # PVector; pyrsistent falls back to its Python one.  Without it the cp312
    # column went out as any/noarch holding pvectorc.cpython-312-darwin.so.
    "pyrsistent": {"PYRSISTENT_SKIP_EXTENSION": "1"},
    # setup.py: FONTTOOLS_WITH_CYTHON unset means "compile the Cython speedups
    # if Cython happens to be importable", so the noarch column depended on the
    # claiming builder's prefix: dev's 4.63.0+cvc.3 `any` bundle carries six
    # cpython-312-darwin.so modules.  "0" pins the pure-Python modules.
    "fonttools": {"FONTTOOLS_WITH_CYTHON": "0"},
}


def classify(wheels: list[dict], interps: list[str], name: str | None = None) -> str:
    """Classify a package by its wheel set:

    * ``pure`` — a ``*-none-any.whl`` (py3-none-any, …) exists: platform- and
      interpreter-independent.  Every column installs the same wheel.
    * ``abi3`` — no pure wheel, but the extension ships stable-ABI (``abi3``)
      wheels and no per-version ``cpNN-cpNN`` build for our non-free-threaded
      targets: one binary serves every non-free-threaded column.
    * ``cext`` — a distinct ``cpNN-cpNN[t]`` binary per interpreter.

    Classification is per-package bookkeeping; wheel selection is per column
    (see wheel_for_column), so an abi3 package with an extra exact cp313t
    wheel still gets its free-threaded column.  ``name`` opts a package into
    ``_PREFER_BINARY`` (skip the none-any shortcut).
    """
    # A none-any wheel wins even when binary wheels also exist (black,
    # charset-normalizer, pytokens, tomli): the pure fallback trades speed
    # (no mypyc / C speedups / upb) for a build-once noarch artifact and
    # full-column coverage incl. cp313t.  _PREFER_BINARY (above) opts a
    # package out of that shortcut when its sdist build would compile.
    if name not in _PREFER_BINARY and any(w["filename"].endswith("none-any.whl") for w in wheels):
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


def compute_columns(
    meta: dict[str, dict],
    interps: list[str],
    extra_cols: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Viable columns per base: a column needs a wheel AND every python dep
    viable in that column, to a fixpoint.  Pruning is reported to stderr so a
    silently missing column is visible in the regeneration log.

    *extra_cols* are hand-written column families (HANDWRITTEN_DEP_BASES) that
    participate as resolvable dependencies: their columns are fixed facts from
    disk, so they seed the map, join the universe, and prune dependents that
    claim a column they lack — but the fixpoint never shrinks them and the
    emit/prune passes never touch them (they are not in *meta*)."""
    extra_cols = extra_cols or {}
    universe = set(meta) | set(extra_cols)
    cols: dict[str, list[str]] = {b: list(c) for b, c in extra_cols.items()}
    why: dict[tuple[str, str], str] = {}
    for base, m in meta.items():
        cols[base] = []
        # A seed may CAP its columns below what upstream's wheels would allow
        # (pillow: the hand conversion covers 311/312/313 only) — the cap
        # prunes dependents through the fixpoint below, keeping the matrix
        # honest instead of emitting columns over an unconverted recipe.
        allowed = m.get("interpreters")
        for i in interps:
            if allowed is not None and i not in allowed:
                why[(base, i)] = "column capped by the seed's `interpreters`"
            elif wheel_for_column(m, i):
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
        m["platforms"] = _SDIST_PLATFORMS_OVERRIDE.get(base, platforms)
        probe = cols[base][0] if cols[base] else interps[0]
        reqs: list[str] = []
        missing: list[str] = []
        # Only pay for the sdist download when the answer can still be "sdist".
        if (
            m["sdist"]
            and not prebuilt_only_reason(base)
            and base not in _UNBUILDABLE_SDIST
            and forced != "wheel"
        ):
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
            "kind": classify(wheels, interps, base),
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
            "kind": classify(wheels, interps, base),
            "license": seed.get("license", lic),
            "files": seed.get("files"),
            "check": seed.get("check"),
            "interpreters": seed.get("interpreters"),
            # Build-only edges beyond the sdist's [build-system] requires, for
            # backends that consult a package opportunistically (see pytest).
            "build_deps": seed.get("build_deps", []),
            # Native cvcpkg recipes the package loads at run time (weasyprint).
            "native_deps": seed.get("native_deps", []),
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

    # Hand-written dep families: columns are facts read from disk.  An empty
    # family that something in the universe DEPENDS on is fatal — every dep
    # edge on it would silently vanish, which is the exact failure this table
    # removes.  An empty family nothing references (a run against a synthetic
    # --out, as the unit-test harness does) is merely irrelevant.
    referenced: set[str] = set()
    for m in meta.values():
        d = m["deps"]
        referenced.update(norm(x) for x in (d if isinstance(d, (list, tuple)) else d.keys()))
    extra, fatal, notes = handwritten_families(out, interps, set(meta), referenced)
    for note in notes:
        print(note, file=sys.stderr)
    if fatal:
        print(fatal, file=sys.stderr)
        return 1

    cols = compute_columns(meta, interps, extra)

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
            _emit_column(out, base, m, interp, meta, cols, extra_universe=set(extra))
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
        if base in meta or base in HANDWRITTEN_DEP_BASES:
            # Hand-written dep families are outside meta by design; the marker
            # test below would already spare them, but their exclusion is a
            # property of the design, not a lucky property of their prose.
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


def _disk_platforms(recipe_dir: Path) -> set[str]:
    """The ``build.matrix`` platforms of an on-disk recipe (empty if absent)."""
    y = recipe_dir / "recipe.yaml"
    if not y.is_file():
        return set()
    text = y.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*-\s*platform:\s*([\w-]+)\s*$", text, re.MULTILINE))


def _windows_gap(
    out: Path,
    base: str,
    interp: str,
    meta: dict,
    extra_universe=frozenset(),
    _seen: set[str] | None = None,
) -> list[str]:
    """Hand-written columns in *base*'s runtime closure (for *interp*) that have
    no Windows build — read from disk, like their column set.  Generated deps are
    walked through (they claim Windows themselves), so a gap two levels down
    (ccxt -> aiodns -> pycares) still surfaces."""
    seen = set() if _seen is None else _seen
    m = meta.get(base)
    if not m or "deps" not in m:
        return []
    gap: list[str] = []
    for dep in deps_for_column(m, interp, set(meta) | set(extra_universe)):
        if dep in seen:
            continue
        seen.add(dep)
        if dep in extra_universe:
            if not _disk_platforms(out / f"{dep}-cp{interp}") & {"windows", "any"}:
                gap.append(f"{dep}-cp{interp}")
        else:
            gap += _windows_gap(out, dep, interp, meta, extra_universe, seen)
    return gap


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


def _emit_column(out, base, m, interp, meta, cols, extra_universe=frozenset()):
    """Emit one recipe: <base>-cp<interp>.

    ``m["mode"]`` selects the shape: ``sdist`` (from-source tarball, the
    default) or ``wheel`` (prebuilt PyPI wheel, the documented fallback).
    ``m["reason"]`` is recorded in the recipe so the choice is auditable from
    the recipe alone, not just from a regeneration log.

    *extra_universe* extends dependency resolution to hand-written column
    families (HANDWRITTEN_DEP_BASES): a seed dep on ``numpy`` renders as a
    ``numpy-cp<interp>`` edge instead of being silently dropped."""
    name = f"{base}-cp{interp}"
    d = out / name
    mode = m.get("mode", "wheel")
    arts = wheel_for_column(m, interp)
    abi = column_abi(m, interp, arts, mode)
    ver = col_version(interp)
    kind = m["kind"]
    dep_bases = deps_for_column(m, interp, set(meta) | set(extra_universe))
    # Runtime deps are ALSO build deps: the post-install `import <pkg>` check
    # imports the package for real, and many packages eagerly import a runtime
    # dep at module load (sqlalchemy -> typing_extensions), so it must be staged
    # in the build prefix or the check fails.
    deps = [f"python{interp}", *(f"{b}-cp{interp}" for b in dep_bases)]
    # Native (non-Python) libraries the package loads at run time, named as
    # their own cvcpkg recipes rather than -cpNNN columns (see the seed's
    # "native_deps"). Runtime edges so a consumer prefix carries them, and build
    # edges like every runtime dep so the import check can load them.
    deps += [n for n in m.get("native_deps") or [] if n not in deps]
    # From-source columns additionally need the PEP-517 backend importable at
    # build time (--no-build-isolation); those are build-only edges.
    backends = backend_edges(m.get("build_requires", []), interp) if mode == "sdist" else []
    # Seed-declared build-only edges the sdist's [build-system] requires does
    # not name (a backend that consults a package only if it is importable).
    if mode == "sdist":
        # A native tool (ninja, meson, ...) stays a bare recipe name, as in
        # backend_edges.
        backends += [
            e
            for e in (
                b if b in _NATIVE_BUILD_REQ else f"{b}-cp{interp}"
                for b in m.get("build_deps") or []
            )
            if e not in backends
        ]
        # meson-python runs meson and ninja to build every package that uses
        # it as its backend.  Upstream gets them from meson-python's own
        # [project] deps and get_requires_for_build_wheel, which
        # --no-build-isolation never sees, and they are host tools, so they
        # cannot ride along as meson-python runtime deps
        # (test_required_deps_no_host_tools).  The consumer declares them
        # (pandas-cp313, job 23056: "Could not find ninja").
        if f"meson-python-cp{interp}" in backends:
            backends += [t for t in ("meson", "ninja") if t not in backends]
    # Stage the transitive runtime closure of the backends in
    # _BACKEND_RUNTIME_CLOSURE (see its definition): --no-build-isolation needs
    # each such backend importable, and importing it imports its own runtime deps,
    # which staging the backend as a build edge does not pull in. Build-only —
    # the consumer's runtime closure already resolves these for RUNTIME. The walk
    # is seeded from the column's whole build closure (backends + runtime-dep-also-
    # build-dep entries, so a runtime-dep cffi is covered), and follows deps to a
    # fixpoint (hatchling -> trove-classifiers -> calver). (Hand-written recipes
    # such as cryptography declare the edge directly and are never regenerated.)
    if mode == "sdist":
        _universe = set(meta) | set(extra_universe)
        _suffix = f"-cp{interp}"
        _staged = {*deps, *backends}
        _frontier = [
            e
            for e in (*backends, *deps)
            if e.endswith(_suffix) and e[: -len(_suffix)] in _BACKEND_RUNTIME_CLOSURE
        ]
        _seen: set[str] = set()
        while _frontier:
            _edge = _frontier.pop()
            if _edge in _seen or not _edge.endswith(_suffix):
                continue
            _seen.add(_edge)
            _bm = meta.get(_edge[: -len(_suffix)])
            if _bm is None:
                continue
            for _rt in deps_for_column(_bm, interp, _universe):
                _rt_edge = f"{_rt}{_suffix}"
                _frontier.append(_rt_edge)  # transitive
                if _rt_edge not in _staged:
                    backends.append(_rt_edge)
                    _staged.add(_rt_edge)

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
    # A column claims Windows only when its whole runtime closure builds there.
    # Generated columns always do; a hand-written dep family may not (pycares,
    # coincurve, ... link cvcpkg native libraries and are linux/macos only), and
    # claiming Windows over one is the phantom closure edge
    # scripts/validate_all_recipes.py rejects.  The noarch `any` build stays.
    if any(p == "windows" for p, _ in plats):
        gap = _windows_gap(out, base, interp, meta, extra_universe)
        if gap:
            plats = [(p, s) for p, s in plats if p != "windows"]
            print(f"  no-windows {name}: no Windows build for {', '.join(gap)}", file=sys.stderr)
    env = ""
    if kind == "pure" and base in _PURE_BUILD_ENV:
        env = "      env:\n" + "".join(
            f'        {k}: "{v}"\n' for k, v in sorted(_PURE_BUILD_ENV[base].items())
        )
    matrix = "".join(f"    - platform: {p}\n      script: {s}\n{env}" for p, s in plats)
    body += "\nbuild:\n  build_type_independent: true\n  matrix:\n" + matrix

    windows = any(p == "windows" for p, _ in plats) or (
        mode != "sdist" and any(k.startswith("windows") for k in arts)
    )
    body += "\npackage:\n  files:\n"
    # A column with a Windows build declares BOTH install layouts: CPython's
    # lib/pythonX.Y/site-packages + bin/ everywhere else, Lib/site-packages +
    # Scripts/ on Windows.  Any one build produces exactly one of them, and the
    # pack-time check (builder.check_declared_files) fails on a declared entry
    # that matched nothing, so each layout is scoped to the matrix platforms
    # that produce it.  A noarch pack is checked as platform "any", which is
    # what a pure column's non-Windows entry is.  Unscoped, every from-source
    # pure column failed to pack on the fleet ("Lib/site-packages/... match
    # nothing"), and its Windows column the same way in reverse.
    unix_scope = sorted({p for p, _ in plats if p != "windows"})
    for g in _files_globs(m, base, interp, windows):
        if not windows:
            body += f"    - {g}\n"  # one layout: nothing to scope
        elif g.startswith(("Lib/", "Scripts/")):
            body += f'    - {{ glob: "{g}", platforms: [windows] }}\n'
        elif unix_scope:
            body += f'    - {{ glob: "{g}", platforms: [{", ".join(unix_scope)}] }}\n'
        # else: a Windows-only column; it never produces the unix layout.

    floor = _RESURRECTED_FLOOR.get(base, 1) if not interp.endswith("t") else 1
    _write_recipe(d, body, floor=floor)

    check = m.get("check") or f"import {_toppkg(base)}"
    # A meson-backed sdist (meson-python) refuses meson's subproject fallback,
    # as every meson recipe does: a missing dependency fails the build instead
    # of being downloaded and built from upstream.
    meson = "meson" in backends
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
            meson_note=_MESON_NOTE if meson else "",
            meson_args="    -C setup-args=--wrap-mode=nofallback \\\n" if meson else "",
            # An any-only column (Windows dropped by _windows_gap) is a noarch
            # recipe in the strict sense: it builds once on whichever host claims
            # it, so it must not read CVC_PLATFORM (tests/unit/test_noarch_recipes).
            env_block=(
                _SDIST_NOARCH_ENV_BLOCK if all(p == "any" for p, _ in plats) else _SDIST_ENV_BLOCK
            ),
        ),
        encoding="utf-8",
        newline="\n",
    )
    (d / "build.sh").chmod(0o755)
    ps1 = d / "build.ps1"
    if windows and mode == "sdist":
        ps1.write_text(
            _BUILD_PS1_SDIST.format(
                name=name,
                check=_ps1_sq(check),
                dist=m["pypi_name"],
                version=m["version"],
                meson_args="    -C setup-args=--wrap-mode=nofallback `\n" if meson else "",
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


# package.files for poetry.lock packages that install a single top-level module
# file rather than a package directory.  Seeds say this with their own "files";
# lock-derived packages have no seed entry, so without this they got the
# default "<module>/" glob, which matches nothing and fails a strict-globs
# build (six-cp313 and typing-extensions-cp313, 2026-10-13).  Found by scanning
# every published cp312 bundle whose recipe used the directory default.
_LOCK_FILES = {
    "six": ["six.py", "six-*.dist-info/"],
    "typing-extensions": ["typing_extensions.py", "typing_extensions-*.dist-info/"],
}


def _files_globs(m, base, interp, windows: bool) -> list[str]:
    """package.files for one column.  The payload lands only under this
    column's interpreter dir, so the globs pin the exact version dir."""
    ver = col_version(interp)
    prefix = f"lib/python{ver}/site-packages/"
    files = m.get("files") or _LOCK_FILES.get(base)  # paths relative to site-packages
    if files:
        globs = [prefix + f for f in files]
    else:
        globs = [prefix + f"{_toppath(base)}/", prefix + "*.dist-info/"]
    for script in SCRIPT_PACKAGES.get(base, ()):
        globs.append(f"bin/{script}")
    if windows:
        if files:
            globs += ["Lib/site-packages/" + f for f in files]
        else:
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
    # PEP-420 namespace: the wheel ships poetry/core/ only (no
    # poetry/__init__.py), and `import poetry_core` does not exist.
    "poetry-core": "poetry.core",
    # The import package is PIL, not pillow — without this a regenerated
    # files glob would name a `pillow/` dir that never exists (the shipped
    # hand-written recipes glob PIL/ correctly).
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "dnspython": "dns",
    "python-decouple": "decouple",
    "restrictedpython": "RestrictedPython",
    # PEP-420 namespace like poetry-core: the wheel ships pdm/backend/ only.
    "pdm-backend": "pdm.backend",
    "absl-py": "absl",
    # PEP-420 namespace: the wheel ships ruamel/yaml/ with no ruamel/__init__.py.
    "ruamel-yaml": "ruamel.yaml",
    # PEP-420 namespace like ruamel-yaml: coherent/licensed/ only.
    "coherent-licensed": "coherent.licensed",
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
#
# {env_block} is _SDIST_ENV_BLOCK, except for an any-only column (see
# _emit_column), which must not read CVC_PLATFORM at all.
_SDIST_ENV_BLOCK = """# Platform toolchain/env.  Sourced conditionally: a noarch (platform: any)
# column can be claimed by a builder whose platform ships no env-*.sh.
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
"""
_SDIST_NOARCH_ENV_BLOCK = """# Noarch column (platform: any only): built once on whichever
# host claims it, so it sources no platform env script -- a pure-Python build
# needs no toolchain.
"""
_MESON_NOTE = """# --wrap-mode=nofallback: a dependency meson cannot find fails the build; it is
# never downloaded and built from upstream as a subproject.
"""
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
{env_block}# shellcheck disable=SC1091
. "${{SCRIPT_DIR}}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${{CVC_PYTHON_ABI:={abi}}}"
: "${{CVC_PYTHON_INTERPRETER:={interpreter}}}"
PY_EXE="$(cvc_python_exe)"
echo "{name}: building with ${{PY_EXE}}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
# A free-threaded column's packages live under lib/python3.13t, not
# lib/python3.13: keep the `t`, or the bridge names a directory that does not
# exist and the backend only imports when the build prefix IS the install prefix.
_D="${{CVC_PYTHON_ABI#cp}}"; _T=""
case "${{_D}}" in *t) _T="t"; _D="${{_D%t}}" ;; esac
_PYMM="${{_D:0:1}}.${{_D:1}}${{_T}}"          # cp311 -> 3.11, cp313t -> 3.13t
if [ -n "${{CVC_BUILD_PREFIX:-}}" ]; then
    _BP_SITE="${{CVC_BUILD_PREFIX}}/lib/python${{_PYMM}}/site-packages"
    export PYTHONPATH="${{_BP_SITE}}${{PYTHONPATH:+:${{PYTHONPATH}}}}"
fi

# ── 3. Build the wheel from the extracted sdist ─────────────────────────────
# --no-deps: transitive deps are cvcpkg recipes, resolved by the depends graph.
# --no-index: no network resolution — this is what makes air-gapped builds work.
{meson_note}WHEELHOUSE="${{CVC_BUILD_DIR:-${{CVC_SOURCE_DIR}}}}/wheelhouse"
mkdir -p "${{WHEELHOUSE}}"
"${{PY_EXE}}" -m pip wheel \\
    --no-build-isolation \\
    --no-deps \\
    --no-index \\
    --no-cache-dir \\
{meson_args}    --wheel-dir "${{WHEELHOUSE}}" \\
    "${{CVC_SOURCE_DIR}}"

# --no-deps means the wheelhouse holds exactly one wheel: ours.  A shell glob,
# not `find -print -quit`: -quit is a GNU findutils extension the *BSD find does
# not implement, and it aborts the build after the wheel has already compiled.
WHEEL=""
for WHEEL in "${{WHEELHOUSE}}"/*.whl; do [ -e "${{WHEEL}}" ] && break; WHEEL=""; done
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
{meson_args}    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
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
