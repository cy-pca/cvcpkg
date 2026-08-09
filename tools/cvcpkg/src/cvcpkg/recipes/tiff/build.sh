#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -Dtiff-tests=OFF \
    -Dtiff-tools=OFF \
    -Dtiff-contrib=OFF \
    -Dtiff-docs=OFF \
    -Djbig=OFF \
    -Dlibdeflate=OFF
