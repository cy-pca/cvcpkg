#!/usr/bin/env bash
# recipes/libffi/build-cosmo.sh — cross-compile libffi with Cosmopolitan.
#
# cosmocc emits ordinary x86-64 System V code, so libffi's x86 backend
# (src/x86/{ffi64.c,unix64.S,sysv.S}) is exactly the right one.  The catch is
# the host triple: libffi's config.sub knows `emscripten*` and `wasi*` but
# has no `cosmo` OS, so `--host=x86_64-unknown-cosmo` (the triple the other
# cosmo recipes use, e.g. recipes/iconv) would be rejected outright.  We pass
# x86_64-pc-linux-gnu instead, which is what cosmocc's ABI actually looks
# like to configure.host, and let CC/AR/RANLIB from env-cosmo.sh do the
# targeting.  Note APE output runs natively on the Linux builder, so
# configure's run-time probes behave normally rather than falling back to
# cross-compilation guesses.
#
# Caveat worth knowing downstream: ffi_closure_alloc needs an executable
# mapping.  Cosmopolitan supports PROT_EXEC mmap, but a W^X-enforcing host
# (OpenBSD, hardened macOS) can refuse it at run time — that is a property of
# the APE's eventual host, not of this build.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=x86_64-pc-linux-gnu \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --disable-docs \
    --disable-dependency-tracking \
    --disable-multi-os-directory \
    --includedir="${CVC_INSTALL_DIR}/include"

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
