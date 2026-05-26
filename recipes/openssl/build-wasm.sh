#!/usr/bin/env bash
# recipes/openssl/build-wasm.sh — cross-compile OpenSSL to wasm.
# Uses Emscripten's built-in OpenSSL support via emconfigure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# OpenSSL's Configure (capital C) supports a "cc" target for generic cross.
emconfigure perl Configure \
    linux-generic32 \
    --prefix="${CVC_INSTALL_DIR}" \
    --openssldir="${CVC_INSTALL_DIR}/ssl" \
    no-shared \
    no-asm \
    no-threads \
    no-engine \
    no-dso \
    no-tests \
    -DNO_FORK

emmake make -j "${CVC_JOBS}"
emmake make install_sw
