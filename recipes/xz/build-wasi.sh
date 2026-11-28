#!/usr/bin/env bash
# recipes/xz/build-wasi.sh — cross-compile xz/liblzma to wasm32-wasi via wasi-sdk.
#
# liblzma only.  xz 5.6's CMake builds the xz, xzdec, lzmadec and lzmainfo
# tools unconditionally, and they need <signal.h> (sig_atomic_t, sigaction),
# which wasip1 does not have; the package ships the library alone on every
# platform anyway.  So build the liblzma target and install its two
# components rather than the whole project.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_wasi_cmake_configure \
    -DBUILD_TESTING=OFF \
    -DENABLE_THREADS=OFF \
    -DENABLE_NLS=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}" --target liblzma
for _component in liblzma_Runtime liblzma_Development; do
    cmake --install "${CVC_BUILD_DIR}" --component "${_component}"
done
cvc_rewrite_install_paths
