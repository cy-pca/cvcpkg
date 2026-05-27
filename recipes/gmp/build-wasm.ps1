# recipes/gmp/build-wasm.ps1 — cross-compile GMP to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR

# GMP cross-compilation needs CC_FOR_BUILD for host-side code generators.
$env:CC_FOR_BUILD = if ($env:CVC_HOST_CC) { $env:CVC_HOST_CC } else { 'cc' }

Push-Location $env:CVC_SOURCE_DIR
try {
    & emconfigure bash ./configure `
        --prefix="$msysPrefix" `
        --host=none-none-none `
        --disable-shared `
        --enable-static `
        --enable-cxx `
        --disable-assembly
    if ($LASTEXITCODE -ne 0) { throw "configure failed" }

    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & emmake make install
    if ($LASTEXITCODE -ne 0) { throw "make install failed" }
}
finally {
    Pop-Location
}
