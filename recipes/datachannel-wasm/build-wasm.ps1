# recipes/datachannel-wasm/build-wasm.ps1 -- build datachannel-wasm with Emscripten (Windows host).
# The same build and install as build-wasm.sh.
$ErrorActionPreference = 'Stop'

# Not $scriptDir: env-wasm.ps1 (dot-sourced) sets its own to _common.
$recipeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$recipeDir\..\_common\env-wasm.ps1"

$env:CXXFLAGS = "-fexceptions $env:CXXFLAGS"

& cmake -G Ninja -S $env:CVC_SOURCE_DIR -B $env:CVC_BUILD_DIR `
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType" '-DCMAKE_POLICY_VERSION_MINIMUM=3.5' `
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain"
if ($LASTEXITCODE -ne 0) { throw 'cmake configure failed' }
& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'cmake build failed' }

$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$cfg = Join-Path $lib 'cmake\datachannel-wasm'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include\rtc'
$share = Join-Path $env:CVC_INSTALL_DIR 'share\datachannel-wasm'
foreach ($d in @($cfg, $inc, $share)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
Copy-Item (Join-Path $env:CVC_BUILD_DIR 'libdatachannel-wasm.a') $lib
Copy-Item (Join-Path $env:CVC_SOURCE_DIR 'wasm\include\rtc\*.hpp') $inc
Copy-Item (Join-Path $env:CVC_SOURCE_DIR 'wasm\js\webrtc.js'), (Join-Path $env:CVC_SOURCE_DIR 'wasm\js\websocket.js') $share
Copy-Item (Join-Path $env:CVC_SOURCE_DIR 'LICENSE') (Join-Path $share 'LICENSE')
Copy-Item (Join-Path $recipeDir 'datachannel-wasmConfig.cmake') $cfg
