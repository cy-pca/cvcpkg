#!/usr/bin/env bash
# recipes/openssl/build-wasm.sh — cross-compile OpenSSL to wasm.
# Uses Emscripten's built-in OpenSSL support via emconfigure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# OpenSSL's Configure (capital C) supports a "cc" target for generic cross.
# We call Configure directly with CC/CXX set to the Emscripten compilers
# instead of using emconfigure, which can garble paths when emsdk_env.sh
# has already set CC to the full emcc path.
CC=emcc CXX=em++ AR=emar RANLIB=emranlib perl Configure \
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

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
