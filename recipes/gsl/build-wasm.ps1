# recipes/gsl/build-wasm.ps1 — cross-compile GSL to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Push-Location $env:CVC_SOURCE_DIR
try {
    & emconfigure bash ./configure `
        --prefix="$env:CVC_INSTALL_DIR" `
        --host=none-none-none `
        --disable-shared `
        --enable-static `
        --with-pic
    if ($LASTEXITCODE -ne 0) { throw "configure failed" }

    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & emmake make install
    if ($LASTEXITCODE -ne 0) { throw "make install failed" }
}
finally {
    Pop-Location
}
