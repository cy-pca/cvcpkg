#!/usr/bin/env bash
# recipes/libffi/build.sh — build libffi from source using autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# OpenBSD's base clang rejects the GCC-ism '-print-multi-os-directory' that
# libffi's libtool multilib probe emits, aborting configure.  Build libffi with
# the gcc package's egcc there instead — a small pure-C library, ABI-compatible
# with the clang-built catalog.
if [ "${CVC_PLATFORM}" = "openbsd" ] && command -v egcc >/dev/null 2>&1; then
    export CC=egcc CXX=eg++
fi

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-docs
    # Install headers into the standard include/ dir (not the versioned
    # lib/libffi-x.y.z/include that libffi uses by default) so consumers'
    # pkg-config / -I flags resolve without version juggling.
    --includedir="${CVC_INSTALL_DIR}/include"
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Embed $ORIGIN RPATH so the shared lib is found next to its consumers
# regardless of the final install prefix.
export LDFLAGS="${LDFLAGS:-} -Wl,-rpath,\$ORIGIN"

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
