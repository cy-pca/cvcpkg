#!/usr/bin/env bash
# recipes/openpgl/build.sh — build the Open Path Guiding Library.
#
# Blender 5.2 links Open PGL statically; this is the shared library (static
# with CVC_LINK=static), otherwise upstream's defaults, as Blender: SSE4.2 and
# AVX2 kernels on x86-64 (no AVX-512), NEON and NEON2X on arm64.
# CMAKE_FIND_PACKAGE_PREFER_CONFIG: Open PGL's bundled FindTBB.cmake would
# otherwise run first and wrap libtbb in its own IMPORTED_NO_SONAME target;
# preferring config mode links oneTBB's own TBB::tbb from TBBConfig.cmake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

if [[ "${CVC_LINK}" == "static" ]]; then
    static=ON
else
    static=OFF
fi

cvc_cmake_build \
    -DOPENPGL_BUILD_STATIC="${static}" \
    -DOPENPGL_BUILD_TOOLS=OFF \
    -DOPENPGL_BUILD_CHECK_TOOL=OFF \
    -DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON
