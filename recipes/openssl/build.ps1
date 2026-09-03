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

# Ensure a WORKING perl is on PATH before MSYS2/Git-for-Windows perl, which
# lacks IPC::Cmd and Params::Check and makes OpenSSL Configure fail.
#
# Prefer cvcpkg's OWN perl. `perl` is a declared build dependency of this
# recipe precisely so the version is pinned, and recipes/perl stages Strawberry
# 5.40.2 portable into <prefix>/lib/strawberry with a bin/perl.cmd launcher.
#
# This used to hardcode C:\Strawberry\perl\bin unconditionally, which put
# whatever perl the machine happens to have ahead of the pinned one — the same
# hermeticity violation as picking up C:\Strawberry\perl\bin\pkg-config.bat and
# letting it decide how BLAS links. It broke on 2026-08-16: the system
# Strawberry here is 5.42.2, and under it OpenSSL 3.4.1 never emits the
# generated header include/internal/der.h. The build then died a long way
# downstream compiling the generated der_ecx_gen.c:
#
#     fatal error C1083: Cannot open include file: 'internal/der.h'
#
# A missing generated header is what a wrong-perl looks like from the compiler.
# With the pinned 5.40.2 the header is generated and the build completes.
#
# NB: the screenfuls of "Use of uninitialized value in join or string at
# re.pm line 47" are NOT the symptom -- they appear under 5.40.2 too. They are
# ordinary OpenSSL 3.4.1 noise, and reading them as the tell sends you after
# the wrong thing.
#
# @() around the whole pipeline, not just the literal: Where-Object unwraps a
# single result to a bare string, and $string[0] is "C" -- which silently
# prepended a one-character directory to PATH instead of the prefix bin.
$perlDirs = @(
    @(
        (Join-Path $env:CVC_BUILD_PREFIX 'bin'),
        (Join-Path $env:CVC_DEPS_PREFIX  'bin')
    ) | Where-Object { $_ -and (Test-Path (Join-Path $_ 'perl.cmd')) }
)

if ($perlDirs) {
    $env:PATH = "$($perlDirs[0]);$env:PATH"
    Write-Host "openssl: using cvcpkg perl from $($perlDirs[0])"
} else {
    # Fall back to the system install, but say so — an unpinned toolchain
    # deciding how a crypto library builds should never be silent.
    $strawberry = 'C:\Strawberry\perl\bin'
    if (Test-Path $strawberry) {
        $env:PATH = "$strawberry;$env:PATH"
        Write-Warning "openssl: cvcpkg perl not found in the prefix; falling back to $strawberry (NOT hermetic)"
    }
}
& perl --version | Select-Object -First 2 | ForEach-Object { if ($_) { Write-Host "openssl: $_" } }

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
