# recipes/ftxui/build.ps1 — build FTXUI (terminal UI library) on Windows via MSVC.
# Plain CMake C++17 library, no platform-specific code — the same switches as
# build.sh. Shared to match the other shared windows bundles.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DFTXUI_BUILD_EXAMPLES=OFF',
    '-DFTXUI_BUILD_TESTS=OFF',
    '-DFTXUI_BUILD_DOCS=OFF',
    '-DFTXUI_BUILD_MODULES=OFF',
    '-DFTXUI_ENABLE_INSTALL=ON',
    '-DFTXUI_QUIET=ON'
)
