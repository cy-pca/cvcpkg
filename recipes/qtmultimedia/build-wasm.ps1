# recipes/qtmultimedia/build-wasm.ps1 — cross-compile Qt Multimedia for
# wasm_singlethread using Emscripten on a Windows host.  Uses Qt's WASM
# media backend; desktop backends (ffmpeg/gstreamer/pulseaudio/pipewire)
# are disabled explicitly.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-wasm.ps1"

$hostQt = Join-Path $env:CVC_DEPS_PREFIX 'host-qt'
$hostQmake = Join-Path $hostQt 'bin\qmake6.exe'
$hostQmakeLibexec = Join-Path $hostQt 'libexec\qmake6.exe'
if (-not (Test-Path $hostQmake) -and -not (Test-Path $hostQmakeLibexec)) {
    Write-Error "expected host Qt at $hostQt (from qt6 wasm bundle) — is the installed qt6 bundle at least cvc.3?"
}

& cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DBUILD_SHARED_LIBS=OFF `
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain" `
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX" `
    "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX" `
    "-DQT_HOST_PATH=$hostQt" `
    -DFEATURE_ffmpeg=OFF `
    -DFEATURE_gstreamer=OFF `
    -DFEATURE_pulseaudio=OFF `
    -DFEATURE_pipewire=OFF `
    -DQT_BUILD_EXAMPLES=OFF `
    -DQT_BUILD_TESTS=OFF `
    -DQT_BUILD_BENCHMARKS=OFF
if ($LASTEXITCODE -ne 0) { throw "qtmultimedia wasm cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "qtmultimedia wasm build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "qtmultimedia wasm install failed" }

Invoke-CvcRewriteInstallPaths
