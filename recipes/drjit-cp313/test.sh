#!/usr/bin/env bash
# recipes/drjit-cp313/test.sh -- run by cvcpkg after build.sh, before packing
# (recipe.yaml `test:`).  Imports the staged drjit and exercises the scalar and
# LLVM backends (check.py).
#
# In a consumer's prefix libLLVM is the llvm20 package's <prefix>/lib/libLLVM.*,
# beside lib/python3.13/site-packages.  Here llvm20 sits in CVC_DEPS_PREFIX and
# drjit in the staging tree CVC_INSTALL_DIR, so the test links libLLVM into the
# staging tree's lib/ for its duration: check.py then asserts that the
# relocatable lookup drjit-libllvm-from-prefix.patch adds chose exactly that
# file, and that the LLVM backend came up from it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, PYTHONNOUSERSITE

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
export PYTHONPATH="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"

case "${CVC_PLATFORM}" in
    linux) LLVM_LEAF=libLLVM.so ;;
    macos) LLVM_LEAF=libLLVM.dylib ;;
    *) LLVM_LEAF="" ;;
esac
STAGED_LLVM=""
if [ -n "${LLVM_LEAF}" ]; then
    [ -e "${CVC_DEPS_PREFIX}/lib/${LLVM_LEAF}" ] || {
        echo "drjit-cp313 test: ${CVC_DEPS_PREFIX}/lib/${LLVM_LEAF} missing -- is the llvm20 runtime dep installed?" >&2
        exit 1
    }
    mkdir -p "${CVC_INSTALL_DIR}/lib"
    STAGED_LLVM="${CVC_INSTALL_DIR}/lib/${LLVM_LEAF}"
    ln -sf "${CVC_DEPS_PREFIX}/lib/${LLVM_LEAF}" "${STAGED_LLVM}"
    # Never ship the link: the real file is llvm20's.
    trap 'rm -f "${STAGED_LLVM}"' EXIT
fi

"${PY}" "${SCRIPT_DIR}/check.py" "${STAGED_LLVM}"
