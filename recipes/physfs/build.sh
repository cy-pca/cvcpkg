#!/usr/bin/env bash
# recipes/physfs/build.sh — build PhysicsFS from source with CMake.
#
# physfs uses its OWN static/shared toggle (PHYSFS_BUILD_STATIC /
# PHYSFS_BUILD_SHARED), not BUILD_SHARED_LIBS, so translate CVC_LINK here
# and build exactly one variant per link mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _static=ON
    _shared=OFF
else
    _static=OFF
    _shared=ON
fi

cvc_cmake_build \
    -DPHYSFS_BUILD_STATIC="${_static}" \
    -DPHYSFS_BUILD_SHARED="${_shared}" \
    -DPHYSFS_BUILD_TEST=OFF \
    -DPHYSFS_BUILD_DOCS=OFF
