# recipes/imagemagick/build-wasm.ps1 — cross-compile ImageMagick to wasm via Emscripten.
# Minimal build: no X11, no external codecs, no threads.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$msysPrefix = ConvertTo-MsysPath $env:CVC_INSTALL_DIR
$msysSourceDir = ConvertTo-MsysPath $env:CVC_SOURCE_DIR

# Run configure via bash -c with explicit cd so that emconfigure's Python
# subprocess finds the script regardless of working-directory differences.
& emconfigure bash -c "cd '$msysSourceDir' && ./configure --prefix='$msysPrefix' --host=none-none-none --disable-shared --enable-static --with-quantum-depth=16 --enable-hdri --with-magick-plus-plus --without-perl --without-x --without-jpeg --without-png --without-webp --without-jbig --without-raw --without-openjp2 --without-threads --disable-docs"
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
