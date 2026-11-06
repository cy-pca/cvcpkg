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
#
# Optional libraries are declared, the OS's own or off, never whatever meson
# finds: it took what the builder or the shared populate prefix had, and cvc.2
# needed it wherever it ran.  Every platform's lib/libpq-oauth-18.so needed
# cvcpkg's libcurl; linux's and macOS's postgres and pgxml cvcpkg's libxml2,
# and pgxml its libxslt; NetBSD's postgres, dblink and libpqwalreceiver
# pkgsrc's libldap.so.6 and libxml2.so.16, and its libpq.so libldap_r.so.6.
#   libxml: the xml type, XMLTABLE and xpath(); libxml2 is a declared
#     dependency on every platform, so required.
#   libxslt: only contrib/xml2's deprecated xslt_process() uses it, and
#     cvcpkg's libxslt has no BSD build; off everywhere.
#   libcurl: libpq-oauth (the OAuth device flow), which the libpq recipe has
#     never had either.
#   LDAP: cvcpkg packages no OpenLDAP; macOS keeps its own (part of the OS).
#   PL/Perl, PL/Tcl: they link the interpreter's library, so built only where
#     the OS ships it: macOS both (/System's libperl and Tcl.framework),
#     OpenBSD PL/Perl (base libperl; its Tcl is a port).  PERL names the OS's
#     /usr/bin/perl: a shared prefix can put cvcpkg's perl first on PATH, and
#     PL/Perl would then link its libperl, undeclared.  cvcpkg packages no Tcl,
#     so only the OS's is found.  Off elsewhere: FreeBSD's and NetBSD's linked
#     the ports and pkgsrc libperl (libpq cvc.9).
_optional=(-Dlibxml=enabled -Dlibxslt=disabled -Dlibcurl=disabled)
case "${CVC_PLATFORM}" in
    macos)   _optional+=(-DPERL=/usr/bin/perl) ;;
    openbsd) _optional+=(-DPERL=/usr/bin/perl -Dpltcl=disabled -Dldap=disabled) ;;
    *)       _optional+=(-Dplperl=disabled -Dpltcl=disabled -Dldap=disabled) ;;
esac

# The BSDs: env-<bsd>.sh appends the ports/pkgsrc include and lib dirs to
# CFLAGS/LDFLAGS, and meson puts LDFLAGS ahead of every dependency's own -L, so
# where the host has a library this recipe declares (libxml2, openssl,
# readline, zstd, lz4, krb5, gettext) the build could link the host's copy.
# Search the deps prefix first.
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd)
        export CFLAGS="-I${CVC_DEPS_PREFIX}/include ${CFLAGS:-}"
        export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
        ;;
esac

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
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
    -Dgssapi=enabled \
    "${_optional[@]}"

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
