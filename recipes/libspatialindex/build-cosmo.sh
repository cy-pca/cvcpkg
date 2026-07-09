#!/usr/bin/env bash
# recipes/libspatialindex/build-cosmo.sh — cross-compile libspatialindex with Cosmopolitan (cosmoc++).
# Builds static libspatialindex.a + libspatialindex_c.a for the APE fat-binary toolchain.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF
