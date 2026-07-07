#!/usr/bin/env bash
# recipes/x264/build.sh — build x264 H.264 encoder library from source.
#
# The standalone encoder CLI is omitted (--disable-cli).
# Assembly optimisations are enabled automatically when nasm is on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-cli
    --disable-lavf
    --disable-swscale
    --disable-opencl
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--enable-static --disable-shared)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
