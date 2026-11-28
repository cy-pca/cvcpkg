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
# xmlIO.c's gzip (and, unbuilt here, xz) readers and writers open "-"
# (stdin/stdout) by dup()ing the descriptor, and wasip1 has no dup().  Those
# three calls are the only ones, so on wasi "-" gives no compressed stream
# (gzdopen(-1) fails and the open returns NULL, as for a bad descriptor);
# gzip-compressed XML files still open, and so does uncompressed "-", which
# reads stdin without the dup.
_xmlio="${CVC_SOURCE_DIR}/xmlIO.c"
if [[ "$(grep -c 'dup(fileno(std\(in\|out\)))' "${_xmlio}")" != 3 ]]; then
    echo "libxml2: expected three dup(fileno(stdin|stdout)) calls in xmlIO.c" >&2
    exit 1
fi
sed -i 's/dup(fileno(std\(in\|out\)))/-1/' "${_xmlio}"

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
