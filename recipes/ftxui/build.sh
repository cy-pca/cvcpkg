#!/usr/bin/env bash
# recipes/ftxui/build.sh — build FTXUI (terminal UI library) from source.
# Plain CMake C++17 library with zero external dependencies. Tests/examples/docs
# off; the single-config Ninja build installs libs into lib/ (not lib/<config>/),
# plus the CMake package config (ftxui::screen/dom/component) and include/ftxui/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DFTXUI_BUILD_EXAMPLES=OFF \
    -DFTXUI_BUILD_TESTS=OFF \
    -DFTXUI_BUILD_DOCS=OFF \
    -DFTXUI_BUILD_MODULES=OFF \
    -DFTXUI_ENABLE_INSTALL=ON \
    -DFTXUI_QUIET=ON
