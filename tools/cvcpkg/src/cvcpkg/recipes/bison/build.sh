#!/usr/bin/env bash
# recipes/bison/build.sh — build GNU Bison from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Bison is an autotools project, not CMake — source the common env
# only for the variables, not for cvc_cmake_build.
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-nls

make -j "${CVC_JOBS}"
make install
