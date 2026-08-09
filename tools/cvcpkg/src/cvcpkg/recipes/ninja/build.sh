#!/usr/bin/env bash
# recipes/ninja/build.sh — bootstrap Ninja from source on Linux and macOS.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

cd "${CVC_SOURCE_DIR}"

# Ninja bootstraps itself via configure.py.
python3 configure.py --bootstrap

mkdir -p "${CVC_INSTALL_DIR}/bin"
cp ninja "${CVC_INSTALL_DIR}/bin/ninja"
