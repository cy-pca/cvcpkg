#!/usr/bin/env bash
# recipes/brotli/build.sh — build Brotli from source with CMake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBROTLI_DISABLE_TESTS=ON
