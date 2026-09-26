# recipes/nlohmann-json/build.ps1 — install nlohmann/json (header-only) on Windows.
# Header-only CMake INTERFACE library, no platform-specific code — the same
# switches the POSIX build.sh uses.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DJSON_BuildTests=OFF',
    '-DJSON_Install=ON',
    '-DJSON_MultipleHeaders=ON'
)
