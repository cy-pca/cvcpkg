# recipes/qt6-wasm-singlethread/build.ps1 — cross-compile Qt 6 Base
# for wasm_singlethread on Windows using Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

if (-not $env:CVC_EMSDK_DIR) { throw 'CVC_EMSDK_DIR must point to the activated emsdk bundle' }

# --- Step 1: Build native host Qt (moc/rcc/uic). ---
$hostBuild  = Join-Path $env:CVC_BUILD_DIR 'host-qt'
$hostPrefix = Join-Path $env:CVC_BUILD_DIR 'host-qt-install'

& cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $hostBuild `
    "-DCMAKE_INSTALL_PREFIX=$hostPrefix" `
    '-DCMAKE_BUILD_TYPE=Release' `
    '-DBUILD_SHARED_LIBS=OFF' `
    '-DQT_BUILD_EXAMPLES=OFF' `
    '-DQT_BUILD_TESTS=OFF' `
    '-DQT_BUILD_BENCHMARKS=OFF'
if ($LASTEXITCODE -ne 0) { throw 'host Qt configure failed' }

& cmake --build $hostBuild -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'host Qt build failed' }

& cmake --install $hostBuild
if ($LASTEXITCODE -ne 0) { throw 'host Qt install failed' }

# --- Step 2: Activate Emscripten. ---
& "$env:CVC_EMSDK_DIR\emsdk_env.bat"

$emscriptenToolchain = Join-Path $env:CVC_EMSDK_DIR 'upstream\emscripten\cmake\Modules\Platform\Emscripten.cmake'

# --- Step 3: Configure Qt for wasm_singlethread. ---
$wasmBuild = Join-Path $env:CVC_BUILD_DIR 'wasm-qt'

& cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $wasmBuild `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    '-DCMAKE_BUILD_TYPE=Release' `
    '-DBUILD_SHARED_LIBS=OFF' `
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain" `
    "-DQT_HOST_PATH=$hostPrefix" `
    '-DFEATURE_thread=OFF' `
    '-DQT_BUILD_EXAMPLES=OFF' `
    '-DQT_BUILD_TESTS=OFF' `
    '-DQT_BUILD_BENCHMARKS=OFF' `
    '-DINPUT_opengl=es2' `
    '-DFEATURE_sql_mysql=OFF' `
    '-DFEATURE_sql_psql=OFF'
if ($LASTEXITCODE -ne 0) { throw 'WASM Qt configure failed' }

# --- Step 4: Build and install. ---
& cmake --build $wasmBuild -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'WASM Qt build failed' }

& cmake --install $wasmBuild
if ($LASTEXITCODE -ne 0) { throw 'WASM Qt install failed' }

# Copy native host tools into install prefix.
foreach ($tool in @('moc.exe', 'rcc.exe', 'uic.exe', 'qmake6.exe')) {
    $src = Join-Path $hostPrefix "bin\$tool"
    if (Test-Path $src) {
        Copy-Item $src "$env:CVC_INSTALL_DIR\bin\$tool" -Force
    }
}

Write-Host "Qt 6 wasm_singlethread installed to $env:CVC_INSTALL_DIR"
