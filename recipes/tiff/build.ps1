# recipes/tiff/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-Dtiff-tests=OFF',
    '-Dtiff-tools=OFF',
    '-Dtiff-contrib=OFF',
    '-Dtiff-docs=OFF',
    '-Dtiff-jpeg=OFF',
    '-Dtiff-jbig=OFF',
    '-Dtiff-lzma=OFF',
    '-Dtiff-webp=OFF',
    '-Dtiff-zstd=OFF',
    '-Dtiff-lerc=OFF'
)
