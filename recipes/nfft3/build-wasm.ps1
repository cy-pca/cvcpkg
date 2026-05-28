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

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR
$msysFftwPrefix = ConvertTo-MsysPath $fftwPrefix
$msysSourceDir = ConvertTo-MsysPath $env:CVC_SOURCE_DIR

# Run configure via bash -c with explicit cd so that emconfigure's Python
# subprocess finds the script regardless of working-directory differences.
& emconfigure bash -c "cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --disable-shared --enable-static --with-pic --disable-examples --disable-applications --disable-openmp --with-fftw3-includedir='$msysFftwPrefix/include' --with-fftw3-libdir='$msysFftwPrefix/lib' CFLAGS='-O3 -ffast-math'"
if ($LASTEXITCODE -ne 0) { throw "configure failed" }

Push-Location $env:CVC_SOURCE_DIR
try {
    & emmake make -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & emmake make install
    if ($LASTEXITCODE -ne 0) { throw "make install failed" }
}
finally {
    Pop-Location
}
