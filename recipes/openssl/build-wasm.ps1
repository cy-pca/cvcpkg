# recipes/openssl/build-wasm.ps1 — cross-compile OpenSSL to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

# OpenSSL uses perl Configure (not autotools).
# We use Strawberry Perl (fully-featured) from PowerShell directly, but
# OpenSSL's Configurations/unix-checker.pm rejects perls whose
# File::Spec::rel2abs() produces backslash paths.  Patch it out so
# Strawberry Perl can drive Configure for the linux-generic32 target.
$unixChecker = Join-Path $env:CVC_SOURCE_DIR 'Configurations\unix-checker.pm'
if (Test-Path $unixChecker) {
    Set-Content -Path $unixChecker -Value "1;"
}

# Use forward-slash paths for prefix/openssldir since they end up in Makefiles.
$installDir = $env:CVC_INSTALL_DIR -replace '\\','/'
$emscriptenDir = Join-Path $env:CVC_EMSDK_DIR 'upstream\emscripten'

Push-Location $env:CVC_SOURCE_DIR
try {
    $env:CC  = Join-Path $emscriptenDir 'emcc.bat'
    $env:CXX = Join-Path $emscriptenDir 'em++.bat'
    $env:AR  = Join-Path $emscriptenDir 'emar.bat'
    $env:RANLIB = Join-Path $emscriptenDir 'emranlib.bat'

    # Use Strawberry Perl explicitly — env-wasm.ps1 prepends Git's usr/bin
    # to PATH which shadows Strawberry's perl with the minimal MSYS2 perl.
    $strawberryPerl = 'C:\Strawberry\perl\bin\perl.exe'
    if (-not (Test-Path $strawberryPerl)) {
        $strawberryPerl = (Get-Command perl -ErrorAction Stop |
            Where-Object { $_.Source -notmatch 'Git' } |
            Select-Object -First 1).Source
    }

    # Set PERL env with forward slashes so the generated Makefile uses paths
    # that sh.exe won't mangle (backslashes get stripped by MSYS sh).
    $env:PERL = $strawberryPerl -replace '\\','/'

    & $strawberryPerl Configure linux-generic32 `
        "--prefix=$installDir" `
        "--openssldir=$installDir/ssl" `
        no-shared no-asm no-threads no-engine no-dso no-tests no-apps -DNO_FORK
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL Configure failed" }

    # mingw32-make auto-detects sh.exe on PATH and uses it as SHELL.
    # sh.exe strips backslashes from paths (e.g. util\dofile.pl becomes
    # utildofile.pl).  Convert path-separator backslashes in the
    # generated Makefile to forward slashes.  Do NOT modify
    # configdata.pm — that triggers OpenSSL's Makefile regeneration
    # which aborts the build.
    $makefilePath = Join-Path $env:CVC_SOURCE_DIR 'makefile'
    if (Test-Path $makefilePath) {
        (Get-Content $makefilePath -Raw) -replace '(?<=[\w.:])\\(?=[\w.])', '/' |
            Set-Content $makefilePath -NoNewline
    }

    # sh.exe (MSYS) has a short ARG_MAX that may cause "command line is
    # too long" when AR links hundreds of .o files.  If that happens,
    # we post-process the Makefile to use $(file ...) response files.
    # For now, try the build with sh.exe on PATH — OpenSSL Makefile
    # recipes use shell constructs (if/fi) that require sh.
    & mingw32-make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & mingw32-make install_sw
    if ($LASTEXITCODE -ne 0) { throw "make install_sw failed" }
}
finally {
    Pop-Location
}
