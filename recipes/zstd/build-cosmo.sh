#!/usr/bin/env bash
# recipes/zstd/build-cosmo.sh — cross-compile zstd to a Cosmopolitan APE
# static archive via the cosmocc toolchain.
#
# zstd is plain C99 with no OS dependencies beyond stdio/malloc in the
# library proper (the CLI, which we do not build, is the part that touches
# the filesystem), so the cosmocc frontend needs no special handling.
# This closes the closure hole for `tiff` and `libiimod`, which both claim
# cosmo and runtime-depend on zstd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

# zstd's CMakeLists.txt is in build/cmake/, not at the tarball root.
CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake"

cvc_cmake_build \
    -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_CONTRIB=OFF \
    -DZSTD_BUILD_TESTS=OFF \
    -DZSTD_BUILD_STATIC=ON \
    -DZSTD_BUILD_SHARED=OFF
