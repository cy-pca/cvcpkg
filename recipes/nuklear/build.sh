#!/usr/bin/env bash
# recipes/nuklear/build.sh — install the single-header Nuklear GUI toolkit.
#
# Header-only: there is nothing to compile, so the output is identical for
# both CVC_LINK=static and CVC_LINK=shared.  The shared env is sourced only
# for consistency (paths, cvc_rewrite_install_paths); no cvc_cmake_build.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

mkdir -p "${CVC_INSTALL_DIR}/include"
cp nuklear.h "${CVC_INSTALL_DIR}/include/"

# No .pc/.cmake files are produced, so this is a no-op, but keep it for
# convention/consistency with the other recipes.
if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
