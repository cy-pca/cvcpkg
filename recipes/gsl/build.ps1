# recipes/gsl/build.ps1 — build GSL on Windows via MSYS2 MinGW + autotools.
#
# GSL is autotools-only upstream (no CMake support, and community CMake
# ports are always behind).  Build with MinGW-w64 gcc, install into
# CVC_INSTALL_DIR, then the msys helper renames libgsl.dll.a → gsl.lib
# so MSVC downstream can link.  GSL is pure C, no libgcc runtime deps.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysAutotoolsBuild @(
    '--with-pic'
)
