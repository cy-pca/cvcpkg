#!/usr/bin/env bash
# recipes/eigen/build.sh — install Eigen (header-only) through its own CMake.
#
# Eigen compiles nothing for an install, but as the TOP-LEVEL project its
# defaults build the Eigen BLAS + LAPACK libraries, the tests, the demos and
# (natively) the docs -- every one of those must be switched off or the bundle
# grows libeigen_blas/libeigen_lapack and the configure wants a Fortran probe.
# What remains is: include/eigen3/{Eigen,unsupported},
# share/eigen3/cmake/Eigen3{Config,ConfigVersion,Targets}.cmake (Eigen3::Eigen),
# and share/pkgconfig/eigen3.pc.
#
# Two hermeticity switches:
#  - Eigen forces CMP0090 NEW and then defaults CMAKE_EXPORT_PACKAGE_REGISTRY
#    ON, so its export(PACKAGE Eigen3) would write a user package-registry entry
#    (~/.cmake/packages, HKCU on Windows) pointing at this temporary build tree,
#    where any later find_package(Eigen3) on the builder could pick it up.  Only
#    CMAKE_EXPORT_PACKAGE_REGISTRY=OFF stops that under CMP0090 NEW;
#    CMAKE_EXPORT_NO_PACKAGE_REGISTRY covers the pre-CMP0090 (3.4.0) behaviour.
#  - EIGEN_PRERELEASE_VERSION defaults to "dev", which the generated Eigen/Version
#    turns into EIGEN_VERSION_STRING "5.0.1-dev"; empty keeps the release string.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DCMAKE_EXPORT_PACKAGE_REGISTRY=OFF \
    -DCMAKE_EXPORT_NO_PACKAGE_REGISTRY=ON \
    -DEIGEN_PRERELEASE_VERSION= \
    -DBUILD_TESTING=OFF \
    -DEIGEN_BUILD_TESTING=OFF \
    -DEIGEN_BUILD_BLAS=OFF \
    -DEIGEN_BUILD_LAPACK=OFF \
    -DEIGEN_BUILD_DOC=OFF \
    -DEIGEN_BUILD_DEMOS=OFF \
    -DEIGEN_BUILD_BTL=OFF \
    -DEIGEN_BUILD_SPBENCH=OFF \
    -DEIGEN_BUILD_PKGCONFIG=ON \
    -DEIGEN_BUILD_CMAKE_PACKAGE=ON

grep -qF "EIGEN_VERSION_STRING \"${CVC_VERSION}\"" "${CVC_INSTALL_DIR}/include/eigen3/Eigen/Version" \
    || { echo "eigen: installed Eigen/Version is not ${CVC_VERSION}" >&2; exit 1; }

install -d "${CVC_INSTALL_DIR}/share/licenses/eigen"
install -m 0644 "${CVC_SOURCE_DIR}"/COPYING.* "${CVC_INSTALL_DIR}/share/licenses/eigen/"
