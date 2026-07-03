# recipes/openssl/build-wasi.ps1 — cross-compile OpenSSL to wasm32-wasi via wasi-sdk on Windows.
#
# OpenSSL uses its own Perl Configure system, so we can't reuse the
# generic Invoke-CvcWasiAutotoolsBuild helper.  We manually shell into
# git-bash + MSYS perl and drive Configure with wasi-sdk clang.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysWasiCC  = ConvertTo-CvcMsysPath $env:CC
$msysWasiCXX = ConvertTo-CvcMsysPath $env:CXX
$msysWasiAR  = ConvertTo-CvcMsysPath $env:AR
$msysWasiRAN = ConvertTo-CvcMsysPath $env:RANLIB
$msysSysroot = ConvertTo-CvcMsysPath $wasiSysroot

$env:MSYSTEM         = 'MSYS'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING  = '1'

$jobs = [int]$env:CVC_JOBS
if ($jobs -le 0) { $jobs = 1 }

$wasiTargetFlags = "--target=wasm32-wasip1 --sysroot=$msysSysroot"

$cfg = @(
    "cd '$msysSource' &&",
    "CC='$msysWasiCC'",
    "CXX='$msysWasiCXX'",
    "AR='$msysWasiAR'",
    "RANLIB='$msysWasiRAN'",
    "perl Configure linux-generic32",
    "--prefix='$msysPrefix'",
    "--openssldir='$msysPrefix/ssl'",
    "no-shared no-asm no-threads no-engine no-dso no-tests no-sock",
    "-DNO_FORK",
    $wasiTargetFlags
) -join ' '

Write-Host "cvcpkg: bash -lc `"$cfg`""
& $bash -lc $cfg
if ($LASTEXITCODE -ne 0) { throw 'openssl Configure failed' }

& $bash -lc "cd '$msysSource' && make -j $jobs"
if ($LASTEXITCODE -ne 0) { throw 'openssl make failed' }

& $bash -lc "cd '$msysSource' && make install_sw"
if ($LASTEXITCODE -ne 0) { throw 'openssl make install_sw failed' }
