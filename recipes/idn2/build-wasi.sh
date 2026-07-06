#!/usr/bin/env bash
# recipes/idn2/build-wasi.sh — cross-compile GNU libidn2 to wasm32-wasi.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${CVC_WASI_SDK_DIR}/share/wasi-sysroot"
export CFLAGS="${WASI_TARGET_FLAGS} ${CFLAGS:-}"
export CXXFLAGS="${WASI_TARGET_FLAGS} ${CXXFLAGS:-}"
export LDFLAGS="${WASI_TARGET_FLAGS} ${LDFLAGS:-} -L${CVC_DEPS_PREFIX:-/nonexistent}/lib"
export CPPFLAGS="${CPPFLAGS:-} -I${CVC_DEPS_PREFIX:-/nonexistent}/include"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --host=wasm32-wasi
    --build="${BUILD_TRIPLET}"
    --disable-shared
    --enable-static
    --disable-dependency-tracking
    --disable-doc
    --disable-gtk-doc
    --disable-nls
)
if [[ -d "${CVC_DEPS_PREFIX:-}/include" ]]; then
    CONFIGURE_ARGS+=(--with-libunistring-prefix="${CVC_DEPS_PREFIX}")
    if [[ -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]]; then
        CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
    fi
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
