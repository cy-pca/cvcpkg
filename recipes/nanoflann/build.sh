#!/usr/bin/env bash
# recipes/nanoflann/build.sh — nanoflann's own CMake install (header-only): include/nanoflann.hpp,
# share/cmake/nanoflann/ (nanoflann::nanoflann) and lib/pkgconfig/nanoflann.pc. Serves wasm and
# wasm-mt too: env-wasm*.sh (through ${CVC_PLATFORM}) redefines cvc_cmake_build with the
# Emscripten toolchain.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DNANOFLANN_BUILD_EXAMPLES=OFF \
    -DNANOFLANN_BUILD_TESTS=OFF

install -d "${CVC_INSTALL_DIR}/share/licenses/nanoflann"
install -m 0644 "${CVC_SOURCE_DIR}/COPYING" "${CVC_INSTALL_DIR}/share/licenses/nanoflann/COPYING"

[ -f "${CVC_INSTALL_DIR}/include/nanoflann.hpp" ] \
    || { echo "nanoflann: include/nanoflann.hpp not installed" >&2; exit 1; }
[ -f "${CVC_INSTALL_DIR}/share/cmake/nanoflann/nanoflannConfig.cmake" ] \
    || { echo "nanoflann: share/cmake/nanoflann/nanoflannConfig.cmake not installed" >&2; exit 1; }
grep -qF "#define NANOFLANN_VERSION_STRING \"${CVC_VERSION}\"" "${CVC_INSTALL_DIR}/include/nanoflann.hpp" \
    || { echo "nanoflann: installed nanoflann.hpp is not ${CVC_VERSION}" >&2; exit 1; }
echo "nanoflann: installed nanoflann.hpp and its CMake package"
