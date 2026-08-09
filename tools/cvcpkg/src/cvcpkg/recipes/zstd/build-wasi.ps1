# recipes/zstd/build-wasi.ps1 — cross-compile zstd to wasm32-wasi. CMake source is in build/cmake/.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild -SourceDir "$env:CVC_SOURCE_DIR\build\cmake" -ExtraArgs @(
    '-DZSTD_BUILD_PROGRAMS=OFF',
    '-DZSTD_BUILD_CONTRIB=OFF',
    '-DZSTD_BUILD_TESTS=OFF',
    '-DZSTD_BUILD_STATIC=ON',
    '-DZSTD_BUILD_SHARED=OFF'
)
