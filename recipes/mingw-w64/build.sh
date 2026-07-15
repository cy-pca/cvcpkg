#!/usr/bin/env bash
# recipes/mingw-w64/build.sh — stage the prebuilt llvm-mingw cross-toolchain.
#
# llvm-mingw is a prebuilt, relocatable Linux→Windows cross-toolchain: clang,
# lld, the mingw-w64 CRT + headers, and the x86_64-w64-mingw32-* wrappers all
# resolve their resources relative to bin/, so staging into the prefix works.
# No compilation happens here.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

cd "${CVC_SOURCE_DIR}"

# The upstream tarball ships sysroots for x86_64/i686/aarch64/armv7/arm64ec.
# Keep only the x86_64 Windows target (plus the shared "generic" headers/CRT it
# needs) to keep the package lean; drop the others.  (An arm64 Windows variant
# can be added later.)
rm -rf i686-w64-mingw32 aarch64-w64-mingw32 armv7-w64-mingw32 arm64ec-w64-mingw32

mkdir -p "${CVC_INSTALL_DIR}"
# Move (not copy) into the prefix so we don't need double the toolchain's disk
# while staging; fall back to a copy if source and install are on different
# filesystems.
for d in bin lib include share x86_64-w64-mingw32 generic-w64-mingw32; do
    [ -e "$d" ] || continue
    mv "$d" "${CVC_INSTALL_DIR}/" 2>/dev/null || cp -a "$d" "${CVC_INSTALL_DIR}/"
done

# Sanity check: the x86_64 Windows cross-compiler must be present and runnable.
"${CVC_INSTALL_DIR}/bin/x86_64-w64-mingw32-clang" --version >/dev/null
echo "mingw-w64 (llvm-mingw, x86_64 target) staged to ${CVC_INSTALL_DIR}"
