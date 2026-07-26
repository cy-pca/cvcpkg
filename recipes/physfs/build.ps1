# recipes/physfs/build.ps1 — build PhysicsFS on Windows via CMake + MSVC.
#
# physfs uses its own PHYSFS_BUILD_STATIC / PHYSFS_BUILD_SHARED toggle,
# so translate CVC_LINK and build exactly one variant per link mode.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }
$shared = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }

Invoke-CvcCMakeBuild @(
    "-DPHYSFS_BUILD_STATIC=$static",
    "-DPHYSFS_BUILD_SHARED=$shared",
    '-DPHYSFS_BUILD_TEST=OFF',
    '-DPHYSFS_BUILD_DOCS=OFF'
)
