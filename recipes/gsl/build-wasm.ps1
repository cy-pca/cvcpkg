# recipes/gsl/build-wasm.ps1 — cross-compile GSL to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR
$msysSourceDir = ConvertTo-MsysPath $env:CVC_SOURCE_DIR

# Run configure via Git Bash (not bare 'bash', which resolves to WSL on Windows
# due to CreateProcess searching System32 before PATH).
& emconfigure $gitBash -c "$emToolExports && cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --disable-shared --enable-static --with-pic"
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
