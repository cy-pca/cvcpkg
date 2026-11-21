#!/usr/bin/env bash
# recipes/snappy/build.sh -- CMake build of Snappy, shared or static per CVC_LINK.
# Snappy's own tests and benchmarks are off: they fetch googletest and google-benchmark
# as git submodules, which the release tarball does not carry.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DSNAPPY_BUILD_TESTS=OFF \
    -DSNAPPY_BUILD_BENCHMARKS=OFF \
    -DSNAPPY_FUZZING_BUILD=OFF \
    -DSNAPPY_INSTALL=ON
