#!/usr/bin/env bash
# recipes/lame/build.sh — build the lame MP3 encoder CLI tool.
#
# Built from the same source tarball as libmp3lame, against the in-tree STATIC
# libmp3lame, so the binary is self-contained.  Only bin/lame is installed;
# the shared library is the libmp3lame recipe's.
#
# This used to configure with both --disable-shared and --disable-static and
# run `make -C frontend` alone, expecting the packaged libmp3lame from the
# deps prefix.  But the frontend's Makefile links ../libmp3lame/libmp3lame.la
# from this tree, and nothing had built it ("No rule to make target
# '../libmp3lame/libmp3lame.la', needed by 'lame'"), so lame failed on every
# platform.  Build the whole tree with the static library instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-dependency-tracking \
    --enable-frontend \
    --enable-static \
    --disable-shared \
    --disable-nls

make -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 frontend/lame "${CVC_INSTALL_DIR}/bin/lame"
