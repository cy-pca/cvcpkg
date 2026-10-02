#!/usr/bin/env bash
# recipes/ffmpeg-cli/build.sh — build the ffmpeg/ffprobe BINARIES on Linux, H.264 only.
#
# Sibling of recipes/ffmpeg (which is --disable-programs: libraries for other
# recipes to link). This one exists for consumers that shell out to an
# `ffmpeg` executable, e.g. GRL-SNAM's `grl-snam capture` (PNG frames ->
# libx264 mp4). Same minimal feature set as build.ps1 (Windows): H.264 encode
# plus the image/rawvideo demuxers a PNG sequence needs, and nothing else.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --cc="${CC:-cc}"
    --enable-gpl
    --enable-version3
    --enable-pic
    --extra-cflags="-I${CVC_DEPS_PREFIX}/include"
    --extra-ldflags="-L${CVC_DEPS_PREFIX}/lib"
    # Static libav*: the shipped binaries are self-contained apart from the
    # cvcpkg libx264/libz they are linked against (found via the RUNPATH below).
    --disable-shared
    --enable-static
    --disable-doc
    --disable-debug
    --disable-ffplay
    # Everything off, then back on only what an H.264 + image-sequence job needs.
    --disable-everything
    --disable-network
    --disable-autodetect
    --enable-zlib
    --enable-pthreads
    --enable-libx264
    --enable-encoder=libx264
    --enable-decoder=h264
    --enable-parser=h264
    --enable-encoder=png
    --enable-encoder=mjpeg
    --enable-decoder=png
    --enable-decoder=mjpeg
    --enable-decoder=rawvideo
    --enable-demuxer=image2
    --enable-demuxer=image2pipe
    --enable-demuxer=rawvideo
    --enable-demuxer=mov
    --enable-muxer=mp4
    --enable-muxer=mov
    --enable-muxer=image2
    --enable-muxer=rawvideo
    --enable-protocol=file
    --enable-protocol=pipe
    --enable-filter=scale
    --enable-filter=format
    --enable-filter=fps
    --enable-swscale
)

if ! command -v nasm >/dev/null 2>&1; then
    CONFIGURE_ARGS+=(--disable-x86asm)
fi

./configure "${CONFIGURE_ARGS[@]}" || {
    echo "cvcpkg: ffmpeg-cli configure failed — dumping ffbuild/config.log" >&2
    tail -n 80 ffbuild/config.log >&2 || true
    exit 1
}

make -j "${CVC_JOBS}"
make install

# Only the executables ship (package.files: bin/). Stamp a relocatable RUNPATH
# so they find libx264/libz in the sibling lib/ of whatever prefix they land in.
# (Stamped after install: "$ORIGIN" is mangled if passed through configure.)
for _exe in "${CVC_INSTALL_DIR}"/bin/ffmpeg "${CVC_INSTALL_DIR}"/bin/ffprobe; do
    patchelf --set-rpath '$ORIGIN/../lib' "${_exe}"
done

# Smoke test: must start and have the H.264 encoder. libx264/libz live in the
# deps prefix at build time (they only land beside bin/ once installed), so
# point the loader there for this check.
LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
    "${CVC_INSTALL_DIR}/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libx264
