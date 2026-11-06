#!/usr/bin/env bash
# recipes/mariadb-connector-c/build.sh — build MariaDB Connector/C.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Two plugins link a library this recipe does not declare, so they are off
# (build.ps1 already has remote_io off).  CMake built each whenever it found
# the library, and cvc.3's bundles on linux, macOS and the BSDs needed what it
# found: cvcpkg's libcurl for remote_io (remote URLs for LOAD DATA LOCAL INFILE
# and option files) and libzstd for zstd (protocol compression only MySQL 8
# servers speak; zlib compression is built in).  Windows never builds zstd.
cvc_cmake_build \
    -DWITH_SSL=OPENSSL \
    -DOPENSSL_ROOT_DIR="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}" \
    -DWITH_EXTERNAL_ZLIB=ON \
    -DWITH_UNIT_TESTS=OFF \
    -DCLIENT_PLUGIN_AUTH_GSSAPI_CLIENT=OFF \
    -DCLIENT_PLUGIN_REMOTE_IO=OFF \
    -DCLIENT_PLUGIN_ZSTD=OFF \
    -DINSTALL_LIBDIR=lib \
    -DINSTALL_INCLUDEDIR=include/mariadb \
    -DINSTALL_PLUGINDIR=lib/mariadb/plugin
