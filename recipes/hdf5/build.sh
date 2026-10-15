#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# HDF5 detects _Float16 with the C compiler, but src/H5private.h -- which uses
# it -- is also compiled by the C++ libraries (c++/, hl/c++/).  aarch64 GCC 11
# has _Float16 in C and not in C++, so linux/arm64 failed in H5PacketTable.cpp:
# "'_Float16' does not name a type".  Turn the non-standard float16 support off
# only when the C++ compiler cannot name the type; elsewhere nothing changes.
_extra=()
if ! echo '_Float16 h5_probe;' | "${CXX:-c++}" -x c++ -c -o /dev/null - >/dev/null 2>&1; then
    echo "hdf5: ${CXX:-c++} has no _Float16 in C++; HDF5_ENABLE_NONSTANDARD_FEATURE_FLOAT16=OFF"
    _extra+=(-DHDF5_ENABLE_NONSTANDARD_FEATURE_FLOAT16=OFF)
fi

cvc_cmake_build \
    -DHDF5_BUILD_CPP_LIB=ON \
    -DHDF5_INSTALL_CMAKE_DIR=lib/cmake/hdf5 \
    -DHDF5_BUILD_TOOLS=ON \
    -DHDF5_BUILD_EXAMPLES=OFF \
    -DBUILD_TESTING=OFF \
    -DHDF5_ENABLE_Z_LIB_SUPPORT=ON \
    ${_extra[@]+"${_extra[@]}"}
