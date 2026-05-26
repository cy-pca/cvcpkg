# recipes/libjpeg-turbo/build-wasm.ps1 — cross-compile libjpeg-turbo to wasm. SIMD disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DENABLE_SHARED=OFF',
    '-DENABLE_STATIC=ON',
    '-DWITH_TURBOJPEG=ON',
    '-DWITH_JAVA=OFF',
    '-DWITH_SIMD=OFF',
    '-DREQUIRE_SIMD=OFF'
)
