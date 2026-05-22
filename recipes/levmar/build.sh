#!/usr/bin/env bash
# recipes/levmar/build.sh — build levmar from vendored sources.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# levmar is built from the vendored third-party/levmar/ CMakeLists.txt
# rather than a downloaded tarball.
LEVMAR_SRC="${CVC_RECIPE_DIR}/../../third-party/levmar"

cmake -G Ninja \
    -S "${LEVMAR_SRC}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DBLA_VENDOR=OpenBLAS
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
