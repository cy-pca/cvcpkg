#!/usr/bin/env bash
# recipes/gettext/build.sh — build GNU gettext from source using autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# macOS builds the full tree too.  It used to build only gettext-runtime
# (libintl): the full build's libtextstyle failed to link on arm64
# ("undefined symbol _iconv_ostream_create") because gnulib's "working iconv"
# test fails against macOS's non-GNU system iconv, so its iconv-ostream module
# was never compiled.  The message tools (msgfmt, xgettext, msgmerge) were then
# left to the build host -- Homebrew -- and with Homebrew gone libpq and
# postgresql (-Dnls=enabled) failed on macOS: "Program 'msgfmt' not found".
# macOS now builds against cvcpkg's GNU libiconv (the iconv dependency, as on
# OpenBSD; picked up below), which passes that test.

# --with-included-libxml, --with-included-libunistring: gettext's own copies, on
# every platform.  configure otherwise links whatever libxml2 or libunistring it
# can find: linux cvc.6's libgettextsrc and cldr-plurals NEEDed libxml2.so.2,
# undeclared, from a shared populate prefix.
# --disable-openmp: msgmerge and msginit NEEDed GCC's libgomp.so.1 (linux
# cvc.6), which nothing declares.  OpenMP only parallelises the fuzzy matching
# those two do while merging.
CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-java
    --disable-csharp
    --without-emacs
    --without-git
    --without-bzip2
    --without-xz
    --with-included-libxml
    --with-included-libunistring
    --disable-openmp
)

# Respect static/shared link mode.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# OpenBSD has no iconv in libc, and macOS has a non-GNU one: use cvcpkg's GNU
# libiconv (staged into CVC_DEPS_PREFIX by the openbsd/macos iconv dep) so the
# bundled libxml finds <iconv.h> and links libiconv, and gnulib's iconv test
# passes.  Only there: elsewhere libc's iconv is used and libiconv is not
# searched for.  Keying this on an iconv.h in the deps prefix also fired in a
# shared populate prefix that held cvcpkg's iconv for another recipe, so
# linux/arm64 cvc.5 linked libiconv.so.2 without declaring it, and its msgfmt
# failed wherever iconv was not installed ("libiconv.so.2: cannot open shared
# object file", libpq's arm64 recipe check, 2026-10-30).
# Fail, not skip, when the declared copy is missing: configure would then link
# any libiconv it could find.  OpenBSD cvc.6's objects NEEDed the build host's
# ports libiconv.so.7.1 (RUNPATH /usr/local/lib), some beside cvcpkg's
# libiconv.so.8.1; the check after `make install` now refuses that.
case "${CVC_PLATFORM:-}" in
    openbsd|macos)
        if [[ -z "${CVC_DEPS_PREFIX:-}" || ! -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]]; then
            echo "cvcpkg: gettext: the declared iconv is missing from ${CVC_DEPS_PREFIX:-the deps prefix}" >&2
            exit 1
        fi
        export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
        export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
        CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
        ;;
    *)
        CONFIGURE_ARGS+=(--without-libiconv-prefix)
        ;;
esac

# OpenBSD's libtool resolves each -liconv itself, and the ports libiconv.la in
# /usr/local/lib beats the deps prefix's plain libiconv.so.8.1 even with
# -L<deps> first.  configure found the declared copy, but every libtool link
# (libintl, the programs) still took libiconv.so.7.1 with an -rpath into
# /usr/local/lib (dev job 24595, cvc.7).  So hand libtool the declared library
# by path, as lib-link would without the -liconv; make passes command-line
# variables to every sub-make, and `make install` relinks with them too.
_make_iconv=()
if [[ "${CVC_PLATFORM:-}" == openbsd ]]; then
    _iconv_so=""
    for _f in "${CVC_DEPS_PREFIX}"/lib/libiconv.so.[0-9]*.[0-9]*; do
        [[ -f "${_f}" ]] && _iconv_so="${_f}"
    done
    if [[ -z "${_iconv_so}" ]]; then
        echo "cvcpkg: gettext: no libiconv.so.X.Y in ${CVC_DEPS_PREFIX}/lib" >&2
        exit 1
    fi
    _make_iconv=(LTLIBICONV="${_iconv_so} -R${CVC_DEPS_PREFIX}/lib")
fi

# Not on linux, where glibc has gettext: the tools link gettext's own libintl,
# the one this bundle ships, rather than an external one found on the link path
# (a BSD host's ports or pkgsrc libintl), which brings that host's libiconv.
case "${CVC_PLATFORM:-}" in
    linux) ;;
    *) CONFIGURE_ARGS+=(--with-included-libintl) ;;
esac

# On BSDs, use gmake if available.
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
else
    MAKE=make
fi
export MAKE

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}" ${_make_iconv[@]+"${_make_iconv[@]}"}
$MAKE install ${_make_iconv[@]+"${_make_iconv[@]}"}

# What the installed objects link must be declared: libintl only as this
# bundle's own, a libiconv only from the deps prefix on OpenBSD/macOS (none
# elsewhere: libc's iconv), and no libxml2, libunistring or libgomp at all.
_linked="$(grep -r -a -h -o -E 'lib(intl|iconv|xml2|unistring|gomp)\.(so\.[0-9.]+|[0-9.]+\.dylib)' \
    "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/libexec" \
    2>/dev/null | sort -u || true)"
for _lib in ${_linked}; do
    if [[ "${_lib}" == libintl.* && -e "${CVC_INSTALL_DIR}/lib/${_lib}" ]]; then
        continue
    fi
    case "${CVC_PLATFORM:-}:${_lib}" in
        openbsd:libiconv.*|macos:libiconv.*)
            if [[ -e "${CVC_DEPS_PREFIX}/lib/${_lib}" ]]; then
                echo "cvcpkg: gettext links ${_lib} from the declared iconv"
                continue
            fi
            ;;
    esac
    echo "cvcpkg: gettext linked ${_lib}, which is not a declared dependency" >&2
    exit 1
done
