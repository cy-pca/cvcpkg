# recipes/automake/build.ps1 — build GNU Automake from source inside MSYS2.
#
# Automake is a build-time host tool only.  Requires autoconf (which
# in turn requires m4) to be built first (declared as host_tools in
# recipe.yaml).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysDeps    = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs        = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

$env:MSYSTEM          = 'MSYS'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

$depsFlag = if ($msysDeps) { "PATH='$msysDeps/bin:'`$PATH " } else { '' }
$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'automake build failed' }

& $bash -lc "$msysPrefix/bin/automake --version" | Select-Object -First 1
