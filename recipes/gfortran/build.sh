#!/usr/bin/env bash
# recipes/gfortran/build.sh — GNU Fortran (GCC 11.4.0) from source, for linux.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || echo 4)}"

# GMP, MPFR and MPC in-tree: GCC configures and links them statically into the
# compiler, so cc1/f951 NEED nothing of ours at run time. Versions and sha512s
# are the ones this GCC release pins in contrib/prerequisites.sha512 (what its
# own contrib/download_prerequisites fetches, over plain http); fetched here
# over https and checked against that file, so the trust chain starts at the
# sha256-pinned GCC tarball. ISL (graphite loop optimizations) is left out.
cd "${CVC_SOURCE_DIR}"
_infra=https://gcc.gnu.org/pub/gcc/infrastructure
for _tarball in gmp-6.1.0.tar.bz2 mpfr-3.1.6.tar.bz2 mpc-1.0.3.tar.gz; do
    _dir=${_tarball%%-*}
    if [[ -d "${_dir}" ]]; then
        continue
    fi
    curl -fsSL --retry 5 --retry-delay 3 -o "${_tarball}" "${_infra}/${_tarball}"
    grep "  ${_tarball}\$" contrib/prerequisites.sha512 | sha512sum -c -
    mkdir "${_dir}"
    tar -xf "${_tarball}" -C "${_dir}" --strip-components=1
    rm -f "${_tarball}"
done

# The Debian/Ubuntu triplet, as the fleet's own GCC uses: GCC then finds the
# multiarch crt files and libc in /usr/lib/<triplet>.
_triplet="$(uname -m)-linux-gnu"
_version=$(cat gcc/BASE-VER)

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"
"${CVC_SOURCE_DIR}/configure" \
    --prefix="${CVC_INSTALL_DIR}" \
    --build="${_triplet}" --host="${_triplet}" --target="${_triplet}" \
    --enable-languages=c,fortran \
    --disable-multilib \
    --disable-bootstrap \
    --disable-nls \
    --without-isl \
    --disable-libsanitizer \
    --disable-libvtv \
    --enable-checking=release \
    --with-pkgversion="cvcpkg gfortran ${_version}" \
    --with-bugurl=https://cvcpkg.org
make -j "${CVC_JOBS}"
make install-strip

# Only gfortran goes on PATH: a build that puts this package's bin/ first must
# keep compiling C and C++ with the compiler it already had.
find "${CVC_INSTALL_DIR}/bin" -mindepth 1 \
    ! -name gfortran ! -name "${_triplet}-gfortran" -exec rm -f {} +
rm -rf "${CVC_INSTALL_DIR}/share"
mkdir -p "${CVC_INSTALL_DIR}/share/licenses/gfortran"
cp "${CVC_SOURCE_DIR}"/COPYING3 "${CVC_SOURCE_DIR}"/COPYING.RUNTIME \
    "${CVC_SOURCE_DIR}"/COPYING.LIB "${CVC_INSTALL_DIR}/share/licenses/gfortran/"

# libgfortran NEEDs libquadmath, its sibling in lib64/: give the runtime
# libraries an $ORIGIN run path (cvcpkg's packager only rewrites lib/).
for _so in "${CVC_INSTALL_DIR}"/lib64/lib{gfortran,quadmath,gomp}.so.*; do
    if [[ -f "${_so}" && ! -L "${_so}" ]]; then
        patchelf --set-rpath '$ORIGIN' "${_so}"
    fi
done

# The compiler works from where it is installed (GCC finds its own libexec and
# lib through the driver's location), compiles quad precision and links its
# runtime: build and run a program with the installed gfortran, nothing else.
_t=$(mktemp -d)
cat > "${_t}/t.f90" <<'F90'
program t
  use, intrinsic :: iso_fortran_env, only: real128
  real(real128) :: x
  x = sqrt(2.0_real128)
  if (abs(x * x - 2.0_real128) > 1.0e-30_real128) stop 1
  print '(a,f40.33)', 'sqrt(2) = ', x
end program t
F90
"${CVC_INSTALL_DIR}/bin/gfortran" --version | head -n 1
"${CVC_INSTALL_DIR}/bin/gfortran" -O2 -o "${_t}/t" "${_t}/t.f90" \
    -Wl,-rpath,"${CVC_INSTALL_DIR}/lib64"
env -u LD_LIBRARY_PATH "${_t}/t"
rm -rf "${_t}"
