# recipes/libtool/build.ps1 — build GNU Libtool from source inside MSYS2.
#
# Libtool is a build-time host tool only.  Requires m4 to be built
# first (declared as host_tools in recipe.yaml).  autotools timestamps
# are touched so make does not try to regenerate them.
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

# Touch autotools timestamps so make doesn't try to regenerate them
# with tools not available at configure time.
$touchCmd = "find '$msysSource' \( -name 'aclocal.m4' -o -name 'configure' -o -name 'Makefile.in' -o -name 'config.h.in' \) -print0 | xargs -0 touch"
& $bash -lc $touchCmd

$depsFlag = if ($msysDeps) { "PATH='$msysDeps/bin:'`$PATH " } else { '' }
$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' && make -j $jobs AUTOMAKE=true ACLOCAL=true AUTOCONF=true AUTOHEADER=true && make install AUTOMAKE=true ACLOCAL=true AUTOCONF=true AUTOHEADER=true"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libtool build failed' }

& $bash -lc "$msysPrefix/bin/libtool --version" | Select-Object -First 1
