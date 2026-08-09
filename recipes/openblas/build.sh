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
    macos)
        # NOFORTRAN/C_LAPACK for the same reason as every other platform (see
        # below). OpenMP is additionally OFF because Apple clang ships no
        # libomp: -fopenmp is a hard configure failure there, not a slower
        # build. OpenBLAS still threads through its own pthread backend.
        OPENBLAS_EXTRA+=(-DNOFORTRAN=ON -DUSE_OPENMP=OFF -DC_LAPACK=ON)
        ;;
    *)
        # Same NOFORTRAN treatment as the BSDs, and for the same reason: a
        # Fortran-linked libopenblas.so carries a DT_NEEDED on libgfortran.so.5,
        # which cvcpkg does NOT ship. The bundle then only links on a host that
        # happens to have gfortran installed, and it fails in a way that names
        # the wrong culprit — a consumer's pkg-config probe resolves openblas.pc
        # fine, then its link check hits
        #     ld: warning: libgfortran.so.5, needed by libopenblas.so, not found
        #     ld: libopenblas.so: undefined reference to _gfortran_concat_string
        #     ld: cannot find -lopenblas
        # and the build reports "No BLAS library detected!". That blocked the
        # from-source numpy fleet-wide and cost a long hunt.
        #
        # Tried first and REJECTED: -static-libgfortran/-static-libgcc via
        # CMAKE_{SHARED,EXE}_LINKER_FLAGS. Those are gfortran-driver flags;
        # OpenBLAS links its shared library with another driver, so cmake
        # accepted them silently and libgfortran.so.5 was still NEEDED
        # (verified with objdump on the rebuilt bundle).
        #
        # Cost: LAPACK falls back to the bundled C reference implementation.
        # The optimized assembly BLAS kernels — the hot path for numpy/scipy —
        # are unaffected. Revisit under the BLAS provider work (roadmap Phase 10
        # capabilities/blas.yaml), where a real libgfortran package or a
        # vendored runtime can restore Fortran LAPACK for every provider at once.
        OPENBLAS_EXTRA+=(-DUSE_OPENMP=ON -DNOFORTRAN=ON -DC_LAPACK=ON)
        ;;
esac

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DNO_LAPACKE=OFF \
    -DDYNAMIC_ARCH=ON \
    -DTARGET=GENERIC \
    "${OPENBLAS_EXTRA[@]}"
