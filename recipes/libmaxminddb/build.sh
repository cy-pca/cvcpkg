#!/usr/bin/env bash
# recipes/libmaxminddb/build.sh — build libmaxminddb from source (Linux, macOS, BSDs).
#
# Upstream's BUILD_SHARED_LIBS defaults OFF; cvc_cmake_build passes the cvcpkg
# link mode through it, so the default `shared` link produces libmaxminddb.so.
# BUILD_TESTING is off: the test suite needs the libtap git submodule, which the
# release tarball does not carry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DMAXMINDDB_BUILD_BINARIES=ON \
    -DMAXMINDDB_INSTALL=ON \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_MANDIR=share/man  # GNUInstallDirs says man/ on OpenBSD
