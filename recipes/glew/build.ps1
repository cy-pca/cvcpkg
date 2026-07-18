# recipes/glew/build.ps1 — build GLEW on Windows via CMake + MSVC.
# GLEW's CMake project lives in build/cmake; links opengl32 from the SDK.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$env:CVC_SOURCE_DIR = Join-Path $env:CVC_SOURCE_DIR 'build\cmake'

Invoke-CvcCMakeBuild @(
    '-DBUILD_UTILS=OFF',
    '-DGLEW_REGAL=OFF',
    '-DGLEW_OSMESA=OFF'
)
