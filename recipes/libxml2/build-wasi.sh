#!/usr/bin/env bash
# recipes/libxml2/build-wasi.sh — cross-compile libxml2 to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-wasi.sh
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# Freestanding feature set for wasi: static only (wasi has no shared libs),
# zlib for gzip-compressed XML, and NO threads/HTTP/FTP — wasi has neither
# pthreads-by-default nor sockets, and ImageMagick only needs libxml2 for
# local delegates.xml/type.xml and SVG parsing. Mirrors build.sh otherwise.
cvc_cmake_build \
    -DBUILD_SHARED_LIBS=OFF \
    -DLIBXML2_WITH_ZLIB=ON \
    -DLIBXML2_WITH_LZMA=OFF \
    -DLIBXML2_WITH_ICONV=OFF \
    -DLIBXML2_WITH_ICU=OFF \
    -DLIBXML2_WITH_PYTHON=OFF \
    -DLIBXML2_WITH_PROGRAMS=OFF \
    -DLIBXML2_WITH_TESTS=OFF \
    -DLIBXML2_WITH_THREADS=OFF \
    -DLIBXML2_WITH_HTTP=OFF
