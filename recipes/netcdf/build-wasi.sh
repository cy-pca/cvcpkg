#!/usr/bin/env bash
# recipes/netcdf/build-wasi.sh — cross-compile netCDF-C to wasm32-wasi via wasi-sdk
# (netcdf-4 on HDF5). Uses the external cvcpkg hdf5 (wasi) + zlib (wasi) from
# CVC_DEPS_PREFIX (found via CMAKE_FIND_ROOT_PATH). Same minimal
# netcdf-4-on-HDF5 config as build-wasm.sh.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"
# Shared wasm32 SIZEOF_* pre-seed (see the file) so check_type_size is skipped.
source "${SCRIPT_DIR}/wasm-type-sizes.sh"

# NOTE: env-wasi.sh's cvc_cmake_build sets CMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY
# globally, which makes link-based check_function_exists false-positive — the same
# trap that makes netCDF misdetect a PARALLEL HDF5 and require MPI. We override it
# back to EXECUTABLE here (later -D wins) so the HDF5 symbol probes link correctly,
# and pre-seed the sizes so check_type_size doesn't need a static-lib try_compile.
# PARALLEL4 off is belt-and-suspenders (wasi has no MPI). (wasi flavor is exercised
# in the WASI build-out wave, not the cvc.6 critical path.)
cvc_cmake_build \
    "${WASM_TYPE_SIZES[@]}" \
    -DCMAKE_TRY_COMPILE_TARGET_TYPE=EXECUTABLE \
    -DENABLE_PARALLEL4=OFF -DNETCDF_ENABLE_PARALLEL4=OFF \
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
