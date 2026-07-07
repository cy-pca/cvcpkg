#!/usr/bin/env bash
# recipes/ffmpeg/build-wasi.sh — cross-compile FFmpeg to WASI via wasi-sdk.
#
# Same pared-down codec set as the WASM build but targeting the WASI
# ABI (System Interface) rather than the browser Emscripten environment.
# Produces static libraries suitable for embedding in WASM runtimes
# (wasmtime, wasmer, WasmEdge, WAMR) that expose WASI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

_WASI_SYSROOT="${CVC_WASI_SDK_DIR}/share/wasi-sysroot"

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

"${CVC_SOURCE_DIR}/configure" \
    --prefix="${CVC_INSTALL_DIR}" \
    --target-os=none \
    --arch=wasm32 \
    --enable-cross-compile \
    --cc="${CC}" \
    --cxx="${CXX:-${CC}}" \
    --ar="${AR}" \
    --ranlib="${RANLIB}" \
    --nm="${NM:-llvm-nm}" \
    --extra-cflags="--sysroot=${_WASI_SYSROOT} -D_WASI_EMULATED_MMAN -D_WASI_EMULATED_SIGNAL" \
    --extra-ldflags="--sysroot=${_WASI_SYSROOT}" \
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
        echo "cvcpkg: FFmpeg wasi configure failed — dumping config.log" >&2
        tail -n 80 "${CVC_BUILD_DIR}/ffbuild/config.log" 2>/dev/null >&2 || true
        exit 1
    }

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
