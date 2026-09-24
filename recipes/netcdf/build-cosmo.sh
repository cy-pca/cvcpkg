#!/usr/bin/env bash
# recipes/netcdf/build-cosmo.sh — cross-compile netCDF-C (netcdf-4 on HDF5) with
# Cosmopolitan. Links the external cvcpkg hdf5 (cosmo) + zlib (cosmo) from
# CVC_DEPS_PREFIX. Same minimal netcdf-4-on-HDF5 config as the other flavors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DNETCDF_ENABLE_DAP=OFF -DENABLE_DAP=OFF \
    -DNETCDF_ENABLE_DAP4=OFF -DENABLE_DAP4=OFF \
    -DNETCDF_ENABLE_BYTERANGE=OFF -DENABLE_BYTERANGE=OFF \
    -DNETCDF_ENABLE_NCZARR=OFF -DENABLE_NCZARR=OFF \
    -DNETCDF_ENABLE_PLUGINS=OFF -DENABLE_PLUGINS=OFF \
    -DNETCDF_ENABLE_LIBXML2=OFF -DENABLE_LIBXML2=OFF \
    -DENABLE_NETCDF_4=ON -DENABLE_HDF5=ON \
    -DNETCDF_ENABLE_TESTS=OFF -DENABLE_TESTS=OFF -DBUILD_TESTING=OFF \
    -DNETCDF_BUILD_UTILITIES=OFF -DBUILD_UTILITIES=OFF \
    -DNETCDF_ENABLE_EXAMPLES=OFF -DENABLE_EXAMPLES=OFF \
    -DNETCDF_ENABLE_FILTER_TESTING=OFF -DENABLE_FILTER_TESTING=OFF
