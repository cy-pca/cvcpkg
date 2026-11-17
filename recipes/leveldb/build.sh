#!/usr/bin/env bash
# recipes/leveldb/build.sh -- CMake build of LevelDB, shared or static per CVC_LINK.
# The optional-library probes are answered "no" up front (see the recipe's notes), so the result
# never links the build host's snappy, crc32c or tcmalloc.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DLEVELDB_BUILD_TESTS=OFF \
    -DLEVELDB_BUILD_BENCHMARKS=OFF \
    -DLEVELDB_INSTALL=ON \
    -DHAVE_SNAPPY=OFF \
    -DHAVE_CRC32C=OFF \
    -DHAVE_TCMALLOC=OFF
