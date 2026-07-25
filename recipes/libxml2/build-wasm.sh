#!/usr/bin/env bash
# recipes/libxml2/build-wasm.sh — cross-compile libxml2 to wasm32 via emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-wasm.sh
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Freestanding feature set for wasm: static only, zlib for gzip-compressed XML,
# and NO threads/HTTP/FTP — the default emscripten target is single-threaded and
# has no sockets, and ImageMagick only needs libxml2 for local delegates.xml/
# type.xml and SVG parsing. Mirrors build.sh otherwise.
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
