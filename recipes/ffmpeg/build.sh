#!/usr/bin/env bash
# recipes/ffmpeg/build.sh — build FFmpeg shared libraries AND the
# ffmpeg/ffprobe programs on Linux/macOS/BSD.
#
# Builds a feature-rich GPL set: H.264 (x264) and H.265 (x265), external codecs
# (Opus, MP3, Vorbis, VP8/VP9, AV1), image formats (WebP, JPEG, PNG), OpenSSL
# for HTTPS, subtitle rendering (freetype, fontconfig, fribidi), and PulseAudio
# on Linux.  Non-free codecs (fdk-aac) are excluded.
#
# Nothing restricts components, so every muxer, demuxer, filter and protocol
# FFmpeg can build is present.  There is no --enable-programs: the binaries are
# on by default and only --disable-programs exists, so NOT passing it is what
# ships them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Put the cvcpkg prefix bin on PATH so FFmpeg's configure finds nasm
# (built as a build dependency) and pkg-config finds external libs.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --enable-gpl
    --enable-pic
    --enable-version3
    --disable-doc
    --disable-debug
    --disable-static
    --enable-shared
    # GPL codecs
    --enable-libx264
    --enable-libx265
    # External codecs (LGPL-compatible)
    --enable-libopus
    --enable-libmp3lame
    --enable-libvorbis
    --enable-libvpx
    --enable-libdav1d
    # Image formats.  JPEG and PNG are NATIVE in FFmpeg — mjpeg is built in and
    # png goes through zlib — so there is no --enable-libjpeg or
    # --enable-libpng.  configure aborts with 'Unknown option' on either, which
    # is why this recipe had never produced a build on any platform.  The real
    # external-JPEG option is --enable-libopenjpeg (JPEG 2000); cvcpkg has no
    # openjpeg recipe, so it is not requested here.
    --enable-libwebp
    # Subtitle rendering
    --enable-libfreetype
    --enable-libfribidi
    # Network / compression
    --enable-openssl
    --enable-zlib
    --enable-bzlib
    --enable-lzma
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS=(
        --prefix="${CVC_INSTALL_DIR}"
        --enable-gpl
        --enable-pic
        --enable-version3
        --disable-doc
        --disable-debug
        --enable-static
        --disable-shared
        --enable-libx264
        --enable-libx265
        --enable-libopus
        --enable-libmp3lame
        --enable-libvorbis
        --enable-libvpx
        --enable-libdav1d
        --enable-libwebp
        --enable-libfreetype
        --enable-libfribidi
        --enable-openssl
        --enable-zlib
        --enable-bzlib
        --enable-lzma
    )
fi

# The deps prefix must reach configure's own probes. pkg-config-covered libs
# get their -I/-L appended as each check succeeds, but libmp3lame ships no .pc
# file, so its bare `-lmp3lame` link test finds nothing without an explicit
# -L — configure then aborts "libmp3lame >= 3.98.3 not found" even though the
# library is right there. (ffmpeg's configure appends repeated --extra-* flags,
# so the macOS rpath addition below still composes with these.)
CONFIGURE_ARGS+=(
    --extra-cflags="-I${CVC_DEPS_PREFIX}/include"
    --extra-ldflags="-L${CVC_DEPS_PREFIX}/lib"
)

# fontconfig is Linux/BSD/macOS only (no Windows port in cvcpkg).
if [[ "${CVC_PLATFORM}" != "windows" ]]; then
    CONFIGURE_ARGS+=(--enable-libfontconfig)
fi

# PulseAudio is Linux-only.
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    CONFIGURE_ARGS+=(--enable-libpulse)
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
    echo "cvcpkg: FFmpeg configure failed — dumping ffbuild/config.log" >&2
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

# Prove the codec set rather than trusting configure — a missing external codec
# does not fail the build, it just silently drops the encoder.  Run the freshly
# installed binary out of the install dir, with the sibling shared libs on the
# loader path (the RPATH stamped above is $ORIGIN-relative, so this works
# without installing anything system-wide).
_ff="${CVC_INSTALL_DIR}/bin/ffmpeg"
[[ -x "${_ff}" ]] || { echo "cvcpkg: no ffmpeg binary produced at ${_ff}" >&2; exit 1; }
[[ -x "${CVC_INSTALL_DIR}/bin/ffprobe" ]] || { echo "cvcpkg: no ffprobe binary produced" >&2; exit 1; }

# The deps prefix must be on the loader path too: the external codec libs
# (libx264.so, libmp3lame.so, ...) live there, and on a split-prefix builder a
# binary that cannot load them produces no output at all — the encoder greps
# below would then misreport "missing encoder" when the binary never started.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${CVC_DEPS_PREFIX}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

_encoders="$("${_ff}" -hide_banner -encoders 2>&1)"
for _want in libx264 libx265 libopus libmp3lame libvorbis libvpx libwebp; do
    grep -q -- "${_want}" <<<"${_encoders}" || {
        echo "cvcpkg: built ffmpeg is missing the ${_want} encoder" >&2; exit 1; }
done

# Match on the NAME column only — a substring test would let "scale2ref" or
# "zscale" satisfy a check for "scale".
# Layout is "<flags> <name> <signature> <description>", e.g. "... hstack VV->V ...",
# so the signature column is what identifies a real filter row and $2 is its name.
_filters="$("${_ff}" -hide_banner -filters 2>&1 | awk '$3 ~ /->/ {print $2}')"
for _want in scale format fps hstack vstack overlay pad crop setpts; do
    grep -qx -- "${_want}" <<<"${_filters}" || {
        echo "cvcpkg: built ffmpeg is missing the ${_want} filter" >&2; exit 1; }
done

echo "cvcpkg: $("${_ff}" -version 2>&1 | head -n1) — full codec set + programs OK"
