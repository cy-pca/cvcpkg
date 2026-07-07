# recipes/m4/build.ps1 — build GNU m4 from source inside MSYS2.
#
# m4 is a build-time host tool only (no Windows library produced).
# It is built inside the MSYS subsystem (not MinGW64) so the resulting
# binary runs in MSYS2's bash environment, where autoconf/automake
# expect to find it.  The install prefix is CVC_INSTALL_DIR which
# the builder adds to PATH for downstream host_tools consumers.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$jobs        = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

# Use MSYS subsystem so configure produces MSYS-native binaries that
# bash can exec.  MSYS_NO_PATHCONV prevents mangling of Windows paths
# passed in env vars.
$env:MSYSTEM          = 'MSYS'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

$cmd = "cd '$msysSource' && ./configure --prefix='$msysPrefix' --disable-nls && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'm4 build failed' }

& $bash -lc "$msysPrefix/bin/m4 --version" | Select-Object -First 1
