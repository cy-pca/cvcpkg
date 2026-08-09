#!/usr/bin/env bash
# recipes/brotli/build-cosmo.sh — cross-compile Brotli with Cosmopolitan.
#
# Pure-C, dependency-free, no architecture-specific code paths, and the CLI
# only wants stdio plus utime/chmod — all of which Cosmopolitan provides on
# every host it targets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DBROTLI_DISABLE_TESTS=ON
