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

# Detect the build triplet so configure properly detects cross-compilation.
$env:CC_FOR_BUILD = if ($env:CVC_HOST_CC) { $env:CVC_HOST_CC } else { 'gcc' }
$buildTriplet = & $gitBash -c "$env:CC_FOR_BUILD -dumpmachine 2>/dev/null || echo x86_64-pc-msys"
$buildTriplet = $buildTriplet.Trim()

# Run configure via Git Bash (not bare 'bash', which resolves to WSL on Windows
# due to CreateProcess searching System32 before PATH).
& emconfigure $gitBash -c "$emToolExports && cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --build='$buildTriplet' --disable-shared --enable-static --with-pic --disable-examples --disable-applications --disable-openmp --with-fftw3-includedir='$msysFftwPrefix/include' --with-fftw3-libdir='$msysFftwPrefix/lib' CFLAGS='-O3 -ffast-math'"
if ($LASTEXITCODE -ne 0) {
    $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'config.log'
    if (Test-Path $cfgLog) { Write-Host '--- config.log (last 60 lines) ---'; Get-Content $cfgLog -Tail 60 }
    throw "configure failed"
}

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
