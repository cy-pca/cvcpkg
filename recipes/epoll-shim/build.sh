#!/usr/bin/env bash
# recipes/epoll-shim/build.sh — build epoll-shim from source with CMake.
#
# epoll-shim is a small, dependency-free C library (kqueue-backed epoll/timerfd/
# signalfd/eventfd) for the BSDs. cvc_cmake_build handles the prefix / build type /
# -DBUILD_SHARED_LIBS (from CVC_LINK) / install / rpath rewrite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DENABLE_COMPILER_WARNINGS=OFF
