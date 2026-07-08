#!/usr/bin/env bash
# recipes/python313t/build-cosmo.sh — cross-compile free-threaded CPython 3.13
# for Cosmopolitan (APE — Actually Portable Executables).
#
# STATUS: Experimental.
#
# NOTES:
#   - Cosmopolitan libc ships a pthreads implementation (cosmopolitan.h
#     exports pthread_create etc.), so --disable-gil should configure
#     and build successfully.
#   - The resulting binary is a fat APE that runs on Linux (2.6.18+),
#     macOS (23.1+), Windows (8+), FreeBSD (13+), OpenBSD (7.3+), and
#     NetBSD (9.2+) for both x86_64 and aarch64.
#   - The free-threaded Python runtime has been tested only on mainstream
#     Linux and macOS; behaviour on the full Cosmo compatibility matrix
#     (especially Windows TIB-based TLS, OpenBSD pledge, etc.) is unknown.
#   - Extension modules (.so) cannot be loaded from a Cosmo binary — the
#     standard library is statically linked; modules that require a shared
#     C extension fall back to "not available".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

export PYTHON_VERSION="3.13.3"
export PYTHON_MINOR="3.13"
export PYTHON_LDVERSION="3.13t"
export PYTHON_DISABLE_GIL=1

source "${SCRIPT_DIR}/../_common/build-python.sh"
