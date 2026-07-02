# recipes/gmp/build.ps1 — build GMP on Windows via MSYS2 MinGW + autotools.
#
# GMP has no MSVC support upstream (assembly + libtool-only).  We build
# with the MinGW-w64 gcc / GNU make / m4 that ship with MSYS2, producing
# a cdecl-C DLL + import library that MSVC downstream (CGAL, MPFR) can
# consume.  --disable-assembly avoids GMP's hand-rolled asm which does
# not build under MinGW's ABI in some configurations; we take the
# portability hit over a build failure.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysAutotoolsBuild @(
    '--enable-cxx',
    '--disable-assembly'
)
