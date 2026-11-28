#!/usr/bin/env bash
# recipes/bzip2/build-wasi.sh — cross-compile bzip2 to wasm32-wasi via wasi-sdk.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

WASI_FLAGS="--target=wasm32-wasip1 --sysroot=${CVC_WASI_SDK_DIR}/share/wasi-sysroot"
export CFLAGS="${WASI_FLAGS} ${CFLAGS:-} -D_FILE_OFFSET_BITS=64"

# The library only, as on wasm: the bzip2 and bzip2recover tools need
# signal(), fchown() and times(), which wasip1 does not have, and the package
# ships no bin/ on the cross targets anyway.
make -j "${CVC_JOBS}" libbz2.a \
    CC="${CC}" AR="${AR}" RANLIB="${RANLIB}"

mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/lib/pkgconfig"
cp libbz2.a "${CVC_INSTALL_DIR}/lib/"
cp bzlib.h  "${CVC_INSTALL_DIR}/include/"

cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/bzip2.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: bzip2
Description: Burrows-Wheeler compression library (wasi build)
URL: https://sourceware.org/bzip2/
Version: 1.0.8
Libs: -L\${libdir} -lbz2
Cflags: -I\${includedir}
PC

cvc_rewrite_install_paths
