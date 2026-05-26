# recipes/nfft3/build-wasm.ps1 — cross-compile NFFT3 to wasm via Emscripten.
# Threads and OpenMP disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$fftwPrefix = if ($env:CVC_DEPS_PREFIX -and (Test-Path "$env:CVC_DEPS_PREFIX\include")) {
    $env:CVC_DEPS_PREFIX
} else {
    $env:CVC_INSTALL_DIR
}

Push-Location $env:CVC_SOURCE_DIR
try {
    & emconfigure bash ./configure `
        --prefix="$env:CVC_INSTALL_DIR" `
        --host=none-none-none `
        --disable-shared `
        --enable-static `
        --with-pic `
        --disable-examples `
        --disable-applications `
        --disable-openmp `
        "--with-fftw3-includedir=$fftwPrefix\include" `
        "--with-fftw3-libdir=$fftwPrefix\lib" `
        'CFLAGS=-O3 -ffast-math'
    if ($LASTEXITCODE -ne 0) { throw "configure failed" }

    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & emmake make install
    if ($LASTEXITCODE -ne 0) { throw "make install failed" }
}
finally {
    Pop-Location
}
