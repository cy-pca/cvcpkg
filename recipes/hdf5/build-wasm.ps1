# recipes/hdf5/build-wasm.ps1 — cross-compile HDF5 to wasm. Tools and threadsafe disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DHDF5_BUILD_CPP_LIB=ON',
    '-DHDF5_BUILD_TOOLS=OFF',
    '-DHDF5_BUILD_EXAMPLES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON',
    '-DHDF5_ENABLE_THREADSAFE=OFF'
)
