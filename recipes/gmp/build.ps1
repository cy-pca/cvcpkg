# recipes/gmp/build.ps1 — build GMP on Windows natively: MinGW-w64 gcc +
# autotools under the MSYS2 shell, every tool from cvcpkg.
#
# GMP has no MSVC build upstream (libtool + autotools only).  The toolchain is
# cvcpkg's own: the msys2 bootstrap (bash + coreutils), make, m4, autoconf,
# automake, libtool and mingw-w64-gcc (WinLibs GCC, UCRT runtime), all declared
# under depends.build scoped to windows.  No vcpkg, and nothing from an ambient
# C:\msys64.  The result is a C DLL + import library that MSVC consumers (CGAL,
# MPFR, libcvc) link directly:
#
#   bin\libgmp-10.dll                 the runtime (UCRT, like an MSVC /MD build)
#   lib\gmp.lib, lib\libgmp.dll.a     the import library (same bytes, both names)
#   include\gmp.h
#
# Same names on purpose as every other cvcpkg MinGW/libtool bundle (gsl's
# libgsl-28.dll + gsl.lib).  The vcpkg-built bundle this replaces (6.3.0+cvc.1)
# named the DLL gmp-10.dll, so anything linked against THAT one -- mpfr's
# windows +cvc.1 -- imports gmp-10.dll and must be rebuilt against this one.
#
# --disable-assembly avoids GMP's hand-rolled asm, which does not build under
# MinGW's ABI in some configurations; we take the speed hit over a build
# failure.
#
# Only the C library on Windows: no --enable-cxx.  A MinGW libgmpxx is C++
# compiled against libstdc++: its exported stream operators carry Itanium
# mangling that MSVC cannot link, and its DLL would need the MinGW C++ runtime
# (libstdc++-6.dll, libgcc_s_seh-1.dll) beside it.  Shipping it (plus the gmpxx.lib copy the helper makes)
# would turn on CGAL's GMPXX support -- CGAL's FindGMPXX matches gmpxx.lib --
# for an MSVC consumer that cannot use it.  Without it CGAL uses its own
# Gmpz/Gmpq wrappers over the C API, which is what it does whenever GMPXX is
# absent.  linux/macos/BSD still build gmpxx (build.sh).
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
Invoke-CvcMsysAutotoolsBuild -Jobs 1 -ConfigureArgs (@('--disable-assembly') + $linkMode)

# -- What did the build install? ---------------------------------------------
$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include'

# The DLL names a PE file imports, from `dumpbin /dependents`: the indented bare
# file names only (the "Dump of file C:\...\x.dll" banner also ends in .dll).
function Get-CvcGmpImports([string]$Path) {
    $lines = & dumpbin /nologo /dependents $Path
    if ($LASTEXITCODE -ne 0) { throw "gmp: dumpbin /dependents $Path failed" }
    $lines | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '(?i)^[^\s\\/:]+\.dll$' }
}

# libtool's .la files hold the absolute, ephemeral build prefix and nothing an
# MSVC consumer reads.
Get-ChildItem -LiteralPath $lib -File -Filter '*.la' -ErrorAction SilentlyContinue |
    Remove-Item -Force

Write-Host '-- gmp: installed tree --'
Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -File | ForEach-Object {
    Write-Host ("  {0}  ({1} bytes)" -f $_.FullName.Substring($env:CVC_INSTALL_DIR.Length).TrimStart('\', '/'), $_.Length)
}

$header = Join-Path $inc 'gmp.h'
$implib = Join-Path $lib 'gmp.lib'
foreach ($required in @($header, $implib)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "gmp: $required was not installed" }
}
$vcpkgLeft = @(Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -Force |
    Where-Object { $_.Name -match '(?i)vcpkg' })
if ($vcpkgLeft.Count -gt 0) { throw "gmp: vcpkg-named file in the install tree: $($vcpkgLeft[0].FullName)" }
# One configuration per bundle: no vcpkg-style debug\ tree beside the release one.
if (Test-Path -LiteralPath (Join-Path $env:CVC_INSTALL_DIR 'debug')) { throw "gmp: install tree has a debug\ subtree (vcpkg's layout)" }

$dll = $null
if ($env:CVC_LINK -ne 'static') {
    $dll = Join-Path $bin 'libgmp-10.dll'
    if (-not (Test-Path -LiteralPath $dll)) { throw "gmp: $dll was not installed" }

    # The DLL may import the OS and the UCRT, nothing else: no MinGW runtime
    # (libgcc_s_seh-1, libstdc++-6, libwinpthread-1, libmcfgthread-2), which
    # nothing ships beside it, and no msvcrt.dll, which would put a second C
    # runtime in an MSVC (/MD, UCRT) consumer's process.
    $deps = @(Get-CvcGmpImports $dll)
    Write-Host "gmp: bin\libgmp-10.dll imports: $($deps -join ', ')"
    $foreign = @($deps | Where-Object { $_ -notmatch '(?i)^(kernel32\.dll|api-ms-win-crt-[a-z0-9-]+\.dll|ucrtbase\.dll)$' })
    if ($foreign.Count -gt 0) {
        throw "gmp: libgmp-10.dll imports [$($foreign -join ', ')]; only KERNEL32 and the UCRT are allowed"
    }
}

# -- Smoke test: an MSVC consumer ----------------------------------------------
# Compile with cl /MD against the installed gmp.h, link lib\gmp.lib, run it.
# This is the consumer the bundle exists for (CGAL, MPFR and libcvc build with
# MSVC), so it exercises the parts a MinGW-built DLL could get wrong for them:
# MSVC reading gmp.h, link.exe reading the GNU-format import library, dllimport
# DATA symbols (gmp_version, mp_bits_per_limb), memory handed back across the
# DLL boundary, and a FILE* passed into the DLL (mpz_out_str), which only works
# when both sides use the same C runtime.  Built under CVC_BUILD_DIR, never the
# install dir: everything in the install dir ships.
# A static build is not smoke-tested: lib\gmp.lib is then a MinGW archive whose
# objects call into libgcc (e.g. ___chkstk_ms for GMP's alloca), which an MSVC
# link does not provide.  The shipped Windows configuration is release/shared.
if (-not $dll) {
    Write-Host 'gmp: NOTE: --link static -- MSVC smoke test skipped (see build.ps1)'
} else {
    $smoke = Join-Path $env:CVC_BUILD_DIR 'gmp-smoke'
    if (Test-Path -LiteralPath $smoke) { Remove-Item -Recurse -Force -LiteralPath $smoke }
    New-Item -ItemType Directory -Force -Path $smoke | Out-Null
    $src = @'
#include <stdio.h>
#include <string.h>
#include <gmp.h>

int main(void) {
    static const char want[] = "265252859812191058636308480000000"; /* 30! */
    void (*free_fn)(void *, size_t);
    mpz_t f, p;
    mpq_t q;
    char *s;
    int ok;

    mpz_init(f);
    mpz_fac_ui(f, 30);
    s = mpz_get_str(NULL, 10, f);
    ok = strcmp(s, want) == 0;
    printf("gmp-smoke: gmp %s, %d-bit limbs, 30! = %s\n", gmp_version, mp_bits_per_limb, s);
    mp_get_memory_functions(NULL, NULL, &free_fn);
    free_fn(s, strlen(s) + 1);

    /* 2^127 - 1 is prime */
    mpz_init(p);
    mpz_ui_pow_ui(p, 2, 127);
    mpz_sub_ui(p, p, 1);
    ok = ok && mpz_probab_prime_p(p, 25) > 0;

    /* 1/3 + 1/6 == 1/2 */
    mpq_init(q);
    mpq_set_ui(q, 1, 3);
    {
        mpq_t r;
        mpq_init(r);
        mpq_set_ui(r, 1, 6);
        mpq_add(q, q, r);
        mpq_clear(r);
    }
    ok = ok && mpz_cmp_ui(mpq_numref(q), 1) == 0 && mpz_cmp_ui(mpq_denref(q), 2) == 0;

    /* A FILE* from this (MSVC, UCRT) side, written by the DLL. */
    fputs("gmp-smoke: mpz_out_str(stdout) 2^127-1 = ", stdout);
    ok = ok && mpz_out_str(stdout, 10, p) > 0;
    fputs("\n", stdout);
    fflush(stdout);
    gmp_printf("gmp-smoke: gmp_printf %Zd / %Qd\n", f, q);
    fflush(stdout);

    mpq_clear(q);
    mpz_clear(p);
    mpz_clear(f);
    ok = ok && mp_bits_per_limb == 64;
    printf("gmp-smoke: %s\n", ok ? "OK" : "FAILED");
    return ok ? 0 : 1;
}
'@
    Set-Content -LiteralPath (Join-Path $smoke 'smoke.c') -Value $src -Encoding Ascii
    Copy-Item -LiteralPath $dll -Destination $smoke   # beside the exe, as a consumer ships it

    Push-Location $smoke
    try {
        & cl /nologo /W3 /O2 /MD "/I$inc" smoke.c /Fe:smoke.exe /link $implib
        if ($LASTEXITCODE -ne 0) { throw 'gmp: smoke.c does not compile/link with cl /MD against include\gmp.h + lib\gmp.lib' }
        $exeDeps = @(Get-CvcGmpImports (Join-Path $smoke 'smoke.exe'))
        Write-Host "gmp: smoke.exe imports: $($exeDeps -join ', ')"
        if (@($exeDeps | Where-Object { $_ -ieq 'libgmp-10.dll' }).Count -ne 1) {
            throw 'gmp: smoke.exe does not import libgmp-10.dll -- lib\gmp.lib binds to the wrong DLL name'
        }
        $out = & .\smoke.exe
        $rc = $LASTEXITCODE
        $out | ForEach-Object { Write-Host $_ }
        if ($rc -ne 0) { throw "gmp: smoke.exe failed (exit $rc)" }
    } finally {
        Pop-Location
    }
    Write-Host 'gmp: OK -- an MSVC (cl) consumer compiles against include\gmp.h, links lib\gmp.lib and runs'
}

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
