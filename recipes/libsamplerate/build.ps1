# recipes/libsamplerate/build.ps1 — build libsamplerate on Windows via CMake + MSVC.
#
# Invoke-CvcCMakeBuild passes BUILD_SHARED_LIBS from CVC_LINK, so both the
# static (samplerate.lib) and shared (libsamplerate-0.dll + import lib) variants
# are produced from the same flags.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DLIBSAMPLERATE_EXAMPLES=OFF',
    '-DLIBSAMPLERATE_INSTALL=ON'
)
