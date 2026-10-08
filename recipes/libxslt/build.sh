#!/usr/bin/env bash
# recipes/libxslt/build.sh — build libxslt (+libexslt) on Linux/macOS/BSD with
# CMake, linking cvcpkg's own libxml2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Minimal feature set, mirroring the sibling libxml2 recipe: shared libs, no
# CLI tools (xsltproc), no tests, no Python bindings. libxslt discovers our
# libxml2 through ITS installed CMake config package — the CMakeLists does
# `find_package(LibXml2 CONFIG REQUIRED)`, and env-*.sh puts CVC_DEPS_PREFIX on
# CMAKE_PREFIX_PATH — so nothing resolves to the build host's system libxml2.
#
# Crypto (libgcrypt/libgpg-error) and the runtime module loader (xsltmodule.c)
# stay OFF: our only consumer is lxml, which uses neither, and leaving them off
# keeps the dependency surface to libxml2 alone. Both already default OFF in
# the 1.1.45 CMakeLists; they are named here so the intent survives an upstream
# default flip.
cvc_cmake_build \
    -DBUILD_SHARED_LIBS=ON \
    -DLIBXSLT_WITH_PYTHON=OFF \
    -DLIBXSLT_WITH_PROGRAMS=OFF \
    -DLIBXSLT_WITH_TESTS=OFF \
    -DLIBXSLT_WITH_CRYPTO=OFF \
    -DLIBXSLT_WITH_MODULES=OFF

# cvcpkg ships the WHOLE install tree (package.files only DECLARES the payload,
# it does not filter it), so trim what this recipe does not ship.  libxslt's
# CMake install always writes the HTML docs/tutorials (share/doc), man pages
# (share/man), the xslt-config helper and the legacy xsltConf.sh autotools shim
# regardless of WITH_PROGRAMS; lxml and every other consumer use pkg-config or
# the CMake config package, never these.  Dropping them keeps the bundle to the
# library + headers + .pc/.cmake, matching the sibling libxml2's footprint.
rm -rf "${CVC_INSTALL_DIR}/share"
rm -f "${CVC_INSTALL_DIR}/bin/xslt-config" "${CVC_INSTALL_DIR}/lib/xsltConf.sh"
rmdir "${CVC_INSTALL_DIR}/bin" 2>/dev/null || true
