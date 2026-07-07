# recipes/pkg-config/build.ps1 — build pkg-config from source via MSYS2/MinGW64.
#
# Produces a native Windows pkg-config.exe (MinGW64, no MSYS2 DLLs) so
# it can be called directly by meson and other native Windows tools.
# --with-internal-glib removes the only external dependency.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$jobs        = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

# Use the MinGW64 subsystem to produce a native Windows binary that
# runs without MSYS2 DLLs — needed for meson and cmake to call it.
$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

$cmd = "cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 --with-internal-glib --disable-dependency-tracking --disable-nls && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'pkg-config build failed' }

& $bash -lc "$msysPrefix/bin/pkg-config --version" | Select-Object -First 1
