# recipes/openblas/build.ps1 — OpenBLAS on Windows via vcpkg.
#
# Building from source requires gfortran (MinGW) for LAPACK Fortran code,
# which produces object files incompatible with MSVC's linker. Use the
# vcpkg port which handles this correctly.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcVcpkgInstall -Port 'openblas'
