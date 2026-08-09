# recipes/x264/build.ps1 — build x264 H.264 encoder on Windows via MSYS2/MinGW64.
#
# Produces a native libx264-*.dll + import lib using MinGW-w64 gcc.
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

# `PATH=... cd dir && ./configure` scopes the assignment to `cd` ALONE — cd is a
# regular builtin, so the prefix assignment does not survive into the commands
# after &&. configure then ran with the original PATH, never saw the prefix's
# nasm, and failed "Found no assembler / Minimum version is nasm-2.13" while
# nasm 2.16.03 sat in <prefix>/bin. Export it so it persists for the whole line.
$depsFlag   = if ($msysDeps) { "export PATH='$msysDeps/bin:'`$PATH; " } else { '' }
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--enable-shared --disable-static'
}

# --cross-prefix only when the cross-named binutils actually exist. cvcpkg's own
# mingw-w64-gcc is a NATIVE Windows toolchain: it ships x86_64-w64-mingw32-gcc
# and friends, but binutils under plain names (strings.exe, ar.exe, ld.exe).
# Passing the cross prefix unconditionally made configure look for
# x86_64-w64-mingw32-strings, which does not exist, and die "endian test failed"
# — a message that says nothing about the missing tool.
$crossProbe = Join-Path $env:CVC_DEPS_PREFIX 'bin\x86_64-w64-mingw32-strings.exe'
$crossFlag  = if ($env:CVC_DEPS_PREFIX -and (Test-Path $crossProbe)) {
    '--cross-prefix=x86_64-w64-mingw32- '
} else { '' }

$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 $crossFlag$sharedFlag --disable-cli --disable-lavf --disable-swscale --disable-opencl && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'x264 build failed' }

Invoke-CvcRewriteInstallPaths
