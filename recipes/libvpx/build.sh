#!/usr/bin/env bash
# recipes/libvpx/build.sh — build VP8/VP9 codec library from source.
#
# libvpx uses a custom configure that wraps cmake/make.  It must be
# run from a separate build directory.  Assembly optimisations are
# enabled automatically when nasm is on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-examples
    --disable-tools
    --disable-docs
    --disable-unit-tests
    --enable-vp8
    --enable-vp9
    --enable-vp9-highbitdepth
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# macOS: name the target.  libvpx 1.15 derives it from `gcc -dumpmachine`, but
# its table only knows darwin20-23; the macOS 15/26 runners report darwin24/25,
# so it fell back to generic-gnu and linked with ELF flags ("ld: unknown
# options: -z -soname --version-script").  The darwin20-23 targets differ only
# in adding -arch; the deployment target comes from MACOSX_DEPLOYMENT_TARGET.
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

# libvpx.so links with --no-undefined but configure adds -lpthread only for
# linux targets: FreeBSD failed with "undefined symbol: pthread_create".
# OpenBSD's compiler driver links neither libc nor libpthread into a shared
# library, so there every libc and pthread symbol was undefined (memset,
# fopen, pthread_cond_wait, ...): name both.
case "$(uname -s)" in
    OpenBSD) export LDFLAGS="${LDFLAGS:+${LDFLAGS} }-pthread -lpthread -lc" ;;
    FreeBSD|NetBSD|DragonFly) export LDFLAGS="${LDFLAGS:+${LDFLAGS} }-pthread" ;;
esac
"${CVC_SOURCE_DIR}/configure" "${CONFIGURE_ARGS[@]}"
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

cvc_rewrite_install_paths
