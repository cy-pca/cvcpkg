# recipes/re2/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DRE2_BUILD_TESTING=OFF',
    '-DCMAKE_CXX_STANDARD=17',
    # Static on Windows -- see grpc/build.ps1 comment.
    '-DBUILD_SHARED_LIBS=OFF'
)
