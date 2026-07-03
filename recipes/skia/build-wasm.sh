#!/usr/bin/env bash
# recipes/skia/build-wasm.sh — build CanvasKit (Skia's wasm variant).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_HOST_PLATFORM:-${CVC_PLATFORM}}.sh"

: "${CVC_EMSDK_DIR:?CVC_EMSDK_DIR must point to the activated emsdk bundle}"

SKIA_TAG="chrome/m137"
SRC="${CVC_SOURCE_DIR}/skia"
if [[ ! -d "${SRC}/.git" ]]; then
    git clone --depth=1 --branch "${SKIA_TAG}" https://skia.googlesource.com/skia.git "${SRC}"
fi
cd "${SRC}"

python3 tools/git-sync-deps

# Activate emsdk.
source "${CVC_EMSDK_DIR}/emsdk_env.sh"

# CanvasKit ships its own build wrapper under modules/canvaskit.
cd modules/canvaskit
./compile.sh release

# Stage output.
mkdir -p "${CVC_INSTALL_DIR}/bin" \
         "${CVC_INSTALL_DIR}/include/skia/canvaskit"

OUT_JS="${SRC}/out/canvaskit_wasm/canvaskit.js"
OUT_WASM="${SRC}/out/canvaskit_wasm/canvaskit.wasm"
if [[ -f "${OUT_JS}" && -f "${OUT_WASM}" ]]; then
    cp "${OUT_JS}"   "${CVC_INSTALL_DIR}/bin/"
    cp "${OUT_WASM}" "${CVC_INSTALL_DIR}/bin/"
fi

# Public API headers for C++ consumers of CanvasKit's exposed types.
if [[ -d "${SRC}/modules/canvaskit/npm_build/types" ]]; then
    cp -R "${SRC}/modules/canvaskit/npm_build/types" \
          "${CVC_INSTALL_DIR}/include/skia/canvaskit/"
fi

echo "skia CanvasKit (wasm) installed to ${CVC_INSTALL_DIR}"
