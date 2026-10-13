#!/usr/bin/env bash
# recipes/krb5/build.sh — build MIT Kerberos 5 client libraries on Unix.
#
# The configure script lives in src/ inside the tarball.  We build
# only what we need (client libs) and install to CVC_INSTALL_DIR.
# The KDC server tools are also built as a side-effect of `make` but
# only the library artifacts are captured by package.files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

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

# krb5 ships its configure inside the src/ subdirectory.
cd "${CVC_SOURCE_DIR}/src"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --disable-static \
    --disable-dependency-tracking \
    --without-ldap \
    --without-tcl \
    --without-readline \
    --without-system-verto \
    --disable-nls

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.  Without this the
# krb5*.pc files kept the builder's temporary install dir as prefix=, so a
# consumer that finds GSSAPI through pkg-config (libpq) got a dead -I path.
cvc_rewrite_install_paths
