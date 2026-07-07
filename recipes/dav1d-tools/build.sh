#!/usr/bin/env bash
# recipes/dav1d-tools/build.sh — build the dav1d AV1 decoder CLI tool.
#
# Built from the same source tarball as the dav1d library recipe.
# libdav1d must be pre-built and available in CVC_DEPS_PREFIX.
# Only the dav1d tool binary is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Denable_tests=false \
    -Denable_tools=true \
    -Denable_examples=false \
    -Denable_docs=false \
    -Ddefault_library=shared

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}" tools/dav1d

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 "${CVC_BUILD_DIR}/tools/dav1d" "${CVC_INSTALL_DIR}/bin/dav1d"
