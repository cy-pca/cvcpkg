#!/usr/bin/env bash
# recipes/ffmpeg-lgpl/build.sh — build a strict LGPL-2.1 FFmpeg shared library
# set on Linux, macOS, and the BSDs (Windows is built by build.ps1).
#
# This is the LGPL counterpart to the `ffmpeg` recipe.  It deliberately does
# NOT pass --enable-gpl, --enable-version3, or --enable-openssl, so the result
# is LGPL-2.1-or-later and can be linked into an LGPL-2.1 library (libcvc /
# Ariadne) without relicensing it.  Consequences:
#   - no x264 / x265 (those are GPL — and encode-only anyway; FFmpeg's own
#     native H.264/HEVC DECODERS are LGPL and remain available);
#   - no OpenSSL (OpenSSL 3.x is Apache-2.0, incompatible with LGPLv2.1;
#     enabling it would force --enable-version3, bumping the build to v3).
# HTTPS/TLS is still available, from a license-clean backend chosen per OS:
#   - macOS  -> SecureTransport (Security.framework), the OS-native TLS.  No
#     external library, no license impact.
#   - Linux/BSD -> GnuTLS (LGPL-2.1+), since FFmpeg has no OS-native TLS there.
# (Windows uses SChannel; see build.ps1.)  Either way the https/tls protocols
# work and the build stays at v2.1.
# Kept: the LGPL/BSD external codecs (Opus, MP3, Vorbis, VP8/VP9, AV1), WebP
# images, subtitle rendering (freetype, fontconfig, fribidi), TLS as above,
# and bzip2/lzma/zlib.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Put the cvcpkg prefix bin on PATH so FFmpeg's configure finds nasm
# (built as a build dependency) and pkg-config finds external libs.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

# Point the compiler/linker at the deps prefix.  Several codecs FFmpeg links
# have no pkg-config file (e.g. libmp3lame), so FFmpeg's configure link-tests
# them with a bare `-lmp3lame` and no `-L`; without the prefix on the search
# path that probe fails with "cannot find -lmp3lame" and configure aborts.
# GCC/Clang read LIBRARY_PATH for `-l` resolution and CPATH for headers; export
# LDFLAGS too for any sub-tool that honours it.  (Matches glib/x264-cli.)
export LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
export CPATH="${CVC_DEPS_PREFIX}/include${CPATH:+:${CPATH}}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

cd "${CVC_SOURCE_DIR}"

# --enable flags common to both link modes.  Note the deliberate absence of
# --enable-gpl / --enable-version3 / --enable-openssl (see the banner): every
# codec here is LGPL- or BSD-licensed, so the build stays LGPL-2.1.
ENABLE_ARGS=(
    # External codecs (LGPL/BSD, LGPL-2.1 compatible)
    --enable-libopus
    --enable-libmp3lame
    --enable-libvorbis
    --enable-libvpx
    --enable-libdav1d
    # Image formats.  FFmpeg's PNG and (M)JPEG are native codecs — there is no
    # --enable-libpng / --enable-libjpeg switch, and passing one makes configure
    # abort with "Unknown option".  Only WebP needs an external library.
    --enable-libwebp
    # Subtitle rendering
    --enable-libfreetype
    --enable-libfribidi
    # Compression
    --enable-zlib
    --enable-bzlib
    --enable-lzma
)

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --enable-pic
    --disable-programs
    --disable-doc
    --disable-debug
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--enable-static --disable-shared)
else
    CONFIGURE_ARGS+=(--disable-static --enable-shared)
fi
CONFIGURE_ARGS+=("${ENABLE_ARGS[@]}")

# fontconfig is Linux/BSD/macOS only (no Windows port in cvcpkg).
if [[ "${CVC_PLATFORM}" != "windows" ]]; then
    CONFIGURE_ARGS+=(--enable-libfontconfig)
fi

# NOTE: no --enable-libpulse.  libpulse is a PulseAudio *device* backend (audio
# capture/playback), not needed by a decode/streaming library — consumers do
# their own audio output.  It is also currently unbuildable here: the cvcpkg
# libpulse ships libpulsecommon in lib/pulseaudio/ but stamps libpulse.so's
# RUNPATH as $ORIGIN (not $ORIGIN/pulseaudio), so FFmpeg's link probe fails with
# undefined pa_* references.  See the libpulse recipe for the underlying bug.

# TLS backend — license-clean, keeps the build at v2.1 (see banner):
#   macOS uses the OS-native SecureTransport (no external lib); Linux and the
#   BSDs use GnuTLS (LGPL-2.1+), declared as a dependency for those platforms.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    CONFIGURE_ARGS+=(--enable-securetransport)
else
    CONFIGURE_ARGS+=(--enable-gnutls)
fi

# FFmpeg's configure hard-defaults its C compiler to "gcc" and does not
# reliably honour $CC.  On the BSDs (and macOS) the system compiler is
# clang exposed as cc, so pass the platform-selected compiler explicitly.
CONFIGURE_ARGS+=(--cc="${CC:-cc}")
if [[ -n "${CXX:-}" ]]; then
    CONFIGURE_ARGS+=(--cxx="${CXX}")
fi

# On platforms without nasm on PATH, fall back to disabling x86 asm so
# the build still succeeds (nasm is only declared as a build dep on
# linux/BSD; macOS runners ship their own assembler toolchain).
if ! command -v nasm >/dev/null 2>&1; then
    CONFIGURE_ARGS+=(--disable-x86asm)
fi

# Relocatable RPATH so the sibling libav* shared libs resolve within any
# install prefix.  On macOS "@loader_path" is a literal token that
# survives FFmpeg's configure, so pass it via --extra-ldflags.  On ELF
# platforms "$ORIGIN" gets expanded to an empty string by FFmpeg's
# configure shell (which then breaks the linker probe under lld on the
# BSDs), so we stamp the RPATH after install with patchelf instead.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    CONFIGURE_ARGS+=(--extra-ldflags="-Wl,-rpath,@loader_path")
fi

./configure "${CONFIGURE_ARGS[@]}" || {
    echo "cvcpkg: FFmpeg (LGPL) configure failed — dumping ffbuild/config.log" >&2
    tail -n 80 ffbuild/config.log >&2 || true
    exit 1
}

# FFmpeg's Makefile relies on GNU-make features, so build with gmake on
# the BSDs (their make(1) cannot parse it).
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        ;;
esac

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

# ELF platforms: stamp $ORIGIN into each installed libav*/libsw* so they
# find their siblings in the same lib dir regardless of the final prefix.
# patchelf is present on the Linux builders; if it is absent (e.g. some
# BSD runners) consumers fall back to their own RPATH / the cvcpkg
# activate LD path.
if [[ "${CVC_PLATFORM}" != "macos" ]] && command -v patchelf >/dev/null 2>&1; then
    shopt -s nullglob
    for _so in "${CVC_INSTALL_DIR}"/lib/lib{av,sw}*.so*; do
        [[ -L "${_so}" ]] && continue
        patchelf --set-rpath '$ORIGIN' "${_so}" || true
    done
    shopt -u nullglob
fi

# On macOS, FFmpeg stamps each dylib's LC_ID_DYLIB with the absolute
# install path and references siblings by absolute path too.  Rewrite
# both to @rpath so the bundle relocates.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    shopt -s nullglob
    _libdir="${CVC_INSTALL_DIR}/lib"
    for dylib in "${_libdir}"/lib*.dylib; do
        [[ -L "${dylib}" ]] && continue
        _base="$(basename "${dylib}")"
        install_name_tool -id "@rpath/${_base}" "${dylib}" || true
        # Repoint references to sibling libav*/libsw* dylibs at @rpath.
        while IFS= read -r dep; do
            case "${dep}" in
                "${_libdir}"/lib*.dylib)
                    install_name_tool -change "${dep}" \
                        "@rpath/$(basename "${dep}")" "${dylib}" || true
                    ;;
            esac
        done < <(otool -L "${dylib}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path" "${dylib}" 2>/dev/null || true
    done
    shopt -u nullglob
fi

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
