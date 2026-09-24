# recipes/netcdf/build.ps1 — native Windows netCDF-C (netcdf-4 on HDF5) via CMake.
# HDF5 + zlib come from the cvcpkg deps prefix (Invoke-CvcCMakeBuild puts it on
# CMAKE_PREFIX_PATH so find_package(HDF5) resolves). Same minimal
# netcdf-4-on-HDF5 config as the POSIX/wasm/wasi flavors: DAP/byterange/NCZARR/
# plugins/libxml2 off, tests/utilities/examples off.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DNETCDF_ENABLE_DAP=OFF', '-DENABLE_DAP=OFF',
    '-DNETCDF_ENABLE_DAP4=OFF', '-DENABLE_DAP4=OFF',
    '-DNETCDF_ENABLE_BYTERANGE=OFF', '-DENABLE_BYTERANGE=OFF',
    '-DNETCDF_ENABLE_NCZARR=OFF', '-DENABLE_NCZARR=OFF',
    '-DNETCDF_ENABLE_PLUGINS=OFF', '-DENABLE_PLUGINS=OFF',
    '-DNETCDF_ENABLE_LIBXML2=OFF', '-DENABLE_LIBXML2=OFF',
    '-DENABLE_NETCDF_4=ON', '-DENABLE_HDF5=ON',
    '-DNETCDF_ENABLE_TESTS=OFF', '-DENABLE_TESTS=OFF', '-DBUILD_TESTING=OFF',
    '-DNETCDF_BUILD_UTILITIES=OFF', '-DBUILD_UTILITIES=OFF',
    '-DNETCDF_ENABLE_EXAMPLES=OFF', '-DENABLE_EXAMPLES=OFF',
    '-DNETCDF_ENABLE_FILTER_TESTING=OFF', '-DENABLE_FILTER_TESTING=OFF'
)
