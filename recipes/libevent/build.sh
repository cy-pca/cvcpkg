#!/usr/bin/env bash
# recipes/libevent/build.sh — build libevent 2.1 via autotools.
#
# The release tarball carries a generated configure, so the build needs only a
# C compiler and make (no cmake).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    # No TLS: bufferevent_openssl would make openssl a runtime dep, and tmux
    # (the consumer this was added for) does not use it.
    --disable-openssl
    --disable-samples
    --disable-libevent-regress
    --disable-doxygen-doc
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# libtool archives keep this build's install dir; nothing needs them.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete
# The evrpc code generator (a Python script); see the recipe's notes.
rm -f "${CVC_INSTALL_DIR}/bin/event_rpcgen.py"
rmdir "${CVC_INSTALL_DIR}/bin" 2>/dev/null || true

# Relocate installed .pc files (recipes/_common/rewrite-install-paths.sh).
. "$(cd "$(dirname "$0")" && pwd)/../_common/rewrite-install-paths.sh"
cvc_rewrite_install_paths
