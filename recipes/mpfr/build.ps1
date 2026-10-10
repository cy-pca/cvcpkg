# recipes/mpfr/build.ps1 — build MPFR on Windows natively: MinGW-w64 gcc +
# autotools under the MSYS2 shell, every tool from cvcpkg.
#
# MPFR is autotools-only upstream.  We build it the same way as gmp (its only
# dependency) so the two match: MinGW-w64 gcc (UCRT), a C DLL + import library
# that MSVC consumers (CGAL, libcvc) link directly:
#
#   bin\libmpfr-6.dll                  imports libgmp-10.dll, KERNEL32, the UCRT
#   lib\mpfr.lib, lib\libmpfr.dll.a    the import library (same bytes, both names)
#   include\mpfr.h, include\mpf2mpfr.h
#
# It links the gmp bundle in CVC_DEPS_PREFIX and carries no copy of it.  (The
# vcpkg-built windows bundle this replaces, 4.2.1+cvc.1, shipped vcpkg's whole
# gmp beside mpfr-6.dll -- gmp-10.dll, gmp.lib, gmp.h -- and vcpkg's port
# helpers under share/vcpkg-*.)
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

if (-not $env:CVC_DEPS_PREFIX) { throw 'CVC_DEPS_PREFIX must be set (gmp must be staged first)' }
$depsInc = Join-Path $env:CVC_DEPS_PREFIX 'include'
$depsLib = Join-Path $env:CVC_DEPS_PREFIX 'lib'
$depsBin = Join-Path $env:CVC_DEPS_PREFIX 'bin'
$gmpDll  = Join-Path $depsBin 'libgmp-10.dll'
foreach ($required in @((Join-Path $depsInc 'gmp.h'), (Join-Path $depsLib 'gmp.lib'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "mpfr: $required not found -- gmp must be staged in CVC_DEPS_PREFIX" }
}

# --with-gmp in drive-letter form with forward slashes (C:/...), not the MSYS
# /c/... form: the compiler is NATIVE MinGW gcc, which cannot open /c/... paths
# (the same reason Invoke-CvcMsysAutotoolsBuild passes --prefix that way).
$gmpRoot = ($env:CVC_DEPS_PREFIX -replace '\\', '/')

# Exactly one of shared/static, matching how gmp is built: on MinGW, MPFR's
# configure insists that a DLL gmp gets a DLL mpfr and a static gmp a static
# one.  Extras win over the helper's defaults because they come last.
$linkMode = if ($env:CVC_LINK -eq 'static') {
    @('--enable-static', '--disable-shared')
} else {
    @('--enable-shared', '--disable-static')
}

# -static-libgcc: unlike gmp, MPFR calls into libgcc -- emutls for its C11
# thread-local state (MPFR_USE_C11_THREAD_SAFE), and the soft-float routines
# behind its _Float128 and decimal-float conversions.  With a plain -shared
# link gcc resolves those against libgcc_s, so libmpfr-6.dll imported
# libgcc_s_seh-1.dll, which nothing ships beside it.  Linked statically, the
# DLL carries its own copy (GCC runtime library exception; no MinGW runtime
# DLL for consumers to ship).  libtool passes -static-* through to gcc.
Invoke-CvcMsysAutotoolsBuild -ConfigureArgs (@("--with-gmp='$gmpRoot'") + $linkMode + @("LDFLAGS='-static-libgcc'"))

# -- What did the build install? ---------------------------------------------
$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include'

# The DLL names a PE file imports, from `dumpbin /dependents`: the indented bare
# file names only (the "Dump of file C:\...\x.dll" banner also ends in .dll).
function Get-CvcMpfrImports([string]$Path) {
    $lines = & dumpbin /nologo /dependents $Path
    if ($LASTEXITCODE -ne 0) { throw "mpfr: dumpbin /dependents $Path failed" }
    $lines | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '(?i)^[^\s\\/:]+\.dll$' }
}

# libtool's .la files hold the absolute, ephemeral build prefix and nothing an
# MSVC consumer reads.
Get-ChildItem -LiteralPath $lib -File -Filter '*.la' -ErrorAction SilentlyContinue |
    Remove-Item -Force

Write-Host '-- mpfr: installed tree --'
Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -File | ForEach-Object {
    Write-Host ("  {0}  ({1} bytes)" -f $_.FullName.Substring($env:CVC_INSTALL_DIR.Length).TrimStart('\', '/'), $_.Length)
}

$implib = Join-Path $lib 'mpfr.lib'
foreach ($required in @((Join-Path $inc 'mpfr.h'), $implib)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "mpfr: $required was not installed" }
}
# The bundle is mpfr alone: no copy of gmp, nothing from vcpkg.
$foreignFiles = @(Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -Force |
    Where-Object { $_.Name -match '(?i)vcpkg' -or $_.Name -match '(?i)^(lib)?gmp' })
if ($foreignFiles.Count -gt 0) { throw "mpfr: unexpected file in the install tree: $($foreignFiles[0].FullName)" }

$dll = $null
if ($env:CVC_LINK -ne 'static') {
    $dll = Join-Path $bin 'libmpfr-6.dll'
    if (-not (Test-Path -LiteralPath $dll)) { throw "mpfr: $dll was not installed" }

    # The OS, the UCRT and gmp's DLL, nothing else: no MinGW runtime
    # (libgcc_s_seh-1, libwinpthread-1, libmcfgthread-2), which nothing ships
    # beside it, and no msvcrt.dll, a second C runtime in an MSVC consumer.
    $deps = @(Get-CvcMpfrImports $dll)
    Write-Host "mpfr: bin\libmpfr-6.dll imports: $($deps -join ', ')"
    $foreign = @($deps | Where-Object { $_ -notmatch '(?i)^(kernel32\.dll|api-ms-win-crt-[a-z0-9-]+\.dll|ucrtbase\.dll|libgmp-10\.dll)$' })
    if ($foreign.Count -gt 0) {
        # Say which symbols each disallowed DLL supplies: that names the cause.
        $imports = & dumpbin /nologo /imports $dll
        foreach ($f in $foreign) {
            $in = $false
            $syms = foreach ($line in $imports) {
                $t = $line.Trim()
                if ($t -ieq $f) { $in = $true; continue }
                if ($in -and $t -match '(?i)^[^\s]+\.dll$') { $in = $false }
                if ($in -and $t -match '^[0-9A-Fa-f]+\s+(\S+)$') { $Matches[1] }
            }
            Write-Host "mpfr: symbols imported from ${f}: $($syms -join ', ')"
        }
        throw "mpfr: libmpfr-6.dll imports [$($foreign -join ', ')]; only KERNEL32, the UCRT and libgmp-10.dll are allowed"
    }
    if (@($deps | Where-Object { $_ -ieq 'libgmp-10.dll' }).Count -ne 1) {
        throw "mpfr: libmpfr-6.dll does not import libgmp-10.dll (imports: $($deps -join ', '))"
    }
}

# -- Smoke test: an MSVC consumer ----------------------------------------------
# Compile with cl /MD against the installed mpfr.h (and gmp.h from the deps
# prefix), link mpfr.lib + gmp.lib, run it with both DLLs beside the exe, the
# way a consumer ships them.  Built under CVC_BUILD_DIR, never the install dir.
# A static build is not smoke-tested, for the reason recipes/gmp/build.ps1
# gives (MinGW archives need libgcc, which an MSVC link does not provide).
if (-not $dll) {
    Write-Host 'mpfr: NOTE: --link static -- MSVC smoke test skipped (see build.ps1)'
} else {
    if (-not (Test-Path -LiteralPath $gmpDll)) { throw "mpfr: $gmpDll not found in the deps prefix" }
    $smoke = Join-Path $env:CVC_BUILD_DIR 'mpfr-smoke'
    if (Test-Path -LiteralPath $smoke) { Remove-Item -Recurse -Force -LiteralPath $smoke }
    New-Item -ItemType Directory -Force -Path $smoke | Out-Null
    $src = @'
#include <stdio.h>
#include <string.h>
#include <gmp.h>
#include <mpfr.h>

int main(void) {
    char buf[64];
    mpfr_t x;
    int ok;

    mpfr_init2(x, 200);
    mpfr_const_pi(x, MPFR_RNDN);
    mpfr_snprintf(buf, sizeof buf, "%.30Rf", x);
    ok = strcmp(buf, "3.141592653589793238462643383280") == 0;
    printf("mpfr-smoke: mpfr %s (gmp %s), pi = %s\n", mpfr_get_version(), gmp_version, buf);

    /* sqrt(2)^2 - 2 is zero to within a few ulps at 200 bits */
    mpfr_set_ui(x, 2, MPFR_RNDN);
    mpfr_sqrt(x, x, MPFR_RNDN);
    mpfr_sqr(x, x, MPFR_RNDN);
    mpfr_sub_ui(x, x, 2, MPFR_RNDN);
    ok = ok && (mpfr_zero_p(x) || mpfr_get_exp(x) < -190);

    /* A FILE* from this (MSVC, UCRT) side, written by the DLL. */
    mpfr_const_log2(x, MPFR_RNDN);
    fputs("mpfr-smoke: mpfr_out_str(stdout) log 2 = ", stdout);
    ok = ok && mpfr_out_str(stdout, 10, 20, x, MPFR_RNDN) > 0;
    fputs("\n", stdout);
    fflush(stdout);

    mpfr_clear(x);
    mpfr_free_cache();
    printf("mpfr-smoke: %s\n", ok ? "OK" : "FAILED");
    return ok ? 0 : 1;
}
'@
    Set-Content -LiteralPath (Join-Path $smoke 'smoke.c') -Value $src -Encoding Ascii
    Copy-Item -LiteralPath $dll    -Destination $smoke
    Copy-Item -LiteralPath $gmpDll -Destination $smoke

    Push-Location $smoke
    try {
        & cl /nologo /W3 /O2 /MD "/I$inc" "/I$depsInc" smoke.c /Fe:smoke.exe /link $implib (Join-Path $depsLib 'gmp.lib')
        if ($LASTEXITCODE -ne 0) { throw 'mpfr: smoke.c does not compile/link with cl /MD against mpfr.h + mpfr.lib + gmp.lib' }
        $exeDeps = @(Get-CvcMpfrImports (Join-Path $smoke 'smoke.exe'))
        Write-Host "mpfr: smoke.exe imports: $($exeDeps -join ', ')"
        if (@($exeDeps | Where-Object { $_ -ieq 'libmpfr-6.dll' }).Count -ne 1) {
            throw 'mpfr: smoke.exe does not import libmpfr-6.dll -- lib\mpfr.lib binds to the wrong DLL name'
        }
        $out = & .\smoke.exe
        $rc = $LASTEXITCODE
        $out | ForEach-Object { Write-Host $_ }
        if ($rc -ne 0) { throw "mpfr: smoke.exe failed (exit $rc)" }
    } finally {
        Pop-Location
    }
    Write-Host 'mpfr: OK -- an MSVC (cl) consumer compiles against mpfr.h + gmp.h, links mpfr.lib + gmp.lib and runs'
}

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
