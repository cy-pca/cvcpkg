#!/usr/bin/env bash
# recipes/setuptools-rust-cp313/build.sh — build setuptools_rust 1.13.0 FROM SOURCE (sdist) for the
# cp313 interpreter column of the per-interpreter wheel matrix.
#
# WHY IT IS HERE: this is the setuptools plugin that runs cargo. maturin's own
# sdist bootstraps through it (`from setuptools_rust import RustBin` in setup.py)
# and bcrypt declares its extension through [[tool.setuptools-rust.ext-modules]],
# so it sits between `rust` and both of them.
#
# It is itself pure Python — it does not compile anything at ITS build time, only
# at its consumers'. That is why this column is `platform: any` and carries no
# `rust` edge: the toolchain requirement belongs to the recipes that invoke the
# plugin (maturin, bcrypt), which declare `rust` themselves. Its runtime edge on
# semantic-version is real, though: setuptools_rust imports it at module scope.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend already provisioned into the prefix by the depends.build edges,
# then pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees setuptools_rust as
# satisfied. This script does NOT re-solve the build closure — it assumes the
# declared deps are present and fails loudly when an import is missing.
#
# Pure Python: no compiler, therefore no _common/env-<platform>.sh and no
# build.ps1. The recipe is `platform: any`, built once and published noarch,
# exactly like every other pure-Python column in the matrix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_interp_version

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
# python.interpreter/python.abi are exported by the builder; keep defaults so the
# script stays runnable standalone.
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
# cvc_interp_version keeps the free-threaded suffix (python313t -> 3.13t), which
# is also the site-packages directory name — do not re-derive it from the ABI tag.
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "setuptools-rust-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. depends.build python columns land in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge — and the
# bridge is also what lets the prefix's setuptools win over whatever the base
# interpreter bundles, because PYTHONPATH precedes site-packages in sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools, setuptools_scm; print("setuptools-rust-cp313: setuptools", setuptools.__version__, "+ setuptools_scm")'

# ── 3. Build the wheel from the sdist (offline, no build isolation) ────────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so it cannot
# resolve anything over the network (this is what makes air-gapped builds work);
# --no-build-isolation so the backend uses the prefix's packages rather than
# downloading its own.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# Plain glob rather than `find -print -quit`: OpenBSD's find has no -quit, and a
# `find | head` pipeline trips `set -o pipefail` when head closes the pipe early.
shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "setuptools-rust-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "setuptools-rust-cp313: built $(basename "${WHEEL}")"

# ── 4. Install into THIS recipe's staging prefix ──────────────────────────
# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree (package.files is not a
# filter), so installing --prefix into the initially-empty per-recipe dir IS the
# packaging contract: nothing but this package can land there.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# Locate the staged site-packages by globbing the known install schemes (same
# reason as above: no `find -quit`). An unmatched glob stays literal, so the
# `-d` test simply fails for it.
SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || {
    echo "setuptools-rust-cp313: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "setuptools-rust-cp313: staged into ${SITE_PACKAGES}"

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 5. Verification ────────────────────────────────────────────────────────
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# A free-threaded column must be proven WITHOUT the GIL: a package that only
# imports under a re-enabled GIL has not been shown to work without one, and
# cvcpkg would be publishing an unproven guarantee (see _common/python-wheel.sh).
PYARGS=()
if cvc_python_is_free_threaded; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
"${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import setuptools_rust
from setuptools_rust import Binding, RustBin, RustExtension
print("setuptools_rust ->", setuptools_rust.__file__)
# semantic_version is a module-scope import of setuptools_rust.extension /
# rustc_info; if the runtime edge were missing, the lines above would already
# have raised. Name it explicitly so the failure is legible.
import semantic_version
print("semantic_version:", semantic_version.__file__)
# The distutils command entry points are what setuptools looks up during a
# consumer's build; a wheel that installed the modules but not the metadata
# would still fail there, so check the metadata too.
from importlib.metadata import distribution
eps = {(e.group, e.name) for e in distribution("setuptools-rust").entry_points}
assert ("distutils.commands", "build_rust") in eps, sorted(eps)
assert ("setuptools.finalize_distribution_options", "setuptools_rust") in eps, sorted(eps)
print("setuptools_rust round-trip OK")
PYCHECK

echo "setuptools-rust-cp313: build + verification complete"
