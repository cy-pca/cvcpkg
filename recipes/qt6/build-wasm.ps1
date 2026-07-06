# recipes/qt6/build-wasm.ps1 — cross-compile Qt 6 Base for
# wasm_singlethread using Emscripten on a Windows host.
#
# This script:
#   1. Builds a minimal native "host Qt" (moc, rcc, uic) from the
#      same source tree.
#   2. Activates the Emscripten SDK from the emsdk bundle.
#   3. Configures Qt for the wasm-emscripten target with threads off.
#   4. Installs the WASM libraries + the native host tools.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-wasm.ps1"

# --- Step 1: Build a native host Qt (needed for moc/rcc/uic). ---
$hostBuildDir = "$env:CVC_BUILD_DIR\host-qt"
$hostInstallDir = "$env:CVC_BUILD_DIR\host-qt-install"

& cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $hostBuildDir `
    "-DCMAKE_INSTALL_PREFIX=$hostInstallDir" `
    -DCMAKE_BUILD_TYPE=Release `
    -DBUILD_SHARED_LIBS=OFF `
    -DFEATURE_icu=OFF `
    -DQT_BUILD_EXAMPLES=OFF `
    -DQT_BUILD_TESTS=OFF `
    -DQT_BUILD_BENCHMARKS=OFF
if ($LASTEXITCODE -ne 0) { throw "host Qt cmake configure failed" }

& cmake --build $hostBuildDir -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "host Qt build failed" }

& cmake --install $hostBuildDir
if ($LASTEXITCODE -ne 0) { throw "host Qt install failed" }

# --- Step 2: Configure Qt for wasm_singlethread. ---
$wasmBuildDir = "$env:CVC_BUILD_DIR\wasm-qt"

& cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $wasmBuildDir `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DBUILD_SHARED_LIBS=OFF `
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain" `
    "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX" `
    "-DQT_HOST_PATH=$hostInstallDir" `
    -DFEATURE_thread=OFF `
    -DFEATURE_icu=OFF `
    -DQT_BUILD_EXAMPLES=OFF `
    -DQT_BUILD_TESTS=OFF `
    -DQT_BUILD_BENCHMARKS=OFF `
    -DINPUT_opengl=es2 `
    -DFEATURE_sql_mysql=OFF `
    -DFEATURE_sql_psql=OFF
if ($LASTEXITCODE -ne 0) { throw "wasm Qt cmake configure failed" }

# --- Step 3: Build and install. ---
& cmake --build $wasmBuildDir -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "wasm Qt build failed" }

& cmake --install $wasmBuildDir
if ($LASTEXITCODE -ne 0) { throw "wasm Qt install failed" }

# Copy native host tools into the install prefix for convenience.
foreach ($tool in @('moc.exe', 'rcc.exe', 'uic.exe', 'qmake6.exe')) {
    foreach ($dir in @('bin', 'libexec')) {
        $src = Join-Path $hostInstallDir "$dir\$tool"
        if (Test-Path $src) {
            $dest = Join-Path $env:CVC_INSTALL_DIR 'bin'
            if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest | Out-Null }
            Copy-Item $src $dest -Force
            break
        }
    }
}

# Preserve the full host-native Qt install tree alongside the wasm output
# so downstream Qt submodules (qtshadertools, qtmultimedia, …) can
# cross-compile against this bundle: their build passes QT_HOST_PATH to
# this tree.  Qt's cross-build needs the mkspecs, cmake config files,
# libexec host tools, and internal headers — not just the four binaries
# copied above.
$hostQtDest = Join-Path $env:CVC_INSTALL_DIR 'host-qt'
Copy-Item -Recurse -Force $hostInstallDir $hostQtDest

Write-Host "Qt 6 wasm_singlethread installed to $env:CVC_INSTALL_DIR"

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
