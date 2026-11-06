#!/usr/bin/env bash
# recipes/netcdf/build.sh — native netCDF-C (netcdf-4 on HDF5) via CMake.
set -euo pipefail
: "${CVC_SOURCE_DIR:?}"; : "${CVC_BUILD_DIR:?}"; : "${CVC_INSTALL_DIR:?}"; : "${CVC_DEPS_PREFIX:?}"
CVC_JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"
case "$(echo "${CVC_BUILD_TYPE:-Release}" | tr '[:upper:]' '[:lower:]')" in
  debug) BT=Debug ;; *) BT=Release ;;
esac
_shared=ON; [ "${CVC_LINK:-shared}" = "static" ] && _shared=OFF

# netCDF 4.9.2 looks for libcurl and the Szip, Bz2, Blosc and Zstd compressors
# unconditionally, and libnetcdf links whichever it finds, even with DAP,
# byte-range and plugins off as here.  cvc.2 needed undeclared libraries:
# libcurl on OpenBSD (the ports one), FreeBSD and macOS, libzstd on FreeBSD and
# macOS, libbz2 on macOS.  With DAP, byte-range, NCZarr and the filter plugins
# off, libnetcdf calls none of them: nc_def_var_zstandard() and friends only
# name an HDF5 filter id, which needs an HDF5 filter plugin at run time, and
# without the library found they return NC_NOERR without setting the filter.
# Only hdf5 and zlib are declared, so keep CMake from finding the rest.
cmake -G Ninja -S "${CVC_SOURCE_DIR}" -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${BT}" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DBUILD_SHARED_LIBS="${_shared}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DENABLE_DAP=OFF -DENABLE_DAP4=OFF -DENABLE_BYTERANGE=OFF \
    -DENABLE_NCZARR=OFF -DENABLE_PLUGINS=OFF -DENABLE_LIBXML2=OFF \
    -DENABLE_NETCDF_4=ON -DENABLE_HDF5=ON \
    -DENABLE_TESTS=OFF -DBUILD_TESTING=OFF -DBUILD_UTILITIES=OFF \
    -DENABLE_EXAMPLES=OFF -DENABLE_FILTER_TESTING=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_CURL=ON -DCMAKE_DISABLE_FIND_PACKAGE_Szip=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_Bz2=ON -DCMAKE_DISABLE_FIND_PACKAGE_Blosc=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_Zstd=ON

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Relocate installed .pc/.cmake files and bin/*-config scripts, which keep this
# build's temporary install dir otherwise (recipes/_common/rewrite-install-paths.sh).
. "$(cd "$(dirname "$0")" && pwd)/../_common/rewrite-install-paths.sh"
cvc_rewrite_install_paths
