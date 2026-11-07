#!/usr/bin/env bash
# recipes/mpfr/build.sh — build MPFR from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# FreeBSD/OpenBSD: link the declared gmp, not the ports one.  --with-gmp
# APPENDS -I/-L${CVC_DEPS_PREFIX} to CPPFLAGS/LDFLAGS, behind the
# -L/usr/local/lib env-<bsd>.sh added, so +cvc.7's OpenBSD libmpfr.so.8.1
# NEEDed the builder's ports libgmp.so.11.1 (and compiled against cvcpkg's
# gmp.h, which comes first through CPPFLAGS).  MPFR needs nothing else from
# /usr/local, so drop those dirs, as recipes/mesa does.
case "${CVC_PLATFORM}" in
    freebsd|openbsd)
        _drop_host_dirs() {
            local _w _out=""
            for _w in $1; do
                case "${_w}" in
                    -I/usr/local/include|-L/usr/local/lib) ;;
                    *) _out="${_out:+${_out} }${_w}" ;;
                esac
            done
            printf '%s' "${_out}"
        }
        # Still exported: env-<bsd>.sh exported them.
        CFLAGS="$(_drop_host_dirs "${CFLAGS:-}")"
        CXXFLAGS="$(_drop_host_dirs "${CXXFLAGS:-}")"
        LDFLAGS="$(_drop_host_dirs "${LDFLAGS:-}")"
        ;;
esac

shared_flags=()
if [[ "${CVC_LINK}" == "static" ]]; then
    shared_flags+=(--disable-shared --enable-static)
else
    shared_flags+=(--enable-shared --enable-static)
fi

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --with-gmp="${CVC_DEPS_PREFIX}" \
    ${shared_flags[@]+"${shared_flags[@]}"}

make -j "${CVC_JOBS}"
make install

# Same autotools/libtool relocatability handling as gmp: normalize .pc/.cmake
# text paths, then rewrite the baked-in temp $CVC_INSTALL_DIR dylib ids/deps
# (libmpfr, plus its dependency on libgmp) to @rpath for macOS.
cvc_rewrite_install_paths
cvc_relocate_macos_dylibs
