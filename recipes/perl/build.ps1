# recipes/perl/build.ps1 — stage Strawberry Perl (portable) on Windows.
#
# Building Perl from the CPAN source with MSVC is impractical, so on Windows we
# stage the official Strawberry Perl portable distribution (same 5.40.2 core).
# Pure download — like the other prebuilt recipes it does NOT source
# env-windows.ps1. The interpreter and its @INC live under lib\strawberry\perl;
# a bin\perl.cmd launcher exposes `perl` on the prefix bin/ (which cvcpkg adds
# to PATH). The unix `source` tarball is fetched by cvcpkg but unused here.
$ErrorActionPreference = 'Stop'

$ver = '5.40.2.1'
$url = "https://github.com/StrawberryPerl/Perl-Dist-Strawberry/releases/download/SP_54021_64bit_UCRT/strawberry-perl-$ver-64bit-portable.zip"
$expected = '7707700d5ad027773b775134fe48cd9610abf221433fcfb68c8eb0ec9c6fde8c'

$zip = Join-Path $env:CVC_BUILD_DIR 'strawberry-perl.zip'
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

$dest = Join-Path $env:CVC_INSTALL_DIR 'lib\strawberry'
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force $dest | Out-Null
Expand-Archive -Path $zip -DestinationPath $dest -Force

$perlExe = Join-Path $dest 'perl\bin\perl.exe'
if (-not (Test-Path $perlExe)) { throw "perl.exe not found at $perlExe after extract" }

# bin\perl.cmd launcher -> the real perl.exe (which locates its @INC and
# perl5*.dll relative to its own directory).
$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force $binDir | Out-Null
$launcher = Join-Path $binDir 'perl.cmd'
Set-Content -Path $launcher -Encoding ASCII -Value @(
    '@echo off',
    '"%~dp0..\lib\strawberry\perl\bin\perl.exe" %*'
)

# Smoke check — capture first (piping to Select-Object would early-close the
# pipe and clobber perl's exit code).
$verOut = & $perlExe --version
if ($LASTEXITCODE -ne 0) { throw "staged perl failed to run" }
Write-Host (($verOut | Select-Object -First 2) -join "`n")
