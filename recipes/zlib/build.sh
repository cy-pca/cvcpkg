#!/usr/bin/env bash
# recipes/zlib/build.sh — build zlib from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# (no-op touch: re-push to embed the current recipes/_common/env-openbsd.sh,
#  which now exports LD_LIBRARY_PATH so cmake, invoked here as a build-tool
#  dependency, can resolve its own runtime deps like libcurl.so on OpenBSD.)

cvc_cmake_build \
    -DZLIB_BUILD_EXAMPLES=OFF \
    -DINSTALL_PKGCONFIG_DIR="${CVC_INSTALL_DIR}/lib/pkgconfig"
