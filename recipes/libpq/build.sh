#!/usr/bin/env bash
# recipes/libpq/build.sh — build libpq (PostgreSQL client) on Linux/macOS.
#
# PostgreSQL >= 16 supports Meson. We use it to build only the client
# library (libpq), not the full server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Dependencies live in two prefixes: CVC_DEPS_PREFIX (the runtime closure,
# e.g. openssl, krb5) and CVC_BUILD_PREFIX (the build closure: meson, ninja,
# bison, flex, pkg-config).  Search both; for a legacy single-prefix layout
# CVC_BUILD_PREFIX is unset or equal and this collapses to the old paths.
_PC="${CVC_DEPS_PREFIX}/lib/pkgconfig"
_BIN="${CVC_DEPS_PREFIX}/bin"
if [ -n "${CVC_BUILD_PREFIX:-}" ] && [ "${CVC_BUILD_PREFIX}" != "${CVC_DEPS_PREFIX}" ]; then
    _PC="${_PC}:${CVC_BUILD_PREFIX}/lib/pkgconfig"
    _BIN="${CVC_BUILD_PREFIX}/bin:${_BIN}"
fi
export PATH="${_BIN}:${PATH}"
export PKG_CONFIG_PATH="${_PC}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# cvcpkg's bison bakes its own build-time prefix in as the location of its
# m4 skeletons and of m4; point it at the relocated copies (as mariadb-server,
# mesa and gstreamer do), never at the host's.
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -d "${_root}/share/bison" && -z "${BISON_PKGDATADIR:-}" ]]; then
        export BISON_PKGDATADIR="${_root}/share/bison"
    fi
    if [[ -x "${_root}/bin/m4" && -z "${M4:-}" ]]; then
        export M4="${_root}/bin/m4"
    fi
done
unset _root

cd "${CVC_SOURCE_DIR}"

# Meson-based build.  readline is psql's line editor: libpq never links it and
# psql is not packaged, so it is off.  (Enabled, it failed the build once the
# readline build dep moved to CVC_BUILD_PREFIX, which this script never searched.)
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --libdir=lib \
    --buildtype=release \
    --pkg-config-path="${_PC}" \
    -Dssl=openssl \
    -Dzlib=enabled \
    -Dreadline=disabled \
    -Dzstd=enabled \
    -Dlz4=enabled \
    -Dnls=enabled \
    -Dgssapi=enabled

cd "${CVC_BUILD_DIR}"
# Build only the libpq shared library and install the whole project.
# Meson's install step will install everything, but the package.files
# glob in recipe.yaml filters to just the libpq artifacts.
ninja -j "${CVC_JOBS}"
ninja install

# Generate a minimal CMake find module so find_package(PostgreSQL) works.
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/PostgreSQL"
cat > "${CVC_INSTALL_DIR}/lib/cmake/PostgreSQL/PostgreSQLConfig.cmake" <<'EOF'
get_filename_component(_PG_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

add_library(PostgreSQL::PostgreSQL UNKNOWN IMPORTED)
# build.sh passes --libdir=lib, but bundles before cvc.7 put libpq in
# lib/<multiarch>/ (meson's default on Debian-family builders).
find_library(_PG_LIB NAMES pq PATHS "${_PG_PREFIX}/lib"
    PATH_SUFFIXES "" x86_64-linux-gnu aarch64-linux-gnu riscv64-linux-gnu
                  powerpc64le-linux-gnu s390x-linux-gnu
    NO_DEFAULT_PATH)
set_target_properties(PostgreSQL::PostgreSQL PROPERTIES
    IMPORTED_LOCATION "${_PG_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${_PG_PREFIX}/include"
)
set(PostgreSQL_FOUND TRUE)
set(PostgreSQL_INCLUDE_DIRS "${_PG_PREFIX}/include")
set(PostgreSQL_LIBRARIES "${_PG_LIB}")
unset(_PG_PREFIX)
unset(_PG_LIB)
EOF

# Ensure installed .pc/.cmake files are relocatable: libpq.pc kept the temporary
# install dir as prefix= (as krb5's did), a dead path anywhere it is installed.
cvc_rewrite_install_paths
