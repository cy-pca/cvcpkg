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
