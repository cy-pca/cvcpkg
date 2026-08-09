#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DENABLE_SHARED="${BUILD_SHARED_LIBS}" \
    -DENABLE_STATIC="$(if [ "$BUILD_SHARED_LIBS" = "OFF" ]; then echo ON; else echo OFF; fi)" \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF
