# recipes/libjpeg-turbo/build-wasi.ps1 — cross-compile libjpeg-turbo to wasm32-wasi. SIMD disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild @(
    '-DENABLE_SHARED=OFF',
    '-DENABLE_STATIC=ON',
    '-DWITH_TURBOJPEG=ON',
    '-DWITH_JAVA=OFF',
    '-DWITH_SIMD=OFF',
    '-DREQUIRE_SIMD=OFF'
)
