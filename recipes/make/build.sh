#!/usr/bin/env bash
# recipes/make/build.sh — build GNU Make from source.
#
# GNU Make 4.4.1 configures and builds with an existing make (the host
# bootstrap toolchain), the same way the other autotools host-tool
# recipes (m4, autoconf, ...) do. The resulting bin/make is what
# downstream recipes then use once CVC_INSTALL_DIR/bin is on PATH.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Haiku ships no <ar.h>, so src/arscan.c fails to compile ("ar.h: No such
# file or directory"). NO_ARCHIVES compiles arscan.c (and its callers) as
# stubs, dropping support for updating .a archive members — a capability a
# build-tool make never exercises here. Must be defined for every TU, so it
# goes through CFLAGS.
case "$(uname -s)" in
    Haiku)
        export CFLAGS="${CFLAGS:-} -DNO_ARCHIVES"
        ;;
esac

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-nls

make -j "${CVC_JOBS}"
make install
