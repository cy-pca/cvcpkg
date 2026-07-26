#!/usr/bin/env bash
# recipes/libopus/build.sh — build libopus from source using autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --disable-extra-programs
    --disable-doc
    # Export the opus_custom_* API (custom frame sizes / non-standard sample
    # rates).  Off by default upstream, but opus_custom.h is installed either
    # way, so consumers that probe only the header (e.g. jack2's NetJack) enable
    # the code path and then fail to link without these symbols.
    --enable-custom-modes
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
