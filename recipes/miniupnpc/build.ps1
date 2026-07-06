# recipes/miniupnpc/build.ps1 — CMake-based build of MiniUPnPc on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$staticOn = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }
$sharedOn = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }

Invoke-CvcCMakeBuild @(
    "-DUPNPC_BUILD_STATIC=$staticOn",
    "-DUPNPC_BUILD_SHARED=$sharedOn",
    '-DUPNPC_BUILD_TESTS=OFF',
    '-DUPNPC_BUILD_SAMPLE=ON'
)
