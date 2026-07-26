# recipes/openal-soft/build.ps1 — build OpenAL Soft on Windows via CMake + MSVC.
#
# On Windows OpenAL Soft builds the WASAPI/DSound/WinMM backends.  Honour
# CVC_LINK via the project's own LIBTYPE switch (STATIC / SHARED).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$libType = if ($env:CVC_LINK -eq 'static') { 'STATIC' } else { 'SHARED' }

Invoke-CvcCMakeBuild @(
    "-DLIBTYPE=$libType",
    '-DALSOFT_EXAMPLES=OFF',
    '-DALSOFT_UTILS=OFF',
    '-DALSOFT_TESTS=OFF',
    '-DALSOFT_INSTALL_EXAMPLES=OFF'
)
