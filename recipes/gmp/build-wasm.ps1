# recipes/gmp/build-wasm.ps1 — cross-compile GMP to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR
$msysSourceDir = ConvertTo-MsysPath $env:CVC_SOURCE_DIR

# GMP cross-compilation needs CC_FOR_BUILD for host-side code generators.
# On Windows there is no 'cc'; use gcc from Git/MSYS2 mingw.
$env:CC_FOR_BUILD = if ($env:CVC_HOST_CC) { $env:CVC_HOST_CC } else { 'gcc' }

# Detect the build triplet so configure properly detects cross-compilation.
$buildTriplet = & $gitBash -c "$env:CC_FOR_BUILD -dumpmachine 2>/dev/null || echo x86_64-pc-msys"
$buildTriplet = $buildTriplet.Trim()

# Run configure via Git Bash (not bare 'bash', which resolves to WSL on Windows
# due to CreateProcess searching System32 before PATH).
& emconfigure $gitBash -c "$emToolExports && cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --build='$buildTriplet' --disable-shared --enable-static --enable-cxx --disable-assembly"
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

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
