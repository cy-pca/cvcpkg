# recipes/libwebp/build-wasi.ps1 — cross-compile libwebp to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-DWEBP_BUILD_ANIM_UTILS=OFF',
    '-DWEBP_BUILD_CWEBP=OFF',
    '-DWEBP_BUILD_DWEBP=OFF',
    '-DWEBP_BUILD_GIF2WEBP=OFF',
    '-DWEBP_BUILD_IMG2WEBP=OFF',
    '-DWEBP_BUILD_VWEBP=OFF',
    '-DWEBP_BUILD_WEBPINFO=OFF',
    '-DWEBP_BUILD_WEBPMUX=OFF',
    '-DWEBP_BUILD_EXTRAS=OFF',
    '-DWEBP_BUILD_LIBWEBPMUX=ON'
)
