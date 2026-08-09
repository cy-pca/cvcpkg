# recipes/openssl/build.ps1 — build OpenSSL from source on Windows.
#
# OpenSSL uses its own Perl-based Configure + nmake build system,
# NOT CMake.  Strawberry Perl and nmake (via MSVC dev env) must be
# on PATH — both are available on GitHub-hosted Windows runners.
$ErrorActionPreference = 'Stop'

# Validate required env vars (set by builder.py / env-windows.ps1).
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_LINK)        { $env:CVC_LINK = 'shared' }
if (-not $env:CVC_JOBS)        { $env:CVC_JOBS = [Environment]::ProcessorCount }

# Ensure Strawberry Perl is on PATH *before* MSYS2/Git-for-Windows Perl.
# The MSYS2 perl shipped with Git for Windows lacks IPC::Cmd and
# Params::Check, making OpenSSL Configure fail.
$strawberry = 'C:\Strawberry\perl\bin'
if (Test-Path $strawberry) {
    $env:PATH = "$strawberry;$env:PATH"
}

# Remove directories that ship a Unix-style 'link' command (creates
# hard links) which shadows MSVC's link.exe (the linker).  Without
# this, nmake's link step fails: "link: extra operand '/dll'".
$env:PATH = ($env:PATH -split ';' | Where-Object {
    -not ($_ -like '*\Strawberry\c\bin*' -or $_ -like '*\Git\usr\bin*')
}) -join ';'

# Choose the target: VC-WIN64A is 64-bit MSVC on x64
$target = 'VC-WIN64A'

# Static vs shared: OpenSSL uses 'no-shared' for static libs.
$sharedFlag = if ($env:CVC_LINK -eq 'static') { 'no-shared' } else { 'shared' }

Push-Location $env:CVC_SOURCE_DIR
try {
    & perl Configure $target `
        --prefix="$env:CVC_INSTALL_DIR" `
        --openssldir="$env:CVC_INSTALL_DIR\ssl" `
        $sharedFlag `
        no-tests
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL Configure failed" }

    & nmake /NOLOGO
    if ($LASTEXITCODE -ne 0) { throw "nmake build failed" }

    & nmake /NOLOGO install_sw install_ssldirs
    if ($LASTEXITCODE -ne 0) { throw "nmake install failed" }
}
finally {
    Pop-Location
}
