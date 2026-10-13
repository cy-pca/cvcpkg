#!/usr/bin/env bash
# recipes/bison/build.sh — build GNU Bison from source on Linux, macOS, BSD.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Bison is an autotools project, not CMake — source the common env
# only for the variables, not for cvc_cmake_build.
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# BSD make can't parse Bison's Makefile; require GNU make there.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        ;;
esac

cd "${CVC_SOURCE_DIR}"

# --enable-relocatable: find the m4 skeletons (share/bison) relative to the
# installed binary instead of this build's temporary prefix, which does not
# exist where the bundle is unpacked ("m4sugar.m4: cannot open").
# M4=m4: run whatever m4 is on PATH, not the absolute build-time path of the
# m4 host tool ("m4 subprocess failed").  The m4 runtime dep puts one there.
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-relocatable \
    --disable-nls \
    M4=m4

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
