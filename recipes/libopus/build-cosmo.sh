#!/usr/bin/env bash
# recipes/libopus/build-cosmo.sh — cross-compile libopus with Cosmopolitan.
#
# Opus asks nothing of the platform beyond libc, so cosmocc handles it directly.
#
# asm/rtcd/intrinsics stay OFF even though cosmocc really is an x86-64 compiler
# and `cpuid` really would work.  The reason is the same one recipes/libpng
# gives for PNG_HARDWARE_OPTIMIZATIONS=OFF on cosmo: an APE is meant to be the
# maximally portable artefact, and opus's x86 path compiles per-ISA translation
# units with -msse4.1/-mavx2 and selects between them at run time.  Baking
# build-time ISA specialisation into an archive whose whole point is running
# anywhere is the wrong trade, and it is the one that has bitten this repo
# before.  The reference C is correct and portable on every host an APE reaches.
#
# Host triple: x86_64-pc-linux-gnu, not x86_64-unknown-cosmo — opus 1.6.1 ships
# a current config.sub, which validates the OS field against a closed list with
# no `cosmo` entry and would reject that triple outright.  See
# recipes/libffi/build-cosmo.sh for the same call.
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
    --disable-dependency-tracking \
    --disable-extra-programs \
    --disable-doc \
    --enable-custom-modes \
    --disable-asm \
    --disable-rtcd \
    --disable-intrinsics

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
