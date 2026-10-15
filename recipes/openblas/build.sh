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
        # NOFORTRAN/C_LAPACK: cvcpkg has no Fortran compiler for macOS (the
        # linux branch below has one). OpenMP is additionally OFF because Apple
        # clang ships no libomp: -fopenmp is a hard configure failure there, not
        # a slower build. OpenBLAS still threads through its own pthread backend.
        OPENBLAS_EXTRA+=(-DNOFORTRAN=ON -DUSE_OPENMP=OFF -DC_LAPACK=ON)
        ;;
    *)
        # Fortran LAPACK, compiled by cvcpkg's gfortran (a build dependency,
        # never one the builder happens to have). libopenblas.so then NEEDs
        # libgfortran.so.5, libquadmath.so.0 and libgomp.so.1, which the
        # gcc-runtime runtime dependency installs beside it.
        #
        # +cvc.5 to +cvc.7 built NOFORTRAN/C_LAPACK on linux, because cvcpkg did
        # not ship libgfortran: a Fortran-linked libopenblas.so only linked on a
        # host that had gfortran installed, and failed elsewhere as
        #     ld: warning: libgfortran.so.5, needed by libopenblas.so, not found
        # ("No BLAS library detected!" in numpy). -static-libgfortran had been
        # tried and does nothing here: it is a gfortran-driver flag, and OpenBLAS
        # links its shared library with the C driver.
        _fc=$(command -v gfortran || true)
        if [[ -z "${_fc}" ]] || ! "${_fc}" --version | grep -q "cvcpkg gfortran"; then
            echo "openblas build.sh: cvcpkg's gfortran is not on PATH (found '${_fc:-nothing}')" >&2
            exit 1
        fi
        OPENBLAS_EXTRA+=(-DUSE_OPENMP=ON "-DCMAKE_Fortran_COMPILER=${_fc}")
        ;;
esac

# NUM_THREADS is compiled in as MAX_CPU_NUMBER: the most threads OpenBLAS will
# ever use, and (x2) the buffers that bound how many threads may call BLAS at
# once. Left unset, CMake takes the BUILD host's core count -- +cvc.8's linux
# bundle, from a 4-core builder, reports MAX_THREADS=4 on every machine it runs
# on, and a 1-core builder would turn threading off. Past that many concurrent
# callers OpenBLAS warns "precompiled NUM_THREADS exceeded" and can crash
# (+cvc.8 with 64 threads calling dgemm: the warning every run, a segfault in
# 2 of 10). 64 is upstream's suggested value for distributed builds
# (docs/distributing.md); it costs ~120 bytes of static data per thread, and at
# run time OpenBLAS still uses no more threads than the machine has cores.
cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DNO_LAPACKE=OFF \
    -DDYNAMIC_ARCH=ON \
    -DTARGET=GENERIC \
    -DNUM_THREADS=64 \
    ${OPENBLAS_EXTRA[@]+"${OPENBLAS_EXTRA[@]}"}

# DIAGNOSTIC (not for merge): numpy-cp312's matmul dies with SIGBUS on Intel
# macOS.  Exercise dgemm on the freshly installed library under each kernel.
if [ "${CVC_PLATFORM}" = "macos" ]; then
    echo "== cpu: $(sysctl -n machdep.cpu.brand_string)"
    sysctl -n machdep.cpu.features machdep.cpu.leaf7_features || true
    D="$(mktemp -d)"
    cat > "${D}/t.c" <<'CEOF'
#include <stdio.h>
#include <stdlib.h>
#include <cblas.h>
static int run(int n) {
    double *a = malloc(sizeof(double) * n * n), *c = malloc(sizeof(double) * n * n);
    for (int i = 0; i < n * n; i++) a[i] = (double)(i % 7);
    cblas_dgemm(CblasRowMajor, CblasNoTrans, CblasTrans, n, n, n, 1.0, a, n, a, n, 0.0, c, n);
    double s = 0; for (int i = 0; i < n * n; i++) s += c[i];
    printf("n=%d sum=%g core=%s\n", n, s, openblas_get_corename());
    free(a); free(c); return 0;
}
int main(void) { run(3); run(64); run(300); return 0; }
CEOF
    "${CC:-cc}" -o "${D}/t" "${D}/t.c" -I"${CVC_INSTALL_DIR}/include" -I"${CVC_INSTALL_DIR}/include/openblas" \
        -L"${CVC_INSTALL_DIR}/lib" -lopenblas -Wl,-rpath,"${CVC_INSTALL_DIR}/lib"
    otool -L "${D}/t"
    OPENBLAS_VERBOSE=2 lldb --batch -o run -o bt -o "register read" -o "disassemble --pc" -o quit -- "${D}/t" 2>&1 | head -120 || true
    nm -m "${CVC_INSTALL_DIR}/lib/libopenblas.dylib" 2>/dev/null | grep -c " (__TEXT,__text) external" || true
    otool -l "${CVC_INSTALL_DIR}/lib/libopenblas.dylib" | grep -A4 -E "LC_BUILD_VERSION|LC_VERSION_MIN" | head -12 || true
    exit 1
fi
