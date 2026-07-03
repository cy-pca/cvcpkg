# recipes/gmp/build-wasi.ps1 — cross-compile GMP to wasm32-wasi via wasi-sdk.
#
# wasi-sdk is a clang toolchain — no emconfigure needed.
# Invoke-CvcWasiAutotoolsBuild handles the CC/CXX/AR/CFLAGS wiring;
# we just supply GMP's extra configure flags.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

# GMP's configure needs CC_FOR_BUILD (a native cc) for host-side code
# generators like gen-fac.  On the Windows host that lives in MSYS2's
# mingw64 bin dir; we don't need to convert paths because CC_FOR_BUILD
# is looked up by name on the bash PATH.
$env:CC_FOR_BUILD = 'gcc'

Invoke-CvcWasiAutotoolsBuild -Jobs 1 -ConfigureArgs @(
    '--enable-cxx',
    '--disable-assembly'
)
