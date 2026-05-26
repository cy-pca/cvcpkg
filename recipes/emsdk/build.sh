#!/usr/bin/env bash
# recipes/emsdk/build.sh — snapshot an activated Emscripten SDK.
#
# The emsdk is fundamentally a release bundler. We clone, install,
# activate, and tar the resulting tree verbatim.  The activated
# snapshot is standalone — no network fetch needed at consumer time.
set -euo pipefail

EMSDK_VER="5.0.7"
EMSDK_REPO="https://github.com/emscripten-core/emsdk.git"

# Clone the emsdk repo at the pinned tag.
EMSDK_DIR="${CVC_BUILD_DIR}/emsdk"
if [[ ! -d "${EMSDK_DIR}" ]]; then
    git clone --depth 1 --branch "${EMSDK_VER}" "${EMSDK_REPO}" "${EMSDK_DIR}"
fi

cd "${EMSDK_DIR}"

# Install and activate the pinned version.
./emsdk install "${EMSDK_VER}"
./emsdk activate "${EMSDK_VER}"

# Pre-populate the Emscripten ports cache so first-build is offline.
source ./emsdk_env.sh
embuilder build MINIMAL

# Stage the entire activated tree into the install prefix.
# Exclude .git and CI metadata to save space.
rsync -a --exclude='.git' --exclude='.github' \
    "${EMSDK_DIR}/" "${CVC_INSTALL_DIR}/"

echo "emsdk ${EMSDK_VER} staged to ${CVC_INSTALL_DIR}"
