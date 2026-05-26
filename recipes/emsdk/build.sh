#!/usr/bin/env bash
# recipes/emsdk/build.sh — snapshot an activated Emscripten SDK.
#
# The emsdk is fundamentally a release bundler. We clone, install,
# activate, and tar the resulting tree verbatim.  The activated
# snapshot is standalone — no network fetch needed at consumer time.
set -euo pipefail

EMSDK_VER="5.0.7"

cd "${CVC_SOURCE_DIR}"

# Install and activate the pinned version.
./emsdk install "${EMSDK_VER}"
./emsdk activate "${EMSDK_VER}"

# Pre-populate the Emscripten ports cache so first-build is offline.
source ./emsdk_env.sh
embuilder build MINIMAL

# Stage the entire activated tree into the install prefix.
# Exclude .git and CI metadata to save space.
rsync -a --exclude='.git' --exclude='.github' \
    "${CVC_SOURCE_DIR}/" "${CVC_INSTALL_DIR}/"

echo "emsdk ${EMSDK_VER} staged to ${CVC_INSTALL_DIR}"
