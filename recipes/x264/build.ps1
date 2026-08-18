# recipes/x264/build.ps1 — build x264 H.264 encoder on Windows via MSYS2/MinGW64.
#
# Produces a native libx264-*.dll + import lib using MinGW-w64 gcc.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

# Drive-letter form with forward slashes, NOT the MSYS /c/... form.
# `make install` runs MSYS `install` (happy with either) and then `ranlib` from
# the toolchain, which is a NATIVE Windows binary and cannot resolve /c/...:
#   gcc-ranlib /c/.../install/lib/libx264.a
#   ranlib.exe: '/c/.../libx264.a': No such file
# That only bites the STATIC path, because the shared path never runs ranlib —
# which is why static was broken while shared worked.
$winPrefix  = ($env:CVC_INSTALL_DIR -replace '\\', '/')

# Host tools (the compiler) are staged into CVC_BUILD_PREFIX, deliberately NOT
# the runtime prefix. Search both: build prefix first, so our pinned gcc wins.
# Without the build prefix here the Windows build silently fell through to
# C:\msys64\mingw64\bin — an ambient toolchain cvcpkg never pinned.
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

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
$depsFlag   = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }

# Honour CVC_LINK in BOTH directions. Static matters for hermeticity: a shared
# libx264 drags MinGW runtime DLLs into anything that links it, and cvcpkg
# packages no MinGW runtime, so a shared-only x264 forces hand-copied DLLs into
# the prefix. Static lets ffmpeg absorb it and ship self-contained.
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
$crossFlag = ''
foreach ($r in $toolRoots) {
    if (Test-Path (Join-Path $r 'bin\x86_64-w64-mingw32-strings.exe')) {
        $crossFlag = '--cross-prefix=x86_64-w64-mingw32- '
        break
    }
}

$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$winPrefix' --host=x86_64-w64-mingw32 $crossFlag$sharedFlag --disable-cli --disable-lavf --disable-swscale --disable-opencl && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'x264 build failed' }

Invoke-CvcRewriteInstallPaths
