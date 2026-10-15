#!/usr/bin/env bash
# recipes/geos/build.sh — GEOS via CMake on the POSIX platforms.
#
# Windows counterpart is build.ps1; same options, same contract.
#
# cvc_cmake_build (from the platform's env script) configures with Ninja,
# CMAKE_BUILD_TYPE/BUILD_SHARED_LIBS for this config and link, PIC and a
# relocatable install RPATH, then builds, installs and rewrites install paths.
# This script used to source a _common/env-posix.sh that never existed and
# read CVC_CMAKE_BUILD_TYPE/CVC_BUILD_SHARED_LIBS, which no builder sets, so it
# had never built on any POSIX platform.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_DOCUMENTATION=OFF \
    -DBUILD_BENCHMARKS=OFF \
    -DBUILD_GEOSOP=OFF

# shapely binds the C API; prove it is present rather than letting shapely's
# build report a missing "geos_c" that says nothing about this package.
[ -f "${CVC_INSTALL_DIR}/include/geos_c.h" ] || {
    echo "geos: geos_c.h missing from the staged prefix" >&2; exit 1; }

echo "geos: build + staging complete"
