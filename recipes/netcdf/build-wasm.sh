#!/usr/bin/env bash
# recipes/netcdf/build-wasm.sh — cross-compile netCDF-C to wasm (netcdf-4 on HDF5).
# Uses the external cvcpkg hdf5 + zlib from CVC_DEPS_PREFIX (found via
# CMAKE_FIND_ROOT_PATH, which cvc_cmake_build sets). netCDF's own configure runs
# its TRY_RUN checks via the emscripten node emulator (this build does NOT hide
# node), so cross-compiling configures cleanly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# DAP/byterange (remote access via curl), NCZARR (libxml2/zip) and plugins (dlopen)
# are off — none work / are wanted on wasm. NETCDF_4 on HDF5 is the point.
# Both the new NETCDF_ENABLE_* and the legacy ENABLE_* option names are passed so
# the recipe is robust across netCDF option renames; unknown ones are ignored.
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
