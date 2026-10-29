#!/usr/bin/env bash
# recipes/pugixml/build.sh — build pugixml as a shared library.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DPUGIXML_BUILD_TESTS=OFF \
    -DPUGIXML_BUILD_SHARED_AND_STATIC_LIBS=OFF \
    -DPUGIXML_USE_VERSIONED_LIBDIR=OFF \
    -DPUGIXML_USE_POSTFIX=OFF
