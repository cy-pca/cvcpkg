# recipes/protobuf/build-wasm.ps1 — cross-compile libprotobuf to wasm (see build-wasm.sh).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

# wasm-mt (the matrix entry sets CVC_WASM_THREADS=1): -pthread on every object, because wasm-ld
# refuses to link pthread and non-pthread objects together. The linux script gets the same flags
# from env-wasm.sh's CVC_WASM_THREADS hook; env-wasm.ps1 has none (joltphysics does the same).
$threadArgs = @()
if ($env:CVC_WASM_THREADS -eq '1') {
    $threadArgs = @('-DCMAKE_C_FLAGS=-pthread', '-DCMAKE_CXX_FLAGS=-pthread')
    Write-Host '-- CVC_WASM_THREADS=1: building protobuf with -pthread (wasm-mt) --'
}

Invoke-CvcWasmCMakeBuild -ExtraArgs (@(
    '-Dprotobuf_BUILD_TESTS=OFF',
    '-Dprotobuf_BUILD_EXAMPLES=OFF',
    '-Dprotobuf_BUILD_CONFORMANCE=OFF',
    '-Dprotobuf_BUILD_PROTOC_BINARIES=OFF',
    '-Dprotobuf_BUILD_LIBPROTOC=OFF',
    '-Dprotobuf_BUILD_LIBUPB=OFF',
    '-Dprotobuf_ABSL_PROVIDER=package',
    '-Dprotobuf_WITH_ZLIB=ON',
    '-DCMAKE_CXX_STANDARD=17'
) + $threadArgs)
