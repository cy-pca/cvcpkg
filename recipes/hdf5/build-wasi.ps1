# recipes/hdf5/build-wasi.ps1 — cross-compile HDF5 to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


# wasi-sdk's fenv.h likewise doesn't define FE_INVALID/FE_DIVBYZERO/FE_OVERFLOW.
Invoke-CvcWasiCMakeBuild @(
    '-DHDF5_BUILD_CPP_LIB=ON',
    '-DHDF5_BUILD_TOOLS=OFF',
    '-DHDF5_BUILD_EXAMPLES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON',
    '-DHDF5_ENABLE_THREADSAFE=OFF',
    '-DCMAKE_C_FLAGS=-DFE_INVALID=0 -DFE_DIVBYZERO=0 -DFE_OVERFLOW=0'
)
