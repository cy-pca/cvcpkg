#!/usr/bin/env bash
# recipes/vpx-tools/build.sh — build vpxenc and vpxdec VP8/VP9 tools.
#
# Built from the same source tarball as the libvpx recipe, against the
# in-tree STATIC libvpx, so the two binaries are self-contained.  Only vpxenc
# and vpxdec are installed.
#
# This used to configure with both --disable-shared and --disable-static and
# link the separately packaged libvpx through --extra-ldflags, an option
# libvpx's configure does not have ("Unknown option --extra-ldflags=..."), so
# vpx-tools had never built on any platform.  libvpx's build only links its
# tools against the library it builds itself.  vpxenc and vpxdec are declared
# in examples.mk ("UTILS ... meant for distribution"), so --enable-examples is
# what builds them; the top-level Makefile hands each goal to every sub-target
# in turn, so build the library (target=libs) and then just the two binaries
# (target=examples).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --enable-examples
    --disable-docs
    --disable-unit-tests
    --enable-vp8
    --enable-vp9
    --enable-static
    --disable-shared
)

# macOS: name the target, as recipes/libvpx/build.sh does.  libvpx 1.15's
# target table only matches darwin20-23 and the runners report darwin24/25,
# which falls back to generic-gnu and ELF link flags.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    CONFIGURE_ARGS+=(--target="$(uname -m)-darwin23-gcc")
fi

# Use gmake on BSDs — their system make(1) is BSD make, not GNU make.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        command -v gmake >/dev/null 2>&1 && MAKE=gmake
        ;;
esac

# vpxenc/vpxdec use pthreads, but configure adds -lpthread only for linux
# targets: FreeBSD failed with "undefined symbol: pthread_create" (populate
# 2026-10-19).  NetBSD's driver already links it.
case "$(uname -s)" in
    FreeBSD|OpenBSD|DragonFly) export LDFLAGS="${LDFLAGS:+${LDFLAGS} }-pthread" ;;
esac
"${CVC_SOURCE_DIR}/configure" "${CONFIGURE_ARGS[@]}"
"${MAKE}" -j "${CVC_JOBS}" target=libs
"${MAKE}" -j "${CVC_JOBS}" target=examples vpxenc vpxdec

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 vpxenc "${CVC_INSTALL_DIR}/bin/vpxenc"
install -m 755 vpxdec "${CVC_INSTALL_DIR}/bin/vpxdec"
