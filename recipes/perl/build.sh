#!/usr/bin/env bash
# recipes/perl/build.sh — build Perl 5 from source via its own Configure.
#
# Perl uses a bespoke Configure script (not autotools). `-des` accepts all
# defaults non-interactively. A relocatable install is produced under
# CVC_INSTALL_DIR; downstream recipes then find bin/perl on PATH.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

sh ./Configure -des \
    -Dprefix="${CVC_INSTALL_DIR}" \
    -Dusethreads \
    -Uversiononly \
    -Dman1dir=none \
    -Dman3dir=none

make -j "${CVC_JOBS}"
make install
