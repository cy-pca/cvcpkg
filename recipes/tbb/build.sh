#!/usr/bin/env bash
# recipes/tbb/build.sh — build oneTBB, tbbmalloc and tbbmalloc_proxy.
#
# TBB_STRICT defaults to ON (-Werror), which turns any new compiler warning on
# a builder into a failed build; it is a developer setting, so it is off here.
# hwloc is not searched for (TBB_DISABLE_HWLOC_AUTOMATIC_SEARCH): pkg-config
# would otherwise find a host libhwloc and build tbbbind against it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DTBB_TEST=OFF \
    -DTBB_EXAMPLES=OFF \
    -DTBB_STRICT=OFF \
    -DTBBMALLOC_BUILD=ON \
    -DTBBMALLOC_PROXY_BUILD=ON \
    -DTBB_DISABLE_HWLOC_AUTOMATIC_SEARCH=ON
