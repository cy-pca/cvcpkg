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

# GMP 6.3.0's compiler-picker probes ("long long reliability test 1",
# "double negation", ...) contain K&R-style function definitions such
# as `void g(){}` that are then called with arguments.  This was legal
# under `-std=gnu17` and older where empty parens meant "unspecified
# parameter list", but under gcc 14+ (default `-std=gnu23`) empty
# parens mean `(void)` and the argument-passing call sites become hard
# errors.  Every probe therefore fails and configure aborts with
# "could not find a working compiler".
#
# Fix: pin gcc's C standard to gnu17 for the probes by injecting
# `-std=gnu17` into the two gcc CFLAGS defaults in the generated
# configure script.  The probes still exercise their original
# regression checks; only the language mode is walked back.
$configureFile = Join-Path $env:CVC_SOURCE_DIR 'configure'
if (Test-Path $configureFile) {
    $content = Get-Content -Raw -LiteralPath $configureFile
    $patched = $content -replace 'gcc_cflags="-O2 -pedantic"',    'gcc_cflags="-O2 -std=gnu17 -pedantic"' `
                        -replace 'gcc_64_cflags="-O2 -pedantic"', 'gcc_64_cflags="-O2 -std=gnu17 -pedantic"'
    if ($patched -ne $content) {
        Set-Content -LiteralPath $configureFile -Value $patched -NoNewline
        Write-Host "cvcpkg: patched gmp configure to pin gcc CFLAGS to -std=gnu17 (gcc-15 C23 compat)"
    }
}

# GMP rejects `--enable-shared --enable-static` together with
# "configure: error: cannot build both static and DLL, since gmp.h
# is different for each." — pick exactly one based on CVC_LINK.
# Extras win over the helper's defaults because they come last on
# the configure command line.
$linkMode = if ($env:CVC_LINK -eq 'static') {
    @('--enable-static', '--disable-shared')
} else {
    @('--enable-shared', '--disable-static')
}

# -j 1: parallel make deadlocks MSYS2's fork emulation for GMP's
# libtool + mpn/*.c compiles when the builder runs as the SYSTEM user.
Invoke-CvcMsysAutotoolsBuild -Jobs 1 -ConfigureArgs (@('--enable-cxx', '--disable-assembly') + $linkMode)

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
