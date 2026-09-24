#!/usr/bin/env bash
# recipes/netcdf/build.sh — native netCDF-C (netcdf-4 on HDF5) via CMake.
set -euo pipefail
: "${CVC_SOURCE_DIR:?}"; : "${CVC_BUILD_DIR:?}"; : "${CVC_INSTALL_DIR:?}"; : "${CVC_DEPS_PREFIX:?}"
CVC_JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"
case "$(echo "${CVC_BUILD_TYPE:-Release}" | tr '[:upper:]' '[:lower:]')" in
  debug) BT=Debug ;; *) BT=Release ;;
esac
_shared=ON; [ "${CVC_LINK:-shared}" = "static" ] && _shared=OFF

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
    -DENABLE_EXAMPLES=OFF -DENABLE_FILTER_TESTING=OFF

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
