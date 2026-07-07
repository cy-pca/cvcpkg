#!/usr/bin/env bash
# recipes/libmp3lame/build.sh — build LAME MP3 library from source.
#
# Only the library is built; the lame encoder frontend is omitted.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --disable-frontend
    --disable-doc
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
