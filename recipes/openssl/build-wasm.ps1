# recipes/openssl/build-wasm.ps1 — cross-compile OpenSSL to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Push-Location $env:CVC_SOURCE_DIR
try {
    & emconfigure perl Configure `
        linux-generic32 `
        --prefix="$env:CVC_INSTALL_DIR" `
        --openssldir="$env:CVC_INSTALL_DIR\ssl" `
        no-shared `
        no-asm `
        no-threads `
        no-engine `
        no-dso `
        no-tests `
        -DNO_FORK
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL Configure failed" }

    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & emmake make install_sw
    if ($LASTEXITCODE -ne 0) { throw "make install_sw failed" }
}
finally {
    Pop-Location
}
