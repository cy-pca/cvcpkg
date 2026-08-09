# recipes/gmp/build.ps1 — install GMP on Windows via vcpkg.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcVcpkgInstall -Port 'gmp'
