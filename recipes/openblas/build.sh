#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

OPENBLAS_EXTRA=()

# BSDs typically lack gfortran and libgomp; build without Fortran LAPACK
# (uses the bundled C reference LAPACK) and disable OpenMP.
case "${CVC_PLATFORM}" in
    *bsd)
        OPENBLAS_EXTRA+=(-DNOFORTRAN=ON -DUSE_OPENMP=OFF -DC_LAPACK=ON)
        ;;
    *)
        OPENBLAS_EXTRA+=(-DUSE_OPENMP=ON)
        ;;
esac

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DNO_LAPACKE=OFF \
    -DDYNAMIC_ARCH=ON \
    -DTARGET=GENERIC \
    "${OPENBLAS_EXTRA[@]}"
