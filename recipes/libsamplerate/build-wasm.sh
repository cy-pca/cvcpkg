#!/usr/bin/env bash
# recipes/libsamplerate/build-wasm.sh — cross-compile libsamplerate to wasm.
#
# Secret Rabbit Code is a bandlimited-sinc resampler: the whole library is
# floating-point arithmetic over caller-supplied buffers, with no threads, no
# files and no assembly or runtime CPU dispatch to switch off.  Its only
# external dependency, libsndfile, is used solely by the examples and tests,
# both of which are off here exactly as in the native build.sh — so the cross
# build is dependency-free.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DLIBSAMPLERATE_EXAMPLES=OFF \
    -DLIBSAMPLERATE_INSTALL=ON
