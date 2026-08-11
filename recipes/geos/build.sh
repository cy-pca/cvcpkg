#!/usr/bin/env bash
# recipes/geos/build.sh — GEOS via CMake on the POSIX platforms.
#
# Windows counterpart is build.ps1; same options, same contract.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-posix.sh"

cmake \
    -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CVC_CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${CVC_BUILD_SHARED_LIBS}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_TESTING=OFF \
    -DBUILD_DOCUMENTATION=OFF \
    -DBUILD_BENCHMARKS=OFF \
    -DBUILD_GEOSOP=OFF

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS:-4}"
cmake --install "${CVC_BUILD_DIR}"

# shapely binds the C API; prove it is present rather than letting shapely's
# build report a missing "geos_c" that says nothing about this package.
[ -f "${CVC_INSTALL_DIR}/include/geos_c.h" ] || {
    echo "geos: geos_c.h missing from the staged prefix" >&2; exit 1; }

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true
echo "geos: build + staging complete"
