#!/usr/bin/env bash
# recipes/libxshmfence/build.sh — build libxshmfence 1.3.3 with autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include${CPPFLAGS:+ ${CPPFLAGS}}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,\$ORIGIN${LDFLAGS:+ ${LDFLAGS}}"

_link=(--enable-shared --disable-static)
[[ "${CVC_LINK:-shared}" == "static" ]] && _link=(--disable-shared --enable-static)

cd "${CVC_SOURCE_DIR}"

# NetBSD has no futex and no process-shared pthread mutexes, so use the POSIX
# semaphore backend NetBSD's own libxshmfence uses (see netbsd-semaphore.patch).
# Its defines mirror NetBSD's lib/libxshmfence Makefile: 4 KiB pages (x86_64 and
# aarch64), the /var/shm tmpfs for the shared-memory file, sem_* from librt.
_extra=()
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    patch -p1 < "${SCRIPT_DIR}/netbsd-semaphore.patch"
    export CPPFLAGS="${CPPFLAGS} -DHAVE_SEMAPHORE=1 -DLIBXSHM_PAGESIZE=4096"
    export LIBS="-lrt${LIBS:+ ${LIBS}}"
    _extra+=(--with-shared-memory-dir=/var/shm)
fi

./configure --prefix="${CVC_INSTALL_DIR}" "${_link[@]}" \
    --disable-selective-werror ${_extra[@]+"${_extra[@]}"}
make -j "${CVC_JOBS}"
make install

# Strip libtool archives (.la): they embed the dependency's absolute build-prefix
# path, which is gone after cvcpkg relocates the prefix, so a downstream libtool
# link fails with "not a valid libtool archive". The .so + pkg-config .pc carry
# the real link info; nothing needs the .la.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
