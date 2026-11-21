#!/usr/bin/env bash
# recipes/minizip/build.sh -- classic minizip from zlib's contrib/minizip, with this recipe's own
# CMakeLists.txt (upstream has none): the zlib source tree is the input, this directory the project.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

zlib_src="${CVC_SOURCE_DIR}"
CVC_SOURCE_DIR="${SCRIPT_DIR}" cvc_cmake_build -DZLIB_SOURCE_DIR="${zlib_src}"
