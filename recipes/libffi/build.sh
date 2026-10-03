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

# No RPATH: libffi links nothing but libc. (An `-Wl,-rpath,\$ORIGIN` LDFLAGS
# used to sit here; make expanded its `$O`, so it only ever embedded the
# CWD-relative RPATH "RIGIN".)

./configure "${CONFIGURE_ARGS[@]}"
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

# OpenBSD: libffi's libtool links libffi.so.12.1 with no DT_SONAME. A consumer
# that names the library by full path on its link line -- meson does this for
# every pkg-config dependency -- then records that whole path as DT_NEEDED, and
# for a cvcpkg build that path is the job's deleted deps prefix (wayland
# +cvc.3: NEEDED ".../cvcpkg-job-wayland-.../lib/libffi.so.12.1"). Stamp the
# file's own name as its SONAME, which OpenBSD's ld.so resolves through its
# usual libffi.so.<major>.<minor> search. Post-install with patchelf, like
# curl's OpenBSD SONAME: libtool's link step mangles a -soname passed in
# LDFLAGS. OpenBSD's lld lays segments out at 4 KiB, where patchelf's added
# segment loads fine (unlike NetBSD -- see recipes/curl).
if [ "${CVC_PLATFORM}" = "openbsd" ] && [ "${CVC_LINK:-shared}" != "static" ]; then
    _cvc_libffi_so=$(find "${CVC_INSTALL_DIR}/lib" -maxdepth 1 -name 'libffi.so.*' -type f | head -1)
    if [ -z "${_cvc_libffi_so}" ]; then
        echo "libffi: no libffi.so.* file was installed" >&2
        exit 1
    fi
    patchelf --set-soname "$(basename "${_cvc_libffi_so}")" "${_cvc_libffi_so}"
fi

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
