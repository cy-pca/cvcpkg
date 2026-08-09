#!/usr/bin/env bash
# recipes/libogg/build-cosmo.sh — cross-compile libogg with Cosmopolitan.
#
# libogg is pure buffer arithmetic, so cosmocc — an ordinary x86-64 C toolchain
# over a POSIX libc — needs no special handling, and the static-only, no-dlopen
# nature of an APE costs nothing here because the cross builds are static
# anyway.
#
# Host triple: NOT --host=x86_64-unknown-cosmo.  libogg 1.3.6 ships a 2024-era
# config.sub, which validates the OS field against a closed list and rejects
# `cosmo` outright (recipes/libffi/build-cosmo.sh hit exactly this and made the
# same switch).  x86_64-pc-linux-gnu is what cosmocc's ABI actually looks like
# to configure; CC/AR/RANLIB exported by env-cosmo.sh do the real targeting.
# Because that equals --build, configure stays in native mode and is free to
# run its probes — which is correct, since APE output executes directly on the
# Linux builder.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=x86_64-pc-linux-gnu \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
