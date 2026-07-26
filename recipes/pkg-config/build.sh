#!/usr/bin/env bash
# recipes/pkg-config/build.sh — build pkgconf and install it as `pkg-config`.
#
# pkgconf is the maintained pkg-config implementation: standalone C, no bundled
# glib, so it builds cleanly on modern toolchains (freedesktop pkg-config 0.29.2
# does not under Xcode 26.5 / clang 21). Built static so the `pkgconf` binary is
# self-contained, then symlinked to `pkg-config` (pkgconf is argument-compatible;
# `pkg-config` is the name PKG_CHECK_MODULES / meson / cmake invoke).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

make -j "${CVC_JOBS}"
make install

# Provide the `pkg-config` name expected by consumers.
ln -sf pkgconf "${CVC_INSTALL_DIR}/bin/pkg-config"
