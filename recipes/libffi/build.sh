#!/usr/bin/env bash
# recipes/libffi/build.sh — build libffi from source using autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# OpenBSD's base clang rejects the GCC-ism '-print-multi-os-directory' that
# libffi's libtool multilib probe emits, aborting configure.  Build the C
# library with the gcc package's egcc there instead — libffi is pure C and
# ABI-compatible with the clang-built catalog.  Two OpenBSD-only wrinkles,
# both verified on openbsd-build (OpenBSD 7.7, gcc-11.2.0p15):
#
#   * NO C++ compiler is needed, and eg++ (the separate g++ ports package) is
#     NOT installed — the gcc package ships egcc only.  Setting CXX=eg++ just
#     makes configure's OPTIONAL C++ probe fail with "eg++: not found" (noise,
#     not fatal).  Leave CXX to autoconf, which then disables C++ cleanly.
#   * configure's automatic-dependency-tracking bootstrap runs $MAKE, and
#     OpenBSD's /usr/bin/make (BSD make) fails it — "Something went wrong
#     bootstrapping makefile fragments ... consider re-running configure with
#     MAKE=gmake".  THIS is the actual fatal error.  Use GNU make throughout.
MAKE="${MAKE:-make}"
if [ "${CVC_PLATFORM}" = "openbsd" ] && command -v egcc >/dev/null 2>&1; then
    export CC=egcc
    MAKE=gmake
    export MAKE
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
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
