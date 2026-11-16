#!/usr/bin/env bash
# recipes/websocketpp/build.sh -- install WebSocket++ (header-only): the headers and its CMake
# package config. Examples and tests stay off, so the configure needs no Boost.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# The CMake config goes to upstream's default, lib/cmake/websocketpp. Don't pass INSTALL_CMAKE_DIR
# as a relative -D: upstream declares it a PATH cache entry, so CMake makes it absolute against the
# build directory and the config never reaches the prefix.
cvc_cmake_build \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_TESTS=OFF
