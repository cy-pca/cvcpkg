#!/usr/bin/env bash
# recipes/openssl/build-wasm.sh — cross-compile OpenSSL to wasm.
# Uses Emscripten's built-in OpenSSL support via emconfigure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# Thread safety: plain wasm has no pthreads, so OpenSSL must be `no-threads` —
# but a no-threads OpenSSL does not define OPENSSL_THREADS, which CPython's
# _ssl/_hashlib reject ("Python requires thread-safe OpenSSL"). The wasm-mt
# flavor DOES have pthreads (env-wasm.sh sets CVC_WASM_THREADS=1 and prepends
# -pthread), so build OpenSSL WITH threads there → OPENSSL_THREADS, giving a
# thread-safe OpenSSL that CPython can build TLS against. Pass -pthread to
# Configure explicitly so OpenSSL's own compile/link carries it.
_SSL_THREADS=(no-threads)
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    _SSL_THREADS=(threads -pthread)
    echo "── openssl(wasm-mt): building thread-safe (threads + -pthread) ──"
fi

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
    "${_SSL_THREADS[@]}" \
    no-engine \
    no-dso \
    no-tests \
    -DNO_FORK

emmake make -j "${CVC_JOBS}"
emmake make install_sw

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
