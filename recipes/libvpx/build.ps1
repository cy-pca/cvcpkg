# recipes/libvpx/build.ps1 — build VP8/VP9 codec library on Windows via MSYS2/MinGW64.
#
# libvpx uses a custom configure that must be run from a separate build dir.
# We use --target=x86_64-win64-gcc so libvpx selects the MinGW-w64 toolchain
# and produces a native Windows DLL.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysBuild   = ConvertTo-CvcMsysPath $env:CVC_BUILD_DIR
$msysDeps    = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs        = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

$depsFlag   = if ($msysDeps) { "PATH='$msysDeps/bin:'`$PATH " } else { '' }
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--disable-shared --enable-static'
} else {
    '--enable-shared --disable-static'
}

$cmd = "$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && '$msysSource/configure' --prefix='$msysPrefix' --target=x86_64-win64-gcc $sharedFlag --disable-examples --disable-tools --disable-docs --disable-unit-tests --enable-vp8 --enable-vp9 --enable-vp9-highbitdepth && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libvpx build failed' }

Invoke-CvcRewriteInstallPaths
