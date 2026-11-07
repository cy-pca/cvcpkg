#!/usr/bin/env bash
# recipes/libinotify/build.sh -- build libinotify-kqueue with its autotools.
#
# A small C library on kqueue and pthreads, with no other dependencies.  The
# release tarball carries a generated configure, so this is a plain
# configure && make, with FreeBSD's port's own configure flag.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    # As devel/libinotify: do not watch the files inside a watched directory
    # on fusefs and smbfs, where opening each one is slow or has side effects.
    --enable-skip-subfiles=fusefs,smbfs
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Makefile.am compiles the library with -Wall -Werror; a newer clang than the
# release was tested with must not turn a new warning into a build failure.
# CFLAGS comes after the per-target flags on the compile line, so this wins.
export CFLAGS="${CFLAGS:-} -Wno-error"

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

for _f in lib/pkgconfig/libinotify.pc include/sys/inotify.h; do
    if [[ ! -f "${CVC_INSTALL_DIR}/${_f}" ]]; then
        echo "libinotify: expected ${_f} after install" >&2
        exit 1
    fi
done

cvc_rewrite_install_paths
