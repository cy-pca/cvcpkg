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
$buildTriplet = & bash -c "$env:CC_FOR_BUILD -dumpmachine 2>/dev/null || echo x86_64-pc-msys"
$buildTriplet = $buildTriplet.Trim()

# Run configure via bash with an explicit script path so that emconfigure's
# Python subprocess finds the configure script regardless of working-directory
# differences between PowerShell and the child process.
& emconfigure bash -c "cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --build='$buildTriplet' --disable-shared --enable-static --enable-cxx --disable-assembly"
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
