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

# GMP 6.3.0's compiler-picker probe (acinclude.m4 "long long reliability
# test 1") contains pre-C23 empty-parameter-list definitions
# (`void g(){}` / `void h(){}`) that are then called with arguments.
# Under gcc 14+ this is a hard error rather than a warning, so every
# compiler candidate is rejected and configure aborts with "could not
# find a working compiler".  We rewrite the two definitions to
# `void g(void){}` / `void h(void){}` in the generated configure
# script; the probe still exercises the original 64-bit-arithmetic
# regression it was designed to catch.
$configureFile = Join-Path $env:CVC_SOURCE_DIR 'configure'
if (Test-Path $configureFile) {
    $content = Get-Content -Raw -LiteralPath $configureFile
    $patched = $content -replace 'void g\(\)\{\}', 'void g(void){}' `
                        -replace 'void h\(\)\{\}', 'void h(void){}'
    if ($patched -ne $content) {
        Set-Content -LiteralPath $configureFile -Value $patched -NoNewline
        Write-Host "cvcpkg: patched gmp configure for gcc-15 C23 compatibility"
    }
}

Invoke-CvcMsysAutotoolsBuild @(
    '--enable-cxx',
    '--disable-assembly'
)
