#!/usr/bin/env bash
# recipes/postgresql-server/build.sh — build the PostgreSQL server on
# Linux/macOS/BSD.
#
# PostgreSQL >= 16 uses Meson.  We build the whole tree (the same proven
# configuration as the libpq recipe) and then prune the pure client-only
# programs, leaving the server daemon and server-side tools.  Pruning only
# removes files, so it can never break the binaries that are kept.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Put the cvcpkg prefix bin on PATH so meson finds ninja from our recipe.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${CVC_SOURCE_DIR}"

# cvcpkg's flex and bison bake in their own build-time prefix as the location of
# m4 (and bison of its skeletons); point them at the relocated copies, as libpq
# does.  On linux/arm64 flex exec'd a dead .../libcvc-deps/prefix/bin/m4.
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

# PostgreSQL's meson appends "postgresql" to its data, plugin and server-header
# dirs only when the prefix does not already name postgres or pgsql, and
# CVC_INSTALL_DIR does (.../cvcpkg-postgresql-client-XXXXXXXX/install).  The
# flat layout that gave (share/*.sample, plugins straight in lib/,
# include/server/) matched none of package.files on the 2026-10-15 populate.
# Configure through a neutrally named symlink to the install dir instead, under
# /tmp (the fleet's TMPDIR is a job dir that names postgresql too), and point
# whatever the build baked back at CVC_INSTALL_DIR after installing.  --libdir=lib
# keeps libpq out of Debian's lib/<multiarch>/.
_PGTMP="$(mktemp -d /tmp/cvcpkg-pg.XXXXXX)"
trap 'rm -rf "${_PGTMP}"' EXIT
_PGPREFIX="${_PGTMP}/prefix"
ln -s "${CVC_INSTALL_DIR}" "${_PGPREFIX}"

# libintl.h and libintl live in the deps prefix wherever libc does not provide
# gettext (macOS, the BSDs).  PostgreSQL's meson probes the header with only its
# own include dirs plus extra_include_dirs, never CPPFLAGS or pkg-config, so
# without these -Dnls=enabled fails ("C header 'libintl.h' not usable") once
# msgfmt is found.  On glibc they change nothing: libintl is in libc.
# PL/Python is off: this recipe declares no Python, and auto-detection took
# whatever python3.pc the shared populate prefix held (python313t's on
# linux/arm64: "The limited API is not currently supported in the
# free-threaded build").
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${_PGPREFIX}" \
    --libdir=lib \
    --buildtype=release \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dextra_include_dirs="${CVC_DEPS_PREFIX}/include" \
    -Dextra_lib_dirs="${CVC_DEPS_PREFIX}/lib" \
    -Dplpython=disabled \
    -Dssl=openssl \
    -Dzlib=enabled \
    -Dreadline=enabled \
    -Dzstd=enabled \
    -Dlz4=enabled \
    -Dnls=enabled \
    -Dgssapi=enabled

cd "${CVC_BUILD_DIR}"
ninja -j "${CVC_JOBS}"
ninja install

# Text files (pkg-config, pgxs Makefiles) name the symlink; name the install dir.
{ grep -rlIF "${_PGPREFIX}" "${CVC_INSTALL_DIR}" 2>/dev/null || true; } | while IFS= read -r _f; do
    sed -i.cvcbak "s|${_PGPREFIX}|${CVC_INSTALL_DIR}|g" "${_f}" && rm -f "${_f}.cvcbak"
done

# ── Prune pure client-only programs ────────────────────────────
# These ship in the postgresql-client package.  The server package
# still keeps pg_isready and pg_config, which are useful on a server
# host, plus the bundled libpq the server-side tools link against.
cd "${CVC_INSTALL_DIR}"
for prog in psql pg_dump pg_dumpall pg_restore pg_amcheck pgbench \
            createdb dropdb createuser dropuser clusterdb reindexdb vacuumdb; do
    rm -f "bin/${prog}"
done

echo "postgresql-server: kept $(ls bin | wc -l | tr -d ' ') programs in bin/"
