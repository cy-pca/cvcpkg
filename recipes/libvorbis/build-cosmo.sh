#!/usr/bin/env bash
# recipes/libvorbis/build-cosmo.sh — cross-compile libvorbis with Cosmopolitan.
#
# Portable floating-point DSP plus stdio is the whole platform requirement, and
# cosmocc supplies both.  Nothing here wants a shared library or dlopen, so the
# static-only nature of an APE is not a constraint.  libogg already covers
# cosmo, which is what makes this entry's runtime dependency resolvable.
#
# Host triple: x86_64-pc-linux-gnu rather than the x86_64-unknown-cosmo used by
# some older cosmo recipes — see recipes/libffi/build-cosmo.sh.  config.sub
# validates the OS field against a closed list and has no `cosmo` entry, so the
# cosmo triple is rejected before configure starts.  The real targeting comes
# from CC/AR/RANLIB exported by env-cosmo.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=x86_64-pc-linux-gnu \
    --build="${BUILD_TRIPLET}" \
    --with-ogg="${CVC_DEPS_PREFIX}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
