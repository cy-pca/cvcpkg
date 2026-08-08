#!/usr/bin/env bash
# recipes/mariadb-connector-c/build.sh — build MariaDB Connector/C.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DWITH_SSL=OPENSSL \
    -DOPENSSL_ROOT_DIR="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}" \
    -DWITH_EXTERNAL_ZLIB=ON \
    -DWITH_UNIT_TESTS=OFF \
    -DCLIENT_PLUGIN_AUTH_GSSAPI_CLIENT=OFF \
    -DINSTALL_LIBDIR=lib \
    -DINSTALL_INCLUDEDIR=include/mariadb \
    -DINSTALL_PLUGINDIR=lib/mariadb/plugin
