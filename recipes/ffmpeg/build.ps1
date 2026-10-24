# recipes/ffmpeg/build.ps1 — build FFmpeg on Windows via MSYS2/MinGW64.
#
# Produces native Windows DLLs (avcodec-*.dll etc.) using the MinGW-w64
# GCC toolchain inside MSYS2.  The same GPL codec set as the Unix build
# is enabled (--enable-gpl): the x264 (H.264) and x265 (HEVC) GPL
# encoders, plus Opus, MP3, Vorbis, VP8/VP9, AV1 (dav1d), WebP, JPEG,
# PNG, OpenSSL, freetype, fribidi, bzip2, lzma.  Enabling x264/x265
# makes the result GPL-2.0-or-later (matching recipe.yaml's license).
#
# All codec/library dependencies must be pre-built and available in
# CVC_DEPS_PREFIX (declared as depends.build in recipe.yaml).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysPrefix = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysBuild  = ConvertTo-CvcMsysPath $env:CVC_BUILD_DIR
$msysDeps   = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

$env:MSYSTEM          = 'MINGW64'
# No MSYS_NO_PATHCONV: configure and make (an MSYS binary) work in /c/... paths,
# and the MSYS runtime converts /c/... and /tmp/... arguments when it starts a
# native program.  With conversion off, cvcpkg's native MinGW gcc got them
# literally: "cc1.exe: fatal error: /tmp/ffconf.XXXXXXXX/test.c: No such file
# or directory".  (Drive-letter paths instead would put C:/ into make rules.)
Remove-Item Env:MSYS_NO_PATHCONV -ErrorAction SilentlyContinue
$env:CHERE_INVOKING   = '1'

# Clear MSVC env so MinGW-w64 gcc is selected by FFmpeg's configure.
'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# cvcpkg's mingw-w64-gcc is staged into CVC_BUILD_PREFIX, the codec libraries
# into CVC_DEPS_PREFIX; search both, build prefix first.  Only the deps prefix
# was on PATH, and as a prefix to `mkdir -p` the assignments reached only mkdir,
# so configure found no compiler ("x86_64-w64-mingw32-gcc: command not found",
# populate job 22643).  Export them, as recipes/x264/build.ps1 does.
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysRoots = $toolRoots | ForEach-Object { ConvertTo-CvcMsysPath $_ }
$depsFlag = ''
if ($msysRoots) {
    $binPath = ($msysRoots | ForEach-Object { "$_/bin" }) -join ':'
    # cvcpkg's pkg-config is a native Windows program, and MSYS converts no
    # environment variable but PATH: give it C:/... entries, ';'-separated.
    $pcPath  = ($toolRoots | ForEach-Object { ($_ -replace '\\', '/') + '/lib/pkgconfig' }) -join ';'
    $depsFlag = "export PATH='${binPath}:'`$PATH PKG_CONFIG_PATH='$pcPath'; "
}

# --cross-prefix only when the cross-named binutils exist: cvcpkg's
# mingw-w64-gcc ships x86_64-w64-mingw32-gcc but plain-named binutils.  Name
# pkg-config outright, or configure looks for x86_64-w64-mingw32-pkg-config.
$crossFlag = ''
foreach ($r in $toolRoots) {
    if (Test-Path (Join-Path $r 'bin\x86_64-w64-mingw32-strings.exe')) {
        $crossFlag = '--cross-prefix=x86_64-w64-mingw32-'
        break
    }
}

$sharedFlags = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--disable-static --enable-shared'
}

# Build in a separate directory (FFmpeg's configure supports out-of-tree).
# cvcpkg's Windows libvpx is a static MinGW libvpx.a whose thread code calls
# pthreads, so configure's libvpx check failed with undefined pthread_* symbols.
# Link winpthreads statically: cvcpkg ships no MinGW runtime DLLs.
$configureCmd = @"
$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && \
  '$msysSource/configure' \
    --prefix='$msysPrefix' \
    --target-os=mingw32 \
    --arch=x86_64 \
    $crossFlag \
    --pkg-config=pkg-config \
    --enable-gpl \
    --enable-pic \
    --enable-version3 \
    $sharedFlags \
    --disable-programs \
    --disable-doc \
    --disable-debug \
    --enable-libx264 \
    --enable-libx265 \
    --enable-libopus \
    --enable-libmp3lame \
    --enable-libvorbis \
    --enable-libvpx \
    --enable-libdav1d \
    --enable-libwebp \
    --enable-libfreetype \
    --enable-libfribidi \
    --enable-openssl \
    --enable-zlib \
    --enable-bzlib \
    --enable-lzma \
    --enable-w32threads \
    --extra-libs=-l:libpthread.a \
    --enable-dxva2 \
    --enable-d3d11va \
  && make -j $jobs \
  && make install
"@

Write-Host "cvcpkg: bash -lc <ffmpeg configure + make>"
& $bash -lc $configureCmd
if ($LASTEXITCODE -ne 0) {
    $cfgLog = Join-Path $env:CVC_BUILD_DIR 'ffbuild\config.log'
    if (Test-Path $cfgLog) {
        Write-Host '--- config.log (last 80 lines) ---'
        Get-Content $cfgLog -Tail 80 | Write-Host
    }
    throw 'FFmpeg build failed'
}

# FFmpeg's MinGW install puts the MSVC import libraries (avcodec.lib, ...) in
# bin\ beside the DLLs; MSVC consumers, and package.files, look in lib\.
Get-ChildItem -Path (Join-Path $env:CVC_INSTALL_DIR 'bin') -Filter '*.lib' -File |
    Move-Item -Destination (Join-Path $env:CVC_INSTALL_DIR 'lib') -Force

Invoke-CvcRewriteInstallPaths
