# recipes/openssl/build.ps1 — build OpenSSL from source on Windows.
#
# OpenSSL uses its own Perl-based Configure + nmake build system,
# NOT CMake.  Strawberry Perl and nmake (via MSVC dev env) must be on PATH.
$ErrorActionPreference = 'Stop'

# Import the MSVC developer environment, exactly as the other nmake recipes do
# (bzip2, sqlite, f2c, pthreads4w).
#
# This was omitted because nmake "is available on GitHub-hosted Windows
# runners" — true there, since the workflow runs ilammy/msvc-dev-cmd before
# invoking us. It is NOT true on a self-hosted builder, whose daemon runs from
# a plain service session with no dev env, so the build died at:
#
#     The term 'nmake' is not recognized as a name of a cmdlet, ...
#
# That is why windows openssl was stranded at +cvc.1 while this recipe sat at
# cvc_revision 3 — the only variants that ever published were built back when a
# GitHub-hosted runner happened to supply the environment. The failure then
# cascade-cancelled curl, which kept curl/freetype/libpng missing for windows
# and left libcvc's package-windows CI red.
#
# Sourcing this is sufficient: env-windows.ps1 calls Import-CvcMsvcEnv at load,
# which no-ops when cl.exe is already on PATH (the GitHub-runner case) and
# otherwise imports vcvars64.bat located via vswhere.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

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

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
