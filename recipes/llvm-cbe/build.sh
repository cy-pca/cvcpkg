#!/usr/bin/env bash
# recipes/llvm-cbe/build.sh — build the LLVM C Backend (llvm-cbe).
#
# llvm-cbe is a standalone CMake project that locates the installed
# LLVM via find_package(LLVM CONFIG).  CVC_DEPS_PREFIX must contain
# an LLVM installation (from the llvm recipe).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_BUILD_DIR:?}"
: "${CVC_DEPS_PREFIX:?}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

case "${CVC_PLATFORM}" in
    macos)   _rpath="@loader_path;@loader_path/../lib" ;;
    *)       _rpath="\$ORIGIN;\$ORIGIN/../lib" ;;
esac

# Match LLVM's RTTI setting.  LLVM builds without RTTI by default, and a
# consumer compiled with it references typeinfo symbols libLLVM never defines:
# the linux/x86_64 fleet build died linking tools/llvm-cbe with "undefined
# reference to `typeinfo for llvm::cl::GenericOptionValue'".  llvm-cbe's
# CMakeLists does not apply LLVM_ENABLE_RTTI itself, so ask llvm-config.
_cxxflags="${CXXFLAGS:-}"
if [ "$("${CVC_DEPS_PREFIX}/bin/llvm-config" --has-rtti 2>/dev/null)" != "YES" ]; then
    _cxxflags="${_cxxflags} -fno-rtti"
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_CXX_FLAGS="${_cxxflags}" \
    -DCMAKE_INSTALL_RPATH="${_rpath}" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DLLVM_DIR="${CVC_DEPS_PREFIX}/lib/cmake/llvm"

cmake --build "${CVC_BUILD_DIR}" --target llvm-cbe -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Built against an installed LLVM, llvm-cbe's tool target has no install rule
# (add_llvm_tool installs only inside an LLVM build tree), so `cmake --install`
# leaves bin/ empty and strict package.files refused the pack.  Install the
# binary the build produced.
if [ ! -x "${CVC_INSTALL_DIR}/bin/llvm-cbe" ]; then
    _cbe="$(find "${CVC_BUILD_DIR}" -type f -name llvm-cbe -perm -u+x | sed -n 1p)"
    [ -n "${_cbe}" ] || { echo "llvm-cbe: no llvm-cbe binary under ${CVC_BUILD_DIR}" >&2; exit 1; }
    mkdir -p "${CVC_INSTALL_DIR}/bin"
    install -m 755 "${_cbe}" "${CVC_INSTALL_DIR}/bin/llvm-cbe"
fi
