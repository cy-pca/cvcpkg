#!/usr/bin/env bash
# recipes/sionna-rt-cp313/build.sh -- build Sionna RT 2.2.0 FROM SOURCE (its
# v2.2.0 tree, setuptools backend) for the cp313 column and install the
# py3-none-any wheel into this recipe's staging prefix.  The shape of a
# generated pure column's build.sh; test.sh exercises the result.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Noarch column (platform: any only): built once on whichever host claims it,
# so it sources no platform env script -- a pure-Python build needs no
# toolchain.
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY_EXE="$(cvc_python_exe)"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "sionna-rt-cp313: building with ${PY_EXE}"

# ── 2. Bridge the build-only backend (setuptools) onto that interpreter ─────
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    export PYTHONPATH="${CVC_BUILD_PREFIX}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── 3. Build the wheel from the staged source tree ──────────────────────────
WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

WHEEL=""
for WHEEL in "${WHEELHOUSE}"/sionna_rt-*.whl; do [ -e "${WHEEL}" ] && break; WHEEL=""; done
[ -n "${WHEEL}" ] || { echo "sionna-rt-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
case "$(basename "${WHEEL}")" in
    *-py3-none-any.whl) ;;
    *) echo "sionna-rt-cp313: expected a py3-none-any wheel, got $(basename "${WHEEL}")" >&2; exit 1 ;;
esac
echo "sionna-rt-cp313: built $(basename "${WHEEL}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SCENE="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages/sionna/rt/scenes/simple_street_canyon/simple_street_canyon.xml"
[ -f "${SCENE}" ] || { echo "sionna-rt-cp313: built-in scenes missing from the wheel (${SCENE})" >&2; exit 1; }
echo "sionna-rt-cp313: build complete (test.sh verifies)"
