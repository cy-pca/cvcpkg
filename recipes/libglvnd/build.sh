#!/usr/bin/env bash
# recipes/libglvnd/build.sh — build libglvnd 1.7.0 with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

# NetBSD's x86_64 run-time linker has no TLS descriptors (R_X86_64_TLSDESC),
# so keep meson.build from choosing -mtls-dialect=gnu2: it skips its probe when
# c_args (seeded from CFLAGS) already names a dialect.
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    export CFLAGS="${CFLAGS:-} -mtls-dialect=gnu"
fi

# The dispatch-table generators run under import('python').find_installation(),
# which is the interpreter running meson (the host python3) unless a machine
# file names one.  Pin cvcpkg's python311: with LD_LIBRARY_PATH on the deps
# prefix, NetBSD's pkgsrc python3.13 loaded cvcpkg's older libexpat and its
# pyexpat failed ("Undefined symbol XML_SetAllocTrackerMaximumAmplification").
_python=""
for _root in "${CVC_DEPS_PREFIX}" "${CVC_BUILD_PREFIX:-}"; do
    if [[ -n "${_root}" && -x "${_root}/bin/python3.11" ]]; then
        _python="${_root}/bin/python3.11"
        break
    fi
done
: "${_python:?cvcpkg: python3.11 (cvcpkg python311) not found}"
_native="${CVC_BUILD_DIR}.native.ini"
cat > "${_native}" <<NATIVE
[binaries]
python = '${_python}'
NATIVE

cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --native-file "${_native}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN" \
    -Dx11=enabled \
    -Dglx=enabled \
    -Degl=true
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
