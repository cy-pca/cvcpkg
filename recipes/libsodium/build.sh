#!/usr/bin/env bash
# recipes/libsodium/build.sh -- build libsodium with upstream's ./configure on
# linux and macos.  Asm and runtime CPU dispatch stay on (libsodium picks its
# AVX2/SSE kernels by CPUID at sodium_init), so one bundle runs on every
# x86_64 and arm64 CPU.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck source=recipes/libsodium/sodium-cmake-config.sh
source "${SCRIPT_DIR}/sodium-cmake-config.sh"

cd "${CVC_SOURCE_DIR}"

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    link_flags=(--disable-shared --enable-static)
else
    link_flags=(--enable-shared --enable-static)
fi

# The extracted source does not keep execute bits everywhere (macOS: "Permission
# denied" on build-aux scripts); configure runs them through sh.
sh ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-dependency-tracking \
    --with-pic \
    "${link_flags[@]}"

make -j "${CVC_JOBS}"
make install

# libtool's archive records the temporary install prefix (libdir=...); nothing
# links through it, and a stale path is all it would carry.
rm -f "${CVC_INSTALL_DIR}/lib/libsodium.la"

case "${CVC_PLATFORM}" in
    macos) shared_lib="libsodium.dylib" ;;
    *) shared_lib="libsodium.so" ;;
esac
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    sodium_write_cmake_config "${CVC_INSTALL_DIR}" "libsodium.a" STATIC
else
    sodium_write_cmake_config "${CVC_INSTALL_DIR}" "${shared_lib}" SHARED
fi

cvc_rewrite_install_paths
# Autotools: libtool bakes the temporary prefix into the dylib's id; make it
# @rpath so the relocated bundle loads (no-op off macOS).
cvc_relocate_macos_dylibs
