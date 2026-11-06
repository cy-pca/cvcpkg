#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Boost's CMake build (>= 1.82) supports standard CMake workflow.
#
# Optional backends are pinned instead of left to find_package(): each one
# defaults ON when the build host happens to have the library, which made the
# bundle depend on whatever that host had installed rather than on what this
# recipe declares. 1.86.0+cvc.4 shipped libboost_locale NEEDing the linux
# builder's ICU 70 (libicui18n.so.70 / libicuuc.so.70) and, on macOS,
# libboost_iostreams loading /opt/homebrew/opt/zstd/lib/libzstd.1.dylib plus the
# bzip2/xz dylibs pack-all had left in its shared prefix. cvcpkg has no ICU
# recipe, so Boost.Locale uses its iconv/POSIX/std backends; Boost.Iostreams
# keeps only zlib, the runtime dependency the recipe declares. That matches what
# every linux bundle already provided, on every platform.
#
# Boost.Charconv's __float128 support is decided here, not by its probe (which
# turns it on whenever the build host has GCC's libquadmath). On linux it is ON
# and links the libquadmath of cvcpkg's gcc-runtime, a runtime dependency, so a
# host without one still loads libboost_charconv (1.86.0+cvc.5 NEEDed the
# builder's; +cvc.6 went without). Elsewhere OFF: clang (macOS, the BSDs) has no
# quadmath.
# OpenBSD: no Boost.Process. Its v2 library needs <wordexp.h> (shell.cpp), which
# OpenBSD does not have, and reads kinfo_proc fields under FreeBSD's names
# (pid.cpp: kp_pid); upstream has never built it there. Every other library
# builds. (bsd-process-backports.patch still carries the cmd/exe/close_handles/
# environ fixes NetBSD needs.)
_boost_extra=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    _boost_extra+=(-DBOOST_EXCLUDE_LIBRARIES=process)
fi
# GCC builds libquadmath (and <quadmath.h>) only for x86, so linux/arm64 is OFF
# too: forcing it ON there failed ("quadmath.h: No such file or directory"),
# and gcc-runtime ships lib/libquadmath* on x86_64 only.
if [[ "${CVC_PLATFORM}" == "linux" ]] && [[ "$(uname -m)" == x86_64 ]]; then
    # -L first so -lquadmath binds the deps prefix's copy, not the compiler's.
    _boost_extra+=(
        -DBOOST_CHARCONV_QUADMATH_FOUND=ON
        "-DCMAKE_SHARED_LINKER_FLAGS=-L${CVC_DEPS_PREFIX}/lib"
    )
else
    _boost_extra+=(-DBOOST_CHARCONV_QUADMATH_FOUND=OFF)
fi
# Boost.Locale's iconv backend (its charset conversion, with ICU off) on OpenBSD
# and macOS links cvcpkg's GNU libiconv, the declared iconv dependency, named
# here so FindIconv cannot pick another.  +cvc.8 linked whatever it found:
# OpenBSD's libboost_locale NEEDed the build host's ports libiconv.so.7.1 from
# /usr/local, macOS's loaded @rpath/libiconv.2.dylib from a shared prefix, and
# neither declared it.  Linux and the other BSDs keep libc's iconv.
case "${CVC_PLATFORM}" in
    openbsd|macos)
        _iconv_lib=""
        for _f in "${CVC_DEPS_PREFIX}"/lib/libiconv.dylib "${CVC_DEPS_PREFIX}"/lib/libiconv.so.[0-9]*; do
            if [[ -f "${_f}" ]]; then
                _iconv_lib="${_f}"
                break
            fi
        done
        if [[ -z "${_iconv_lib}" || ! -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]]; then
            echo "cvcpkg: boost: the declared iconv is missing from ${CVC_DEPS_PREFIX}" >&2
            exit 1
        fi
        _boost_extra+=(
            -DBOOST_LOCALE_ENABLE_ICONV=ON
            -DIconv_IS_BUILT_IN=FALSE
            "-DIconv_INCLUDE_DIR=${CVC_DEPS_PREFIX}/include"
            "-DIconv_LIBRARY=${_iconv_lib}"
        )
        ;;
esac

cvc_cmake_build \
    ${_boost_extra[@]+"${_boost_extra[@]}"} \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DBOOST_LOCALE_ENABLE_ICU=OFF \
    -DBOOST_IOSTREAMS_ENABLE_ZLIB=ON \
    -DBOOST_IOSTREAMS_ENABLE_BZIP2=OFF \
    -DBOOST_IOSTREAMS_ENABLE_LZMA=OFF \
    -DBOOST_IOSTREAMS_ENABLE_ZSTD=OFF

# A libiconv the libraries link must be the declared one: the deps prefix's on
# OpenBSD/macOS, and none elsewhere (libc's iconv).
_iconv_linked="$(grep -a -h -o -E 'libiconv\.(so\.[0-9.]+|[0-9]+\.dylib)' \
    "${CVC_INSTALL_DIR}"/lib/libboost_* 2>/dev/null | sort -u || true)"
for _lib in ${_iconv_linked}; do
    case "${CVC_PLATFORM}" in
        openbsd|macos)
            if [[ -e "${CVC_DEPS_PREFIX}/lib/${_lib}" ]]; then
                echo "cvcpkg: Boost links ${_lib} from the declared iconv"
                continue
            fi
            ;;
    esac
    echo "cvcpkg: Boost linked ${_lib}, which is not the declared iconv" >&2
    exit 1
done
