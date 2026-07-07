# recipes/x265-cli/build.ps1 — build x265 encoder CLI on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Import-CvcMsvcEnv

# Put nasm and the prebuilt x265 library on PATH / search paths.
if ($env:CVC_DEPS_PREFIX) {
    $env:PATH = "$env:CVC_DEPS_PREFIX\bin;$env:PATH"
}

& cmake -G Ninja `
    -S "$env:CVC_SOURCE_DIR\source" `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX" `
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX" `
    -DENABLE_CLI=ON `
    -DENABLE_SHARED=OFF `
    -DENABLE_STATIC=OFF `
    -DENABLE_TESTS=OFF
if ($LASTEXITCODE -ne 0) { throw 'x265-cli cmake configure failed' }

& cmake --build $env:CVC_BUILD_DIR --target x265 -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'x265-cli cmake build failed' }

$binary = Get-ChildItem $env:CVC_BUILD_DIR -Filter 'x265.exe' | Select-Object -First 1
if (-not $binary) { throw 'x265.exe not found after build' }
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
Copy-Item $binary.FullName "$env:CVC_INSTALL_DIR\bin\x265.exe"
