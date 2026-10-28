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

# no-uplink: libcrypto uses the FILE* it is handed directly.  With uplink (the
# VC-WIN64A default) every FILE*-based call goes through OPENSSL_Applink, which
# the calling EXECUTABLE must export, and the process exits with
# "OPENSSL_Uplink(...): no OPENSSL_Applink" when it does not -- CPython's
# _ssl.load_dh_params did exactly that.  Uplink only matters when OpenSSL and
# its caller use different C runtimes; everything here links the UCRT (/MD).
# CPython builds the OpenSSL it ships the same way (PCbuild\openssl.vcxproj).
#
# --prefix and --openssldir are compiled into libcrypto as OPENSSLDIR (where it
# reads openssl.cnf, which can load any DLL), ENGINESDIR and MODULESDIR (where
# it loads providers such as "legacy").  They used to be the job's install dir,
# so every machine with this package looked for its config and providers in a
# builder's temp tree.  Under a builder job dir like C:\cvb\... that is a path
# any local user can create, then plant an openssl.cnf for every process using
# this libcrypto to load (CVE-2019-1552 is the same flaw in mingw builds).  Use
# OpenSSL's own Windows defaults instead, which only administrators can write
# and which python.org's libcrypto also uses, and install through DESTDIR.
$prefix = 'C:\Program Files\OpenSSL'
$openssldir = 'C:\Program Files\Common Files\SSL'
$stage = Join-Path $env:CVC_BUILD_DIR 'destdir'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
Push-Location $env:CVC_SOURCE_DIR
try {
    & perl Configure $target `
        --prefix="$prefix" `
        --openssldir="$openssldir" `
        $sharedFlag `
        no-uplink `
        no-tests
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL Configure failed" }

    & nmake /NOLOGO
    if ($LASTEXITCODE -ne 0) { throw "nmake build failed" }

    # windows-makefile.tmpl drops the drive letter under DESTDIR, so this lands
    # in $stage\Program Files\OpenSSL and $stage\Program Files\Common Files\SSL.
    & nmake /NOLOGO install_sw install_ssldirs "DESTDIR=$stage"
    if ($LASTEXITCODE -ne 0) { throw "nmake install failed" }
}
finally {
    Pop-Location
}

# The prefix tree is the package root; OPENSSLDIR's files (openssl.cnf,
# misc\CA.pl, ...) ship in ssl\ as before.
New-Item -ItemType Directory -Force -Path $env:CVC_INSTALL_DIR | Out-Null
Copy-Item -Recurse -Force (Join-Path $stage ($prefix.Substring(2) + '\*')) $env:CVC_INSTALL_DIR
$sslDest = Join-Path $env:CVC_INSTALL_DIR 'ssl'
New-Item -ItemType Directory -Force -Path $sslDest | Out-Null
Copy-Item -Recurse -Force (Join-Path $stage ($openssldir.Substring(2) + '\*')) $sslDest

# c_rehash.pl puts "$prefix/bin" first on PATH to find openssl.exe. Point it at
# the openssl.exe beside it rather than one in C:\Program Files\OpenSSL. $dir
# (OPENSSLDIR, the default certs directory) stays: it is what libcrypto reads.
# It also looks for a file named "openssl" in each PATH directory, which never
# exists on Windows, so without OPENSSL=openssl.exe it always skipped
# rehashing; name the .exe. And Strawberry Perl implements symlink(), so it
# chose symlinks, which need a privilege most Windows users lack; the failure
# went unreported (`symlink $f, $h || warn` binds || to $h) and the hash links
# never appeared. Copy, its fallback where symlinks are missing.
$rehash = Join-Path $env:CVC_INSTALL_DIR 'bin\c_rehash.pl'
$text = [IO.File]::ReadAllText($rehash)
$edits = @(
    @('(?m)^my \$prefix = "[^"\r\n]*";', 'use FindBin (); my $$prefix = "$$FindBin::RealBin/..";'),
    @('(?m)^my \$openssl = \$ENV\{OPENSSL\} \|\| "openssl";', 'my $$openssl = $$ENV{OPENSSL} || "openssl.exe";'),
    @('(?m)^my \$symlink_exists=eval \{symlink\("",""\); 1\};', 'my $$symlink_exists=0;')
)
foreach ($e in $edits) {
    if ([regex]::Matches($text, $e[0]).Count -ne 1) { throw "openssl: c_rehash.pl does not have exactly one line matching $($e[0])" }
    $text = [regex]::Replace($text, $e[0], $e[1])
}
[IO.File]::WriteAllText($rehash, $text)

# The installed openssl.exe must report the fixed directories, and no file in
# the package may name the build tree (PDBs record source and object paths
# for the debugger, which is what they are for).
$dirs = & (Join-Path $env:CVC_INSTALL_DIR 'bin\openssl.exe') version -d -e -m
if ($LASTEXITCODE -ne 0) { throw "openssl: the installed openssl.exe does not run" }
$dirs | ForEach-Object { Write-Host "openssl: $_" }
$want = @(
    "OPENSSLDIR: `"$openssldir`"",
    "ENGINESDIR: `"$prefix\lib\engines-3`"",
    "MODULESDIR: `"$prefix\lib\ossl-modules`""
)
foreach ($w in $want) {
    if ($dirs -notcontains $w) { throw "openssl: expected '$w' from openssl version -d -e -m" }
}
$buildRoots = @($env:CVC_INSTALL_DIR, $env:CVC_BUILD_DIR, $stage) | Where-Object { $_ } |
    ForEach-Object { $_.TrimEnd('\', '/') } | ForEach-Object { $_; $_ -replace '\\', '/'; $_ -replace '\\', '\\' }
$leaks = Get-ChildItem -Recurse -File $env:CVC_INSTALL_DIR | Where-Object { $_.Extension -ne '.pdb' } | Where-Object {
    $bytes = [IO.File]::ReadAllBytes($_.FullName)
    $s = [Text.Encoding]::GetEncoding(28591).GetString($bytes)
    $hit = $false
    foreach ($r in $buildRoots) { if ($s.IndexOf($r, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $hit = $true; break } }
    $hit
}
if ($leaks) { throw "openssl: files name the build tree: $(($leaks | ForEach-Object { $_.FullName }) -join ', ')" }

# The license travels with the DLLs, as Apache-2.0 asks.  CPython's Windows
# bundle ships libcrypto/libssl in DLLs\ and appends this text to its
# LICENSE.txt (_common/python-externals.targets), as python.org's does.
$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\openssl'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Copy-Item (Join-Path $env:CVC_SOURCE_DIR 'LICENSE.txt') $lic

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
