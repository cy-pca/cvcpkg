# recipes/mpfr/build.ps1 — build MPFR on Windows via MSYS2 MinGW + autotools.
#
# MPFR is autotools-only upstream.  We build the same way as gmp (its
# only dependency) so the ABI matches: MinGW gcc, cdecl DLL + import
# library that MSVC downstream can link against.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$msysDeps = if ($env:CVC_DEPS_PREFIX) {
    ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX
} else {
    throw 'CVC_DEPS_PREFIX must be set (gmp must be staged first)'
}

Invoke-CvcMsysAutotoolsBuild @(
    "--with-gmp='$msysDeps'"
)
