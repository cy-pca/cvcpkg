# recipes/hdf5/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DHDF5_BUILD_CPP_LIB=ON',
    '-DHDF5_BUILD_TOOLS=ON',
    '-DHDF5_BUILD_EXAMPLES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON',
    # HDF5 puts its CMake package in <prefix>/cmake on Windows; lib/cmake/hdf5
    # as on the other platforms (package.files; windows populate 2026-10-19).
    '-DHDF5_INSTALL_CMAKE_DIR=lib/cmake/hdf5'
)
