# recipes/hdf5/build-wasm.ps1 — cross-compile HDF5 to wasm. Tools and threadsafe disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


# Emscripten provides fenv.h but omits FE_INVALID/FE_DIVBYZERO/FE_OVERFLOW.
# Define them as no-ops so HDF5's H5Tinit_float.c compiles.
Invoke-CvcWasmCMakeBuild @(
    '-DHDF5_BUILD_CPP_LIB=ON',
    '-DHDF5_BUILD_TOOLS=OFF',
    '-DHDF5_BUILD_EXAMPLES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON',
    '-DHDF5_ENABLE_THREADSAFE=OFF',
    '-DCMAKE_C_FLAGS=-DFE_INVALID=0 -DFE_DIVBYZERO=0 -DFE_OVERFLOW=0'
)
