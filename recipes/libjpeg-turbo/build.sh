#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# (no-op touch: re-push to embed the current recipes/_common/env-openbsd.sh,
#  which now exports LD_LIBRARY_PATH so cmake, invoked here as a build-tool
#  dependency, can resolve its own runtime deps like libcurl.so on OpenBSD.)

cvc_cmake_build \
    -DENABLE_SHARED="${BUILD_SHARED_LIBS}" \
    -DENABLE_STATIC="$(if [ "$BUILD_SHARED_LIBS" = "OFF" ]; then echo ON; else echo OFF; fi)" \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF
