# recipes/qtshadertools/build.ps1 — build Qt ShaderTools module on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DQT_BUILD_EXAMPLES=OFF',
    '-DQT_BUILD_TESTS=OFF',
    '-DQT_BUILD_BENCHMARKS=OFF'
)
