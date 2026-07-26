# recipes/pkg-config/build.ps1 — build pkgconf (the pkg-config replacement)
# via MSYS2/MinGW64.
#
# Produces a native Windows pkgconf.exe (MinGW64, no MSYS2 DLLs) and copies it
# to pkg-config.exe, so meson/cmake can call either name directly. pkgconf is
# standalone C (no bundled glib), so no --with-internal-glib is needed.
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

# Symlinks are unreliable on Windows, so copy pkgconf.exe to pkg-config.exe.
$cmd = "cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 --disable-shared --enable-static --disable-dependency-tracking && make -j $jobs && make install && cp '$msysPrefix/bin/pkgconf.exe' '$msysPrefix/bin/pkg-config.exe'"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'pkgconf build failed' }

& $bash -lc "$msysPrefix/bin/pkg-config.exe --version" | Select-Object -First 1
