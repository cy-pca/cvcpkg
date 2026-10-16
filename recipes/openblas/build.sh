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
        # Intel Macs: link with Apple's classic linker. ld-prime (the default
        # since Xcode 15) mis-links OpenBLAS's x86_64 assembly kernels: +cvc.9's
        # Intel dylib segfaulted in its first cblas_dgemm under every
        # DYNAMIC_ARCH kernel (Haswell, Zen, Sandybridge, Nehalem, Prescott),
        # and numpy died with SIGBUS in its matmul check. The same build linked
        # with -ld_classic passes under Haswell, Sandybridge, Nehalem and
        # Prescott. Apple has deprecated the flag; if a later Xcode drops it,
        # this links with the default and the dgemm check at the end of this
        # script fails the build instead of publishing a dylib that crashes.
        if [ "$(uname -m)" = "x86_64" ]; then
            _probe="$(mktemp -d)"
            printf 'int main(void) { return 0; }\n' > "${_probe}/t.c"
            if "${CC:-cc}" -Wl,-ld_classic -o "${_probe}/t" "${_probe}/t.c" >/dev/null 2>&1; then
                OPENBLAS_EXTRA+=("-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-ld_classic")
            else
                echo "openblas build.sh: the linker has no -ld_classic; using its default" >&2
            fi
            rm -rf "${_probe}"
        fi
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

# macOS: one dgemm through the installed library, the check that caught the
# Intel mis-link above (a crash, not a wrong answer, so any size shows it).
if [ "${CVC_PLATFORM}" = "macos" ]; then
    _t="$(mktemp -d)"
    cat > "${_t}/t.c" <<'CEOF'
#include <stdio.h>
#include <cblas.h>
int main(void) {
    static double a[64 * 64], c[64 * 64];
    double s = 0;
    for (int i = 0; i < 64 * 64; i++) a[i] = (double)(i % 7);
    cblas_dgemm(CblasRowMajor, CblasNoTrans, CblasTrans, 64, 64, 64, 1.0, a, 64, a, 64, 0.0, c, 64);
    for (int i = 0; i < 64 * 64; i++) s += c[i];
    printf("openblas dgemm check (%s): sum %.0f\n", openblas_get_corename(), s);
    return s == 2358405.0 ? 0 : 1;
}
CEOF
    "${CC:-cc}" -o "${_t}/t" "${_t}/t.c" -I"${CVC_INSTALL_DIR}/include" -I"${CVC_INSTALL_DIR}/include/openblas" \
        -L"${CVC_INSTALL_DIR}/lib" -lopenblas -Wl,-rpath,"${CVC_INSTALL_DIR}/lib"
    "${_t}/t" || { echo "openblas build.sh: cblas_dgemm through the built library failed (rc=$?)" >&2; exit 1; }
    rm -rf "${_t}"
fi
