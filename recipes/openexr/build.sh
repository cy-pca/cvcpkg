#!/usr/bin/env bash
# recipes/openexr/build.sh — build the OpenEXR libraries.
#
# OPENEXR_FORCE_INTERNAL_* stay OFF and the cvcpkg imath/libdeflate/openjph are
# on CMAKE_PREFIX_PATH, so OpenEXR neither builds its vendored libdeflate nor
# fetches OpenJPH from git at configure time. The configure log names the
# libdeflate and OpenJPH it used; a "vendored"/"downloading" line there means
# the prefix was not searched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_WEBSITE=OFF \
    -DOPENEXR_BUILD_TOOLS=OFF \
    -DOPENEXR_INSTALL_TOOLS=OFF \
    -DOPENEXR_BUILD_EXAMPLES=OFF \
    -DOPENEXR_BUILD_PYTHON=OFF \
    -DOPENEXR_BUILD_BOTH_STATIC_SHARED=OFF \
    -DOPENEXR_FORCE_INTERNAL_DEFLATE=OFF \
    -DOPENEXR_FORCE_INTERNAL_OPENJPH=OFF \
    -DOPENEXR_FORCE_INTERNAL_IMATH=OFF
