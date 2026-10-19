#!/usr/bin/env bash
# recipes/x264-cli/build.sh — build the x264 H.264 encoder CLI tool.
#
# Built from the same source tarball as the x264 library recipe.
# x264 (libx264) must be pre-built and available in CVC_DEPS_PREFIX.
# Only the x264 encoder binary is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

cd "${CVC_SOURCE_DIR}"

# No --sysroot: "--sysroot=/" is the default on Linux and, on macOS, points
# clang away from the SDK, so x264's compiler probe failed there with "No
# working C compiler found".  --enable-cli is the default, and x264 has no
# --disable-static/--disable-shared (configure ignored all three).
bash ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-lavf \
    --disable-swscale \
    --disable-opencl \
    --extra-cflags="${CPPFLAGS:-}" \
    --extra-ldflags="${LDFLAGS:-}"

# x264's Makefile is GNU make; the BSDs' make is not.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        command -v gmake >/dev/null 2>&1 && MAKE=gmake
        ;;
esac
"${MAKE}" -j "${CVC_JOBS}" x264

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 x264 "${CVC_INSTALL_DIR}/bin/x264"
