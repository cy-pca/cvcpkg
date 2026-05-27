#!/usr/bin/env bash
# recipes/curl/build.sh — build libcurl from source using autotools.
# Uses autotools (./configure) so cmake is NOT required — this allows
# curl to be built before cmake, breaking the circular dependency.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_DEPS_PREFIX:=}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --with-openssl
    --without-libpsl
    --without-brotli
    --without-zstd
    --without-nghttp2
    --without-libidn2
    --without-libssh2
    --disable-ldap
    --disable-manual
    --disable-dict
    --disable-gopher
    --disable-imap
    --disable-mqtt
    --disable-pop3
    --disable-rtsp
    --disable-smb
    --disable-smtp
    --disable-telnet
    --disable-tftp
)

# Point to our openssl if built as a dependency.
if [[ -n "${CVC_DEPS_PREFIX}" && -d "${CVC_DEPS_PREFIX}/include/openssl" ]]; then
    CONFIGURE_ARGS+=(--with-openssl="${CVC_DEPS_PREFIX}")
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install
