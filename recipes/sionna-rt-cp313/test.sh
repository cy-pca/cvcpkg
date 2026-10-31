#!/usr/bin/env bash
# recipes/sionna-rt-cp313/test.sh -- run by cvcpkg after build.sh, before
# packing (recipe.yaml `test:`).  check.py imports sionna.rt and runs a small
# PathSolver and RadioMapSolver on the CPU (llvm_ad_mono_polarized).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, PYTHONNOUSERSITE

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
export PYTHONPATH="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
unset DRJIT_LIBLLVM_PATH
# Headless builders: matplotlib (imported by sionna.rt) must not look for a display.
export MPLBACKEND=Agg
export MPLCONFIGDIR="${CVC_BUILD_DIR:-${TMPDIR:-/tmp}}/mplconfig"

# mitsuba-cp313 and drjit-cp313 need a Haswell or newer CPU on x86_64
# (x86-64-v3); on an older host importing them dies with SIGILL.  Say why.
case "$(uname -m)" in
    x86_64|amd64)
        if [ -r /proc/cpuinfo ]; then
            missing=""
            for f in avx2 fma f16c; do grep -q -w "${f}" /proc/cpuinfo || missing="${missing} ${f}"; done
            if [ -n "${missing}" ]; then
                echo "sionna-rt-cp313: this x86_64 host lacks${missing}; Mitsuba and Dr.Jit need a Haswell or newer CPU (x86-64-v3)" >&2
                exit 1
            fi
        fi ;;
esac

"${PY}" "${SCRIPT_DIR}/check.py"
