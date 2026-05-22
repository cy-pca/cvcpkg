# recipes/hdf5/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DHDF5_BUILD_CPP_LIB=ON',
    '-DHDF5_BUILD_TOOLS=ON',
    '-DHDF5_BUILD_EXAMPLES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON'
)
