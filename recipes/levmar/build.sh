#!/usr/bin/env bash
# recipes/levmar/build.sh — build levmar from vendored sources.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# levmar is built from the vendored third-party/levmar/ CMakeLists.txt
# rather than a downloaded tarball.
LEVMAR_SRC="${CVC_RECIPE_DIR}/../../third-party/levmar"

# Per-platform BLAS/LAPACK strategy:
#   Linux  — OpenBLAS built from recipe, installed in prefix
#   macOS  — Apple Accelerate framework (system-provided)
LEVMAR_CMAKE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    LEVMAR_CMAKE_EXTRA+=(-DBLA_VENDOR=OpenBLAS)
    LEVMAR_CMAKE_EXTRA+=(-DCMAKE_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib")
fi

cmake -G Ninja \
    -S "${LEVMAR_SRC}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    ${LEVMAR_CMAKE_EXTRA[@]+"${LEVMAR_CMAKE_EXTRA[@]}"}
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
