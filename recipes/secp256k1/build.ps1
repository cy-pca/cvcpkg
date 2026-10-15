# recipes/secp256k1/build.ps1 — build libsecp256k1 from source on Windows (MSVC).
# Same module set as build.sh (recovery switched on for coincurve).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DSECP256K1_ENABLE_MODULE_ECDH=ON',
    '-DSECP256K1_ENABLE_MODULE_RECOVERY=ON',
    '-DSECP256K1_ENABLE_MODULE_EXTRAKEYS=ON',
    '-DSECP256K1_ENABLE_MODULE_SCHNORRSIG=ON',
    '-DSECP256K1_BUILD_TESTS=OFF',
    '-DSECP256K1_BUILD_EXHAUSTIVE_TESTS=OFF',
    '-DSECP256K1_BUILD_CTIME_TESTS=OFF',
    '-DSECP256K1_BUILD_BENCHMARK=OFF',
    '-DSECP256K1_BUILD_EXAMPLES=OFF',
    '-DCMAKE_INSTALL_LIBDIR=lib'
)
