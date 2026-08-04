#!/usr/bin/env bash
# recipes/perl/build.sh — build Perl 5 from source via its own Configure.
#
# Perl uses a bespoke Configure script (not autotools). `-des` accepts all
# defaults non-interactively.
#
# -Duserelocatableinc is LOAD-BEARING and was missing. `-Dprefix` alone bakes
# ABSOLUTE @INC paths at configure time, and cvcpkg builds in a throwaway
# scratch dir, so the published bundle pointed its own core modules at a
# directory that ceased to exist the moment the build finished:
#
#     Can't locate strict.pm in @INC (@INC entries checked:
#       /tmp/cvcpkg-builder/cvcpkg-job-perl-vk_k0664/cvcpkg-perl-mf3qnc5w/install/lib/...)
#     BEGIN failed--compilation aborted at ./Configure line 13.
#
# i.e. the bundle could not run `perl -e 'use strict'` on any machine. That is
# why openssl (whose Configure IS a perl script) failed to build fleet-wide,
# which cascade-cancelled curl and log4cplus and left several variants
# unbuildable. The old comment claimed this install was relocatable; it was not.
#
# With -Duserelocatableinc, @INC entries are stored as ".../"-prefixed paths
# resolved relative to the perl binary at runtime, so the bundle works wherever
# cvcpkg unpacks it.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

sh ./Configure -des \
    -Dprefix="${CVC_INSTALL_DIR}" \
    -Duserelocatableinc \
    -Dusethreads \
    -Uversiononly \
    -Dman1dir=none \
    -Dman3dir=none

make -j "${CVC_JOBS}"
make install
