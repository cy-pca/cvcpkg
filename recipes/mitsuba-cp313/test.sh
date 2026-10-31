#!/usr/bin/env bash
# recipes/mitsuba-cp313/test.sh -- run by cvcpkg after build.sh, before packing
# (recipe.yaml `test:`).  check.py imports the staged mitsuba against the
# drjit-cp313 runtime dependency and renders test-scene.xml and an equivalent
# load_dict() scene with scalar_rgb and, where libLLVM is in the closure, with
# llvm_ad_mono_polarized.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, PYTHONNOUSERSITE
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/variants.sh"                          # mi_variants

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
export PYTHONPATH="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
# drjit picks libLLVM from its own prefix (drjit-cp313's patch); an inherited
# override would hide a broken lookup.
unset DRJIT_LIBLLVM_PATH

case "${CVC_PLATFORM}" in
    linux|macos) CHECK_LLVM=1 ;;
    *) CHECK_LLVM=0 ;;
esac

"${PY}" "${SCRIPT_DIR}/check.py" "$(mi_variants)" "${SCRIPT_DIR}/test-scene.xml" "${CHECK_LLVM}"
