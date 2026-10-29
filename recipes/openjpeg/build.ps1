# recipes/openjpeg/build.ps1 — build libopenjp2 (shared, no codec executables) on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_STATIC_LIBS=OFF',
    '-DBUILD_CODEC=OFF',
    '-DBUILD_THIRDPARTY=OFF',
    '-DBUILD_TESTING=OFF',
    '-DBUILD_DOC=OFF'
)
