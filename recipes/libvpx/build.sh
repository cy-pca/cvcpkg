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

# Use gmake on BSDs — their system make(1) is BSD make, not GNU make.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        command -v gmake >/dev/null 2>&1 && MAKE=gmake
        ;;
esac

"${CVC_SOURCE_DIR}/configure" "${CONFIGURE_ARGS[@]}"
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

cvc_rewrite_install_paths
