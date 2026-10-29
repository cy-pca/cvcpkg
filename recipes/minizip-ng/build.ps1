# recipes/minizip-ng/build.ps1 — build minizip-ng (zlib only, no compat layer) on Windows.
# MZ_FETCH_LIBS defaults ON for WIN32 upstream; it is off here as in build.sh.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DMZ_COMPAT=OFF',
    '-DMZ_ZLIB=ON',
    '-DMZ_BZIP2=OFF',
    '-DMZ_LZMA=OFF',
    '-DMZ_ZSTD=OFF',
    '-DMZ_LIBCOMP=OFF',
    '-DMZ_FETCH_LIBS=OFF',
    '-DMZ_FORCE_FETCH_LIBS=OFF',
    '-DMZ_PKCRYPT=OFF',
    '-DMZ_WZAES=OFF',
    '-DMZ_OPENSSL=OFF',
    '-DMZ_LIBBSD=OFF',
    '-DMZ_ICONV=OFF',
    '-DMZ_BUILD_TESTS=OFF',
    '-DMZ_BUILD_UNIT_TESTS=OFF'
)
