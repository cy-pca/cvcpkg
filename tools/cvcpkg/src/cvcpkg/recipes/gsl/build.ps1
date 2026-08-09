# recipes/gsl/build.ps1 — GSL on Windows via vcpkg.
# GNU GSL uses autotools (no CMakeLists.txt), so we use the vcpkg
# port which wraps the build with its own CMake scaffolding.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcVcpkgInstall -Port 'gsl'
