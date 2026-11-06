#!/usr/bin/env bash
# recipes/idn2/build.sh — build GNU libidn2 via autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --disable-doc
    --disable-gtk-doc
    --disable-nls
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Point autoconf at the cvcpkg-provided libunistring / libiconv when
# present so libidn2 links against our bundles rather than any system
# copy.  --with-libunistring-prefix is a Gnulib idiom that libidn2's
# configure recognises.
if [[ -d "${CVC_DEPS_PREFIX:-}/include" ]]; then
    CONFIGURE_ARGS+=(--with-libunistring-prefix="${CVC_DEPS_PREFIX}")
    case "$(uname -s)" in
        Darwin|FreeBSD|NetBSD|OpenBSD)
            CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
            ;;
    esac
fi

# Ensure pkg-config and the linker find bundle-supplied deps.
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
case "${CVC_PLATFORM:-}" in
    freebsd|netbsd|openbsd)
        # The build tree's lib/.libs first, then the deps prefix.  libtool
        # links bin/idn2 against ../lib/libidn2.la as -L<that dir> -lidn2, and
        # an -L/usr/local/lib got ahead of it: cvc.4's OpenBSD bin/idn2 NEEDed
        # the builder's ports libidn2.so.1.1 (RUNPATH /usr/local/lib), not the
        # bundle's libidn2.so.4.0.  This script sources no env-<platform>.sh,
        # so libtool added that -L itself, most likely on finding a ports .la
        # for -lunistring or -liconv: the deps prefix has those only as
        # libfoo.so.X.Y, which libtool's search skips.  LDFLAGS' -L dirs lead
        # the link line, so these win.  Checked after `make install`.
        export CPPFLAGS="-I${CVC_DEPS_PREFIX:-/nonexistent}/include${CPPFLAGS:+ ${CPPFLAGS}}"
        export LDFLAGS="-L${CVC_SOURCE_DIR}/lib/.libs -L${CVC_DEPS_PREFIX:-/nonexistent}/lib${LDFLAGS:+ ${LDFLAGS}}"
        ;;
    *)
        export CPPFLAGS="${CPPFLAGS:-} -I${CVC_DEPS_PREFIX:-/nonexistent}/include"
        export LDFLAGS="${LDFLAGS:-} -L${CVC_DEPS_PREFIX:-/nonexistent}/lib"
        ;;
esac

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# bin/idn2 must load the libidn2 this build installs, not a host copy (see the
# LDFLAGS comment above).
case "${CVC_PLATFORM:-}" in
    freebsd|netbsd|openbsd)
        if [[ -f "${CVC_INSTALL_DIR}/bin/idn2" ]]; then
            _idn2_libs="$(grep -a -h -o -E 'libidn2\.so(\.[0-9]+)*' \
                "${CVC_INSTALL_DIR}/bin/idn2" | sort -u || true)"
            for _lib in ${_idn2_libs}; do
                if [[ ! -e "${CVC_INSTALL_DIR}/lib/${_lib}" ]]; then
                    echo "idn2: bin/idn2 links ${_lib}, which this build did not install" >&2
                    exit 1
                fi
                echo "idn2: bin/idn2 links its own ${_lib}"
            done
        fi
        ;;
esac

# Relocate installed .pc/.cmake files and bin/*-config scripts.  This script
# does not source env-*.sh, so the old `command -v` guard was always false
# and the rewrite never ran; source the helper directly.
. "$(cd "$(dirname "$0")" && pwd)/../_common/rewrite-install-paths.sh"
cvc_rewrite_install_paths
