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

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_INSTALL_RPATH="${_rpath}" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DLLVM_DIR="${CVC_DEPS_PREFIX}/lib/cmake/llvm"

cmake --build "${CVC_BUILD_DIR}" --target llvm-cbe -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
