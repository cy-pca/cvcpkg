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

# include/libmp3lame.sym exports lame_init_old, which 3.100 compiles as static
# (DEPRECATED_OR_OBSOLETE_CODE_REMOVED), so the symbol does not exist.  GNU ld
# lets that pass; Apple's linker does not ("Undefined symbols for architecture
# arm64: _lame_init_old"), and every macOS build failed.  Drop it from the list.
sed -i.orig '/^lame_init_old$/d' include/libmp3lame.sym
rm -f include/libmp3lame.sym.orig

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
