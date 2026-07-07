#!/usr/bin/env bash
# recipes/ffmpeg/build-cosmo.sh — cross-compile FFmpeg with Cosmopolitan libc.
#
# Produces static libraries that can be linked into Actually Portable
# Executables (APE) running on Linux, macOS, Windows, and the BSDs
# from a single binary.
#
# The codec set is the same pared-down selection as the wasm/wasi builds:
# no hardware accel, no threads, no GPL external codecs in this profile —
# the cosmo build targets maximum portability over maximum features.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

"${CVC_SOURCE_DIR}/configure" \
    --prefix="${CVC_INSTALL_DIR}" \
    --target-os=linux \
    --arch=x86_64 \
    --enable-cross-compile \
    --cc="${CC}" \
    --cxx="${CXX}" \
    --ar="${AR}" \
    --ranlib="${RANLIB}" \
    --enable-static \
    --disable-shared \
    --disable-asm \
    --disable-runtime-cpudetect \
    --disable-programs \
    --disable-doc \
    --disable-debug \
    --disable-pthreads \
    --disable-w32threads \
    --disable-os2threads \
    --disable-network \
    --disable-hwaccels \
    --disable-everything \
    --enable-avcodec \
    --enable-avformat \
    --enable-avutil \
    --enable-swresample \
    --enable-swscale \
    --enable-avfilter \
    --enable-protocol=file \
    --enable-decoder=h264 \
    --enable-decoder=hevc \
    --enable-decoder=vp8 \
    --enable-decoder=vp9 \
    --enable-decoder=av1 \
    --enable-decoder=aac \
    --enable-decoder=mp3 \
    --enable-decoder=mp3float \
    --enable-decoder=vorbis \
    --enable-decoder=opus \
    --enable-decoder=flac \
    --enable-decoder=pcm_s16le \
    --enable-decoder=pcm_s16be \
    --enable-decoder=pcm_s24le \
    --enable-decoder=pcm_f32le \
    --enable-decoder=pcm_f64le \
    --enable-encoder=pcm_s16le \
    --enable-encoder=pcm_s24le \
    --enable-encoder=pcm_f32le \
    --enable-parser=h264 \
    --enable-parser=hevc \
    --enable-parser=vp8 \
    --enable-parser=vp9 \
    --enable-parser=aac \
    --enable-parser=mp3 \
    --enable-parser=opus \
    --enable-parser=vorbis \
    --enable-parser=flac \
    --enable-demuxer=mov \
    --enable-demuxer=matroska \
    --enable-demuxer=avi \
    --enable-demuxer=ogg \
    --enable-demuxer=mp3 \
    --enable-demuxer=wav \
    --enable-demuxer=flac \
    --enable-demuxer=aac \
    --enable-demuxer=mjpeg \
    --enable-muxer=mp4 \
    --enable-muxer=matroska \
    --enable-muxer=ogg \
    --enable-muxer=null \
    --enable-muxer=wav \
    || {
        echo "cvcpkg: FFmpeg cosmo configure failed — dumping config.log" >&2
        tail -n 80 "${CVC_BUILD_DIR}/ffbuild/config.log" 2>/dev/null >&2 || true
        exit 1
    }

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
