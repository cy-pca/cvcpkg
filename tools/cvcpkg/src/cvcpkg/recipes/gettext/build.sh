#!/usr/bin/env bash
# recipes/gettext/build.sh — build GNU gettext from source using autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-java
    --disable-csharp
    --without-emacs
    --without-git
    --without-bzip2
    --without-xz
)

# Respect static/shared link mode.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# On BSDs, use gmake if available.
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
else
    MAKE=make
fi
export MAKE

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install
