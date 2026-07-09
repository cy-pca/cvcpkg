#!/usr/bin/env bash
# recipes/libgeos/build-cosmo.sh — cross-compile GEOS with Cosmopolitan (cosmoc++).
# Builds static libgeos.a + libgeos_c.a for the APE fat-binary toolchain; the
# C API (geos_c.h) is the intended entry point for cosmo consumers.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_BENCHMARKS=OFF
