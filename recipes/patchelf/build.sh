#!/usr/bin/env bash
# recipes/patchelf/build.sh — build patchelf from source (autotools).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# OpenBSD's automake depfiles bootstrap fails during config.status
# ("Something went wrong bootstrapping makefile fragments").  Dependency
# tracking only speeds incremental rebuilds, which a one-shot package build
# doesn't need — disable it.
./configure --prefix="${CVC_INSTALL_DIR}" --disable-dependency-tracking

# Use gmake on BSDs (the generated Makefile uses GNU-make syntax).
MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

$MAKE -j "${CVC_JOBS}"
$MAKE install
