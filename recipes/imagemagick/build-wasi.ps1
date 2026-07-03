# recipes/imagemagick/build-wasi.ps1 — cross-compile ImageMagick to wasm32-wasi via wasi-sdk on Windows.
# Minimal build: no X11, no external codecs, Q16-HDRI quantum, no threads.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiAutotoolsBuild -ConfigureArgs @(
    '--with-quantum-depth=16',
    '--enable-hdri',
    '--with-magick-plus-plus',
    '--without-perl',
    '--without-x',
    '--without-jpeg',
    '--without-png',
    '--without-webp',
    '--without-jbig',
    '--without-raw',
    '--without-openjp2',
    '--without-threads',
    '--without-openmp',
    '--without-modules',
    '--disable-docs'
)

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
