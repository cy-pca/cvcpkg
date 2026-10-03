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

# Haiku: pre-generate the warning tables so make never enters the recursive
# warnings rule.
#
# NASM's `asm/warnings.time` rule (Makefile.in) touches its own sentinel and
# then runs `$(MAKE) $(WARNTIMES)` to regenerate asm/warnings_c.h,
# include/warnings.h and doc/warnings.src via asm/warnings.pl.  Those targets
# depend back on asm/warnings.time, so the pass only terminates because the
# freshly-touched sentinel tests strictly-newer than its sources.  On Haiku's
# BFS the mtime granularity is too coarse for that to hold, so the sentinel
# never wins the comparison and the `$(MAKE) $(WARNTIMES)` line recurses until
# make aborts (`make[28]: *** [asm/warnings.time] Error 2`).
#
# Generate the three files directly with the same perl invocations the rule
# uses, then stamp every generated file and sentinel to one instant that is
# newer than the 2024 tarball sources.  make then sees the whole warnings
# sub-tree up to date and skips the recursive rule entirely.
if [ "$(uname -s)" = "Haiku" ]; then
    perl asm/warnings.pl c   asm/warnings_c.h   .
    perl asm/warnings.pl h   include/warnings.h .
    perl asm/warnings.pl doc doc/warnings.src   .
    touch asm/warnings_c.h include/warnings.h doc/warnings.src \
          asm/warnings_c.h.time include/warnings.h.time doc/warnings.src.time \
          asm/warnings.time
fi

# `install` copies the nasm/ndisasm executables plus the pre-built man
# pages that ship in the release tarball (no doc toolchain required).
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
