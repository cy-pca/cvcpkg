#!/usr/bin/env bash
# recipes/swig/build.sh — build SWIG from source on Linux and macOS.
#
# SWIG 4.4.1 supports JavaScript/Emscripten target for WASM output.
# Uses PCRE2 from the cvcpkg prefix (built as a separate recipe).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Put the cvcpkg prefix bin on PATH so bison from our recipe is found.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

# Bison's compiled-in PKGDATADIR points to the original build host.
# Override it so bison finds its m4sugar data in the deps prefix.
export BISON_PKGDATADIR="${CVC_DEPS_PREFIX}/share/bison"

# Fix Perl module paths for cvcpkg-built automake on BSDs.
# The aclocal script has hardcoded Perl paths from the build host; PERL5LIB
# lets Perl find the Automake modules at their actual installed location.
_am_share=$(find "${CVC_DEPS_PREFIX}/share" -maxdepth 1 -name 'automake-*' -type d 2>/dev/null | head -1)
if [[ -n "${_am_share}" ]]; then
    export PERL5LIB="${_am_share}${PERL5LIB:+:$PERL5LIB}"
fi

cd "${CVC_SOURCE_DIR}"

# SWIG uses autotools; run autogen if configure doesn't exist.
if [[ ! -f configure ]]; then
    ./autogen.sh
fi

# Use PCRE2 from the cvcpkg deps prefix.
PCRE2_CONFIG="${CVC_DEPS_PREFIX}/bin/pcre2-config"
if [[ ! -x "${PCRE2_CONFIG}" ]]; then
    echo "ERROR: pcre2-config not found at ${PCRE2_CONFIG}" >&2
    echo "       Build the pcre2 recipe first: cvcpkg build pcre2" >&2
    exit 1
fi

# The BSDs: link the declared pcre2.  configure appends pcre2-config's
# -L${CVC_DEPS_PREFIX}/lib -lpcre2-8 to LIBS, behind the -L/usr/local/lib (or
# NetBSD's -L/usr/pkg/lib) that env-<bsd>.sh put in LDFLAGS, so +cvc.5's
# OpenBSD bin/swig-bin NEEDed the builder's ports libpcre2-8.so.0.7, and the
# first NetBSD build compiled against pkgsrc's pcre2 10.47 and then failed to
# run on the declared one ("version PCRE2_10.47 ... not defined", dev job
# 24660).  SWIG needs nothing else from there (bison and the autotools come
# from the prefix), so drop those dirs, as recipes/mesa does.
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd)
        _drop_host_dirs() {
            local _w _out=""
            for _w in $1; do
                case "${_w}" in
                    -I/usr/local/include|-L/usr/local/lib) ;;
                    -I/usr/pkg/include|-L/usr/pkg/lib|-Wl,-R/usr/pkg/lib) ;;
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
# OpenBSD's linker takes -lpcre2-8 only as libpcre2-8.so or
# libpcre2-8.so.MAJOR.MINOR, and a pcre2 bundle built before env-openbsd.sh's
# cvc_openbsd_lib_names ships just CMake's libpcre2-8.so.0.13.0 and .so.0, so
# -lpcre2-8 could not find the prefix's library even ahead of /usr/local.  Give
# the link a libpcre2-8.so.X.Y name for it; the result NEEDs its SONAME,
# libpcre2-8.so.0, which pcre2 ships.
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    _libnames="${CVC_BUILD_DIR}/cvc-libnames"
    mkdir -p "${_libnames}"
    for _f in "${CVC_DEPS_PREFIX}"/lib/libpcre2-8.so.[0-9]*.[0-9]*.[0-9]*; do
        [[ -f "${_f}" ]] || continue
        _base="${_f##*/}"
        ln -sf "${_f}" "${_libnames}/${_base%.*}"
    done
    LDFLAGS="-L${_libnames}${LDFLAGS:+ ${LDFLAGS}}"
fi

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --with-pcre2-prefix="${CVC_DEPS_PREFIX}"

# SWIG's Makefile uses GNU make features (ifeq, filter, ...).  Use gmake
# on the BSDs where /usr/bin/make is BSD make.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then MAKE=gmake; fi
        ;;
esac

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

# The pcre2 swig links must be the declared one (see above).
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd)
        _pcre_libs="$(grep -a -h -o -E 'libpcre2-8\.so(\.[0-9]+)*' \
            "${CVC_INSTALL_DIR}/bin/swig" | sort -u || true)"
        for _lib in ${_pcre_libs}; do
            if [[ ! -e "${CVC_DEPS_PREFIX}/lib/${_lib}" ]]; then
                echo "swig: bin/swig links ${_lib}, which the declared pcre2 does not ship" >&2
                exit 1
            fi
            echo "swig: bin/swig links ${_lib} from the declared pcre2"
        done
        ;;
esac

# Generate a CMake config file so find_package(SWIG) works inside bundles.
SWIG_VER=$("${CVC_INSTALL_DIR}/bin/swig" -version | sed -n 's/.*SWIG Version \([0-9.]*\).*/\1/p')

# ── make swig relocatable ────────────────────────────────────────────────────
# autotools compiles the configure-time --prefix into the binary as its swiglib
# path, and that prefix is the BUILDER's temp dir. In an installed bundle
# `swig -swiglib` therefore reports something like
#   /tmp/cvcpkg-builder/cvcpkg-swig-xxxx/install/share/swig/4.4.1
# which no longer exists, so every invocation dies with
#   :EOF: Error: Unable to find 'swig.swg'
# and CMake's MODULE-mode FindSWIG — which derives SWIG_DIR by running
# `swig -swiglib` — fails with "Could NOT find SWIG (missing: SWIG_DIR)".
# build.ps1 already solves this for Windows by staging the library at the
# <exedir>\Lib path swigwin compiles in; this is the Unix half.
#
# SWIG honours the SWIG_LIB environment variable in preference to the compiled-in
# path, and `-swiglib` reports the honoured value, so a wrapper that sets it
# relative to its own location fixes direct invocation and module-mode FindSWIG
# alike. A caller's own SWIG_LIB still wins.
mv "${CVC_INSTALL_DIR}/bin/swig" "${CVC_INSTALL_DIR}/bin/swig-bin"
cat > "${CVC_INSTALL_DIR}/bin/swig" <<'WRAPPER'
#!/bin/sh
# cvcpkg wrapper: point SWIG at the runtime library inside THIS prefix, since
# the binary's compiled-in swiglib path is the builder's temp dir. Generated by
# the cvcpkg swig recipe (recipes/swig/build.sh).
self=$0
# Resolve symlinks without readlink -f, which BSD/macOS do not all provide.
while [ -L "$self" ]; do
    link=$(ls -ld -- "$self" | sed 's/^.*-> //')
    case $link in
        /*) self=$link ;;
        *)  self=$(dirname -- "$self")/$link ;;
    esac
done
bindir=$(CDPATH= cd -- "$(dirname -- "$self")" && pwd -P)
prefix=$(CDPATH= cd -- "$bindir/.." && pwd -P)
if [ -z "${SWIG_LIB:-}" ]; then
    for d in "$prefix"/share/swig/*/; do
        [ -f "$d/swig.swg" ] || continue
        SWIG_LIB=${d%/}
        export SWIG_LIB
        break
    done
fi
exec "$bindir/swig-bin" "$@"
WRAPPER
chmod 0755 "${CVC_INSTALL_DIR}/bin/swig"

# Prove it: an installed-layout invocation must resolve swig.swg without the
# caller setting anything. Catches a wrong relative path or a missed staging
# step at BUILD time rather than in a consumer's CI.
( unset SWIG_LIB
  reported=$("${CVC_INSTALL_DIR}/bin/swig" -swiglib)
  case "${reported}" in
      "${CVC_INSTALL_DIR}"/*) : ;;
      *) echo "ERROR: swig wrapper resolved swiglib to '${reported}', outside ${CVC_INSTALL_DIR}" >&2
         exit 1 ;;
  esac
  [ -f "${reported}/swig.swg" ] || {
      echo "ERROR: swig.swg not present at '${reported}'" >&2; exit 1; }
  printf 'swig wrapper OK: -swiglib -> %s\n' "${reported}" )
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/SWIG"
cat > "${CVC_INSTALL_DIR}/lib/cmake/SWIG/SWIGConfig.cmake" <<EOF
# Generated by cvcpkg swig recipe.
set(SWIG_EXECUTABLE "\${CMAKE_CURRENT_LIST_DIR}/../../../bin/swig")
set(SWIG_DIR "\${CMAKE_CURRENT_LIST_DIR}/../../../share/swig/${SWIG_VER}")
set(SWIG_VERSION "${SWIG_VER}")
set(SWIG_FOUND TRUE)
EOF
cat > "${CVC_INSTALL_DIR}/lib/cmake/SWIG/SWIGConfigVersion.cmake" <<EOF
set(PACKAGE_VERSION "${SWIG_VER}")
if("\${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("\${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
EOF
