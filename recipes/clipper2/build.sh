#!/usr/bin/env bash
# recipes/clipper2/build.sh — Clipper2 2.0.1 (C++), static, for every Unix
# platform and for wasm / wasm-mt (env-${CVC_PLATFORM}.sh picks the toolchain).
#
# The CMake project lives in CPP/ of the source tree.  Utilities, examples and
# tests are off: they fetch GoogleTest and add nothing a consumer links.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

LICENSE_FILE="${CVC_SOURCE_DIR}/LICENSE"
export CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/CPP"

cvc_cmake_build \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCLIPPER2_UTILS=OFF \
    -DCLIPPER2_EXAMPLES=OFF \
    -DCLIPPER2_TESTS=OFF \
    -DCLIPPER2_USINGZ=ON

install -d "${CVC_INSTALL_DIR}/share/clipper2"
install -m 0644 "${LICENSE_FILE}" "${CVC_INSTALL_DIR}/share/clipper2/LICENSE"

for f in lib/libClipper2.a lib/libClipper2Z.a include/clipper2/clipper.h \
         lib/cmake/clipper2/Clipper2Config.cmake; do
    [ -e "${CVC_INSTALL_DIR}/${f}" ] || { echo "clipper2: ${f} not installed" >&2; exit 1; }
done
echo "clipper2: installed $(ls "${CVC_INSTALL_DIR}"/lib/libClipper2*.a | tr '\n' ' ')"
