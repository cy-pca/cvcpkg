#!/usr/bin/env bash
# recipes/netcdf/build-wasi.sh — cross-compile netCDF-C to wasm32-wasi via wasi-sdk
# (netcdf-4 on HDF5). Uses the external cvcpkg hdf5 (wasi) + zlib (wasi) from
# CVC_DEPS_PREFIX (found via CMAKE_FIND_ROOT_PATH, which env-wasi.sh's
# cvc_cmake_build sets). Same minimal netcdf-4-on-HDF5 config as build-wasm.sh —
# DAP/byterange (remote curl), NCZARR (libxml2/zip) and plugins (dlopen) are off;
# they don't apply on a wasi backend target either. Both the new NETCDF_ENABLE_*
# and legacy ENABLE_* option names are passed so the recipe survives netCDF option
# renames; unknown ones are ignored.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

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
