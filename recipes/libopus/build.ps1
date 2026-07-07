# recipes/libopus/build.ps1 — build libopus on Windows via MSYS2/MinGW64.
#
# Produces a native opus-*.dll and import lib using MinGW-w64 gcc.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysPrefix = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysDeps   = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

$depsFlag   = if ($msysDeps) { "PATH='$msysDeps/bin:'`$PATH " } else { '' }
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--enable-shared --enable-static'
}

$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 $sharedFlag --disable-dependency-tracking --disable-extra-programs --disable-doc && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libopus build failed' }

Invoke-CvcRewriteInstallPaths
