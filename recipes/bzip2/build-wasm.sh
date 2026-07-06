#!/usr/bin/env bash
# recipes/bzip2/build-wasm.sh — cross-compile bzip2 to wasm via Emscripten.
# Static library only (wasm has no shared library concept).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

export CFLAGS="${CFLAGS:-} -D_FILE_OFFSET_BITS=64"

# Static lib + CLI tools via upstream Makefile — Emscripten will emit
# wasm-native binaries for bzip2/bunzip2/bzip2recover.
make -j "${CVC_JOBS}" libbz2.a bzip2 bzip2recover \
    CC=emcc AR=emar RANLIB=emranlib

mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/lib/pkgconfig"
cp libbz2.a "${CVC_INSTALL_DIR}/lib/"
cp bzlib.h  "${CVC_INSTALL_DIR}/include/"

cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/bzip2.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: bzip2
Description: Burrows-Wheeler compression library (wasm build)
URL: https://sourceware.org/bzip2/
Version: 1.0.8
Libs: -L\${libdir} -lbz2
Cflags: -I\${includedir}
PC

cvc_rewrite_install_paths
