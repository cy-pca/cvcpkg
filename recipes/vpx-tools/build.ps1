# recipes/vpx-tools/build.ps1 — build vpxenc/vpxdec on Windows via MSYS2/MinGW64.
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

$depsFlag = if ($msysDeps) {
    "PKG_CONFIG_PATH='$msysDeps/lib/pkgconfig' CPPFLAGS='-I$msysDeps/include' LDFLAGS='-L$msysDeps/lib -lvpx' PATH='$msysDeps/bin:'`$PATH "
} else { '' }

$cmd = "$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && '$msysSource/configure' --prefix='$msysPrefix' --target=x86_64-win64-gcc --enable-tools --disable-examples --disable-docs --disable-unit-tests --disable-shared --disable-static --enable-vp8 --enable-vp9 && make -j $jobs vpxenc vpxdec && mkdir -p '$msysPrefix/bin' && install -m 755 vpxenc.exe '$msysPrefix/bin/vpxenc.exe' && install -m 755 vpxdec.exe '$msysPrefix/bin/vpxdec.exe'"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'vpx-tools build failed' }
