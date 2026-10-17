# recipes/pegtl/build-wasm.ps1 — install taocpp/PEGTL (header-only) for wasm and
# wasm-mt from a Windows host.  Mirrors build.sh, which the linux-hosted entries
# run through env-wasm.sh / env-wasm-mt.sh.
#
# wasm-mt (the matrix entry sets CVC_WASM_THREADS=1): env-wasm.ps1 has no
# threads hook, so -pthread is added here, as joltphysics does.  The headers do
# not change; it keeps PEGTL's std::filesystem probe in the threaded world.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"
# Not $scriptDir: env-wasm.ps1 reassigns it to _common when dot-sourced.
. "$PSScriptRoot\check-install.ps1"

$threadArgs = @()
if ($env:CVC_WASM_THREADS -eq '1') {
    $threadArgs = @('-DCMAKE_C_FLAGS=-pthread', '-DCMAKE_CXX_FLAGS=-pthread')
    Write-Host '-- CVC_WASM_THREADS=1: probing with -pthread (wasm-mt) --'
}

Invoke-CvcWasmCMakeBuild -ExtraArgs (@(
    '-DPEGTL_BUILD_TESTS=OFF',
    '-DPEGTL_BUILD_EXAMPLES=OFF',
    '-DPEGTL_INSTALL_DOC_DIR=share/licenses/pegtl'
) + $threadArgs)

Test-PegtlInstall
