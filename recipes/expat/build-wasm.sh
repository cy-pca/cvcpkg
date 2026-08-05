#!/usr/bin/env bash
# recipes/expat/build-wasm.sh — cross-compile Expat to wasm via Emscripten.
#
# Expat is a self-contained C89 XML parser: no dependencies, and the only
# thing it wants from the platform is an entropy source for its hash salt.
# Emscripten's virtual FS provides /dev/urandom, which is expat's default
# on UNIX-like targets, so the detection needs no help.
#
# EXPAT_SHARED_LIBS=OFF because expat has its own shared/static switch
# independent of BUILD_SHARED_LIBS, and the tools are dropped: xmlwf is a
# CLI, which is not a useful artefact for a cross target.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DEXPAT_BUILD_TESTS=OFF \
    -DEXPAT_BUILD_EXAMPLES=OFF \
    -DEXPAT_BUILD_TOOLS=OFF \
    -DEXPAT_BUILD_DOCS=OFF \
    -DEXPAT_SHARED_LIBS=OFF
