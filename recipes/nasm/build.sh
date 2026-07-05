#!/usr/bin/env bash
# recipes/nasm/build.sh — build the NASM assembler from source.
#
# NASM is a pure host tool (assembler); it produces no libraries.  We
# build it with its own autotools configure and install just the
# nasm/ndisasm executables into the prefix so downstream recipes
# (FFmpeg) can find it on PATH via $CVC_DEPS_PREFIX/bin.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# NASM's Makefile uses GNU-make syntax (ifeq/endif), which the BSD
# make(1) cannot parse — use gmake there.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        ;;
esac

./configure --prefix="${CVC_INSTALL_DIR}"

# `install` copies the nasm/ndisasm executables plus the pre-built man
# pages that ship in the release tarball (no doc toolchain required).
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
