#!/usr/bin/env bash
# recipes/fftw3/build.sh — build FFTW3 double + single + threads.
# Two cmake builds: double precision, then single precision (float).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

COMMON_ARGS=(
    -DBUILD_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_THREADS=ON
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
)

# Pass 1: double precision
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/double" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/double" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/double"

# Pass 2: single precision (float)
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/float" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DENABLE_FLOAT=ON \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/float" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/float"

# ── Rewrite pkg-config files for relocatability ──
# FFTW3's CMake install bakes CVC_INSTALL_DIR (a /tmp/cvcpkg-builder/... path
# on the builder) into prefix=/exec_prefix=/libdir=/includedir=.  Rewrite them
# to be relative to ${pcfiledir} so downstream consumers work in any prefix.
for pc in "${CVC_INSTALL_DIR}"/lib/pkgconfig/fftw3*.pc; do
    [ -f "$pc" ] || continue
    sed -i.bak \
        -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' \
        -e 's|^exec_prefix=.*|exec_prefix=${prefix}|' \
        -e 's|^libdir=.*|libdir=${prefix}/lib|' \
        -e 's|^includedir=.*|includedir=${prefix}/include|' \
        "$pc"
    rm -f "${pc}.bak"
done
