# recipes/libwebp/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
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
