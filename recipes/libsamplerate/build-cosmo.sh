#!/usr/bin/env bash
# recipes/libsamplerate/build-cosmo.sh — cross-compile libsamplerate with Cosmopolitan.
#
# Nothing in the resampler needs more than libm and malloc, both of which
# Cosmopolitan provides, and there is no assembly or ISA-specific code path to
# constrain — so unlike libpng or libopus there is no hardware-optimisation
# switch to turn off for APE portability.  Static-only is a non-issue: the cross
# builds are static regardless.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DLIBSAMPLERATE_EXAMPLES=OFF \
    -DLIBSAMPLERATE_INSTALL=ON
