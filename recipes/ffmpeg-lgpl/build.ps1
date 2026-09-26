# recipes/ffmpeg-lgpl/build.ps1 — build a strict LGPL-2.1 FFmpeg on Windows
# via MSYS2/MinGW64.
#
# LGPL counterpart to the `ffmpeg` recipe: no --enable-gpl, no
# --enable-version3, no x264/x265, no OpenSSL, so the result is
# LGPL-2.1-or-later and links into LGPL-2.1 libcvc without relicensing it.
# H.264/HEVC DECODE still works via FFmpeg's native LGPL decoders (x264/x265
# are GPL encoders only).  HTTPS/TLS uses SChannel, the OS-native Windows TLS
# stack — no OpenSSL, no external TLS library, no license impact.  Kept: Opus,
# MP3, Vorbis, VP8/VP9, AV1 (dav1d), WebP, freetype, fribidi, bzip2, lzma.
# Given up vs `ffmpeg`: H.264/HEVC encode.
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
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

# Clear MSVC env so MinGW-w64 gcc is selected by FFmpeg's configure.
'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

$depsFlag = if ($msysDeps) {
    "PKG_CONFIG_PATH='$msysDeps/lib/pkgconfig' PATH='$msysDeps/bin:'`$PATH "
} else { '' }

$sharedFlags = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--disable-static --enable-shared'
}

# Build in a separate directory (FFmpeg's configure supports out-of-tree).
# No --enable-gpl / --enable-version3 / x264 / x265 / openssl: LGPL-2.1.
# TLS comes from SChannel (the OS-native Windows TLS), so no external TLS lib
# is linked.  PNG/(M)JPEG are native FFmpeg codecs, so there is no
# --enable-libpng / --enable-libjpeg switch — only WebP is external.
$configureCmd = @"
$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && \
  '$msysSource/configure' \
    --prefix='$msysPrefix' \
    --target-os=mingw32 \
    --arch=x86_64 \
    --cross-prefix=x86_64-w64-mingw32- \
    --enable-pic \
    $sharedFlags \
    --disable-programs \
    --disable-doc \
    --disable-debug \
    --enable-libopus \
    --enable-libmp3lame \
    --enable-libvorbis \
    --enable-libvpx \
    --enable-libdav1d \
    --enable-libwebp \
    --enable-libfreetype \
    --enable-libfribidi \
    --enable-schannel \
    --enable-zlib \
    --enable-bzlib \
    --enable-lzma \
    --enable-w32threads \
    --enable-dxva2 \
    --enable-d3d11va \
  && make -j $jobs \
  && make install
"@

Write-Host "cvcpkg: bash -lc <ffmpeg-lgpl configure + make>"
& $bash -lc $configureCmd
if ($LASTEXITCODE -ne 0) {
    $cfgLog = Join-Path $env:CVC_BUILD_DIR 'ffbuild\config.log'
    if (Test-Path $cfgLog) {
        Write-Host '--- config.log (last 80 lines) ---'
        Get-Content $cfgLog -Tail 80 | Write-Host
    }
    throw 'FFmpeg (LGPL) build failed'
}

Invoke-CvcRewriteInstallPaths
