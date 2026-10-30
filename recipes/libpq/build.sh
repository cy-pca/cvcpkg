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

# libintl.h and libintl live in the deps prefix wherever libc does not provide
# gettext (macOS, the BSDs).  PostgreSQL's meson probes the header with only its
# own include dirs plus extra_include_dirs, never CPPFLAGS or pkg-config, so
# without these -Dnls=enabled fails ("C header 'libintl.h' not usable") once
# msgfmt is found.  On glibc they change nothing: libintl is in libc.
# PL/Python is off: this recipe declares no Python, and auto-detection took
# whatever python3.pc the shared populate prefix held (python313t's on
# linux/arm64: "The limited API is not currently supported in the
# free-threaded build").
#
# Meson-based build.  readline is psql's line editor: libpq never links it and
# psql is not packaged, so it is off.  (Enabled, it failed the build once the
# readline build dep moved to CVC_BUILD_PREFIX, which this script never searched.)
#
# libpq compiles in SYSCONFDIR, where it reads pg_service.conf (connection
# settings for a service name), and LOCALEDIR, where it binds its message
# catalogs (format strings it prints errors through) unless PGLOCALEDIR is set.
# Both used to be under the job's install dir, /tmp/cvcpkg-builder/... on most
# builders, a path any local user can create on a machine that uses this
# libpq.  Use the system locations instead, which only root can write; libpq
# reads them as a /usr build of PostgreSQL does.
meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --libdir=lib \
    --sysconfdir=/etc \
    --localedir=/usr/share/locale \
    --buildtype=release \
    --pkg-config-path="${_PC}" \
    -Dextra_include_dirs="${CVC_DEPS_PREFIX}/include" \
    -Dextra_lib_dirs="${CVC_DEPS_PREFIX}/lib" \
    -Dplpython=disabled \
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
# Install through DESTDIR: the catalogs' install dir is now /usr/share/locale,
# which must not be written on the builder.  They ship in share/locale as
# before (PGLOCALEDIR points libpq at them).
_stage="${CVC_BUILD_DIR}/destdir"
rm -rf "${_stage}"
DESTDIR="${_stage}" ninja install
mkdir -p "${CVC_INSTALL_DIR}"
cp -R "${_stage}${CVC_INSTALL_DIR}/." "${CVC_INSTALL_DIR}/"
if [ -d "${_stage}/usr/share/locale" ]; then
    mkdir -p "${CVC_INSTALL_DIR}/share/locale"
    cp -R "${_stage}/usr/share/locale/." "${CVC_INSTALL_DIR}/share/locale/"
fi

# The library must carry the system directories and not the job's.
for _lib in "${CVC_INSTALL_DIR}"/lib/libpq.so.* "${CVC_INSTALL_DIR}"/lib/libpq.*.dylib; do
    [ -f "${_lib}" ] && [ ! -L "${_lib}" ] || continue
    if ! LC_ALL=C grep -a -q -F -e /etc/postgresql "${_lib}" ||
        LC_ALL=C grep -a -q -F -e "${CVC_INSTALL_DIR}/etc" -e "${CVC_INSTALL_DIR}/share/locale" "${_lib}"; then
        echo "libpq: ${_lib} does not compile in /etc/postgresql, or still names the job's etc or locale dir" >&2
        exit 1
    fi
done

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
