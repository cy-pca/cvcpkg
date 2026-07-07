#!/usr/bin/env bash
# recipes/ffmpeg/build-wasm.sh — cross-compile FFmpeg to WebAssembly via Emscripten.
#
# Produces a static, pared-down FFmpeg library set suitable for use in
# browser/WASM environments:
#   - No external codec libraries (only FFmpeg's built-in decoders/encoders)
#   - No threading, no hardware acceleration, no network stack
#   - Essential decoders: H.264, HEVC, VP8/VP9, AV1, AAC, MP3, Vorbis, Opus, FLAC
#   - Essential demuxers: MP4/MOV, Matroska/WebM, AVI, OGG, WAV, FLAC, AAC, MP3
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Build in the designated build directory.
mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

emconfigure "${CVC_SOURCE_DIR}/configure" \
    --prefix="${CVC_INSTALL_DIR}" \
    --target-os=none \
    --arch=x86_32 \
    --enable-cross-compile \
    --cc=emcc \
    --cxx=em++ \
    --ar=emar \
    --ranlib=emranlib \
    --nm=llvm-nm \
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
        echo "cvcpkg: FFmpeg configure failed — dumping config.log" >&2
        tail -n 80 "${CVC_BUILD_DIR}/ffbuild/config.log" 2>/dev/null >&2 || true
        exit 1
    }

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
