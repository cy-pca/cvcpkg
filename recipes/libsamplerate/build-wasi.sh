#!/usr/bin/env bash
# recipes/libsamplerate/build-wasi.sh — cross-compile libsamplerate to wasm32-wasi.
#
# Pure floating-point resampling over caller-owned buffers: none of the wasip1
# restrictions (single-threaded, no sockets, no dlopen, no fork) touch anything
# libsamplerate does.  The src_* API never opens a file; only the examples and
# tests do, via libsndfile, and those stay off.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DLIBSAMPLERATE_EXAMPLES=OFF \
    -DLIBSAMPLERATE_INSTALL=ON
