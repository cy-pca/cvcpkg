#!/usr/bin/env bash
# recipes/epoll-shim/build.sh — build epoll-shim from source with CMake.
#
# epoll-shim is a small, dependency-free C library (kqueue-backed epoll/timerfd/
# signalfd/eventfd) for the BSDs. cvc_cmake_build handles the prefix / build type /
# -DBUILD_SHARED_LIBS (from CVC_LINK) / install / rpath rewrite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# CMAKE_INSTALL_PKGCONFIGDIR: upstream defaults it to "libdata/pkgconfig" on every
# non-Linux system. cvcpkg bundles keep .pc files in lib/pkgconfig on every
# platform (package.files, and the PKG_CONFIG_PATH every consumer recipe sets).
# The :PATH type is required. Upstream declares the variable with
# set(... CACHE PATH ...), and set() converts an UNTYPED command-line value that
# is relative into an absolute path against the current directory -- the .pc
# files then landed in the build tree, not the install prefix.
cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DENABLE_COMPILER_WARNINGS=OFF \
    -DCMAKE_INSTALL_PKGCONFIGDIR:PATH=lib/pkgconfig

# Upstream's .pc templates ship a literal empty "Version:" (the CMake project
# declares no VERSION). Fill in the release so versioned pkg-config checks work.
for _pc in "${CVC_INSTALL_DIR}/lib/pkgconfig/epoll-shim.pc" \
           "${CVC_INSTALL_DIR}/lib/pkgconfig/epoll-shim-interpose.pc"; do
    if [ ! -f "${_pc}" ]; then
        echo "epoll-shim: expected ${_pc} after install" >&2
        exit 1
    fi
    sed -i.bak "s/^Version:[[:space:]]*\$/Version: ${CVC_VERSION:-0.0.20240608}/" "${_pc}"
    rm -f "${_pc}.bak"
done
