# recipes/libsodium/build-wasm.ps1 -- cross-compile libsodium to wasm with
# Emscripten on a Windows host: build-wasm.sh's configuration, with configure
# and make run through Git Bash (env-wasm.ps1).  Single-threaded only:
# env-wasm.ps1 has no threaded mode, so wasm-mt is build-wasm.sh's.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR
$msysSourceDir = ConvertTo-MsysPath $env:CVC_SOURCE_DIR

$env:CFLAGS = "$env:CFLAGS -O3".Trim()

& emconfigure $gitBash -c "$emToolExports && cd '$msysSourceDir' && sh ./configure --prefix='$msysPrefix' --disable-shared --enable-static --disable-dependency-tracking --disable-ssp --disable-asm --disable-pie --without-pthreads"
if ($LASTEXITCODE -ne 0) {
    $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'config.log'
    if (Test-Path $cfgLog) { Write-Host '--- config.log (last 60 lines) ---'; Get-Content $cfgLog -Tail 60 }
    throw "libsodium: configure failed"
}

Push-Location $env:CVC_SOURCE_DIR
try {
    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "libsodium: make failed" }
    & emmake make install
    if ($LASTEXITCODE -ne 0) { throw "libsodium: make install failed" }
}
finally {
    Pop-Location
}

Remove-Item -LiteralPath (Join-Path $env:CVC_INSTALL_DIR 'lib\libsodium.la') -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath (Join-Path $env:CVC_INSTALL_DIR 'lib\libsodium.a'))) {
    throw "libsodium: lib\libsodium.a not installed"
}

# The same CMake package as build-wasm.sh writes.
$msysScript = ConvertTo-MsysPath (Join-Path $scriptDir 'sodium-cmake-config.sh')
& $gitBash -c "source '$msysScript' && sodium_write_cmake_config '$msysPrefix' libsodium.a STATIC"
if ($LASTEXITCODE -ne 0) { throw "libsodium: writing lib/cmake/sodium failed" }

Invoke-CvcRewriteInstallPaths
