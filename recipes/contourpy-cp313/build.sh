#!/usr/bin/env bash
# recipes/contourpy-cp313/build.sh — build contourpy 1.3.1 FROM SOURCE
# (hand-converted: generated shape + the pybind11 pkg-config fix in step 2b).
#
# WHY FROM SOURCE: a PyPI wheel is somebody else's compiled artifact, linked
# against libraries we did not build.  cvcpkg fetches and sha256-verifies the
# SDIST (source.type: tarball) instead, and this script compiles the wheel with
# the prefix's own interpreter, then installs it — so the bundle contains only
# things cvcpkg built.  pip still produces a real wheel (dist-info/RECORD/
# METADATA), so a consumer's later `pip install <other>` coexists and pip's
# resolver sees this package as satisfied.
#
# BUILD BACKEND: meson, meson-python-cp313, pybind11-cp313
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
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY_EXE="$(cvc_python_exe)"
echo "contourpy-cp313: building with ${PY_EXE}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp311 -> 3.11
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    _BP_SITE="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages"
    export PYTHONPATH="${_BP_SITE}${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── 2b. Make the staged pybind11 discoverable by meson ──────────────────────
# meson's dependency('pybind11') probes pkg-config, the pybind11-config tool,
# then cmake — and all three miss.  The staged bin/pybind11-config cannot
# run: its shebang is the ephemeral build-prefix interpreter of the machine
# that BUILT pybind11 (the same broken-console-script disease numpy's build
# shims around for cython).  pybind11 publishes its .pc directory for exactly
# this case; put it on PKG_CONFIG_PATH and meson's first probe hits.
_PB11_PC="$("${PY_EXE}" -m pybind11 --pkgconfigdir)"
[ -n "${_PB11_PC}" ] || { echo "contourpy-cp313: python -m pybind11 --pkgconfigdir returned nothing" >&2; exit 1; }
export PKG_CONFIG_PATH="${_PB11_PC}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

# ── 3. Build the wheel from the extracted sdist ─────────────────────────────
# --no-deps: transitive deps are cvcpkg recipes, resolved by the depends graph.
# --no-index: no network resolution — this is what makes air-gapped builds work.
WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# --no-deps means the wheelhouse holds exactly one wheel: ours.
WHEEL="$(find "${WHEELHOUSE}" -maxdepth 1 -name '*.whl' -print -quit)"
[ -n "${WHEEL}" ] || { echo "contourpy-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "contourpy-cp313: built $(basename "${WHEEL}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
# stage_bundle ships the whole CVC_INSTALL_DIR tree, so installing --prefix into
# an empty dir with --no-deps is what keeps the bundle to just this package.
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# ── 5. Verify the staged package actually imports ──────────────────────────
# Drop the build-prefix bridge first: the check must exercise the RUNTIME
# closure, not accidentally import a build-only backend.
unset PYTHONPATH
cvc_python_check "import contourpy"
