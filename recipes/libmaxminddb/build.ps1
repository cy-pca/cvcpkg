# recipes/libmaxminddb/build.ps1 — build libmaxminddb from source on Windows (MSVC).
# Upstream sets WINDOWS_EXPORT_ALL_SYMBOLS for a shared build, so maxminddb.dll
# gets a usable maxminddb.lib import library. mmdblookup is not built here.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DMAXMINDDB_BUILD_BINARIES=OFF',
    '-DMAXMINDDB_INSTALL=ON',
    '-DCMAKE_INSTALL_LIBDIR=lib'
)
