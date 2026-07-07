# recipes/ffmpeg/build.ps1 — build FFmpeg on Windows via MSYS2/MinGW64.
#
# Produces native Windows DLLs (avcodec-*.dll etc.) using the MinGW-w64
# GCC toolchain inside MSYS2.  The same external codec set as the Unix
# build is enabled: Opus, MP3, Vorbis, VP8/VP9, AV1 (dav1d), WebP,
# JPEG, PNG, OpenSSL, freetype, fribidi, bzip2, lzma.
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
$configureCmd = @"
$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && \
  '$msysSource/configure' \
    --prefix='$msysPrefix' \
    --target-os=mingw32 \
    --arch=x86_64 \
    --cross-prefix=x86_64-w64-mingw32- \
    --enable-pic \
    --enable-version3 \
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
    --enable-libjpeg \
    --enable-libpng \
    --enable-libfreetype \
    --enable-libfribidi \
    --enable-openssl \
    --enable-zlib \
    --enable-bzlib \
    --enable-lzma \
    --enable-w32threads \
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

Invoke-CvcRewriteInstallPaths
