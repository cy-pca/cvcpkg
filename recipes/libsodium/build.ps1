# recipes/libsodium/build.ps1 -- build libsodium on Windows with MSVC.
#
# Upstream's Windows build is an MSBuild project (builds/msvc/vs2022), and
# cvcpkg's own MSVC (recipes/msvc) has no MSBuild.  msvc/CMakeLists.txt here
# compiles the same sources with the same defines; see its header.  Shared:
# bin/libsodium.dll + lib/libsodium.lib (import library).  Static:
# lib/libsodium.lib against the static CRT, as cvcpkg's other Windows static
# bundles are; its sodium::sodium target carries SODIUM_STATIC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Copy-Item -LiteralPath (Join-Path $scriptDir 'msvc\CMakeLists.txt') `
    -Destination (Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt') -Force

Invoke-CvcCMakeBuild

$lib = Join-Path $env:CVC_INSTALL_DIR 'lib\libsodium.lib'
if (-not (Test-Path -LiteralPath $lib)) { throw "libsodium: lib\libsodium.lib not installed" }
if ($env:CVC_LINK -ne 'static') {
    $dll = Join-Path $env:CVC_INSTALL_DIR 'bin\libsodium.dll'
    if (-not (Test-Path -LiteralPath $dll)) { throw "libsodium: bin\libsodium.dll not installed" }
}
foreach ($h in @('include\sodium.h', 'include\sodium\version.h', 'include\sodium\crypto_box.h')) {
    if (-not (Test-Path -LiteralPath (Join-Path $env:CVC_INSTALL_DIR $h))) { throw "libsodium: $h not installed" }
}
if (Test-Path -LiteralPath (Join-Path $env:CVC_INSTALL_DIR 'include\sodium\private')) {
    throw "libsodium: include\sodium\private installed (internal headers)"
}

# Build check.c (the consumer test.sh runs elsewhere) against what was just
# installed, with the MSVC environment this build already has, and run it:
# test.sh runs on Windows under bash without vcvars.  Shared: link the import
# library and run with bin\ on PATH, so the exe loads this libsodium.dll.
$checkDir = Join-Path $env:CVC_BUILD_DIR 'check'
New-Item -ItemType Directory -Force -Path $checkDir | Out-Null
$exe = Join-Path $checkDir 'check.exe'
$clArgs = @('/nologo', '/O2', '/W3', "/I$(Join-Path $env:CVC_INSTALL_DIR 'include')",
            (Join-Path $scriptDir 'check.c'), "/Fe$exe", "/Fo$checkDir\")
if ($env:CVC_LINK -eq 'static') { $clArgs += @('/MT', '/DSODIUM_STATIC=1') } else { $clArgs += '/MD' }
$clArgs += @('/link', $lib)
& cl @clArgs
if ($LASTEXITCODE -ne 0) { throw "libsodium: check.c did not build ($LASTEXITCODE)" }
$savedPath = $env:PATH
try {
    $env:PATH = "$(Join-Path $env:CVC_INSTALL_DIR 'bin');$env:PATH"
    & $exe
    if ($LASTEXITCODE -ne 0) { throw "libsodium: check.exe failed ($LASTEXITCODE)" }
} finally {
    $env:PATH = $savedPath
}
