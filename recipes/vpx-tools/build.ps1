# recipes/vpx-tools/build.ps1 — build vpxenc/vpxdec on Windows via MSYS2/MinGW64.
#
# Same approach as build.sh: link the tools against the static libvpx built
# from this tarball (target=libs, then target=examples vpxenc vpxdec).  The
# MSYS plumbing (staged tool PATH, a TMPDIR the native compiler can read) is
# recipes/libvpx/build.ps1's; see the comments there.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
$msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
# A drive-letter path with forward slashes: libvpx's makefiles hand the source
# path it was configured from to the native nasm/gcc, which cannot open
# /c/... paths (MSYS_NO_PATHCONV=1 turns MSYS's conversion off).
$winSource   = $env:CVC_SOURCE_DIR -replace '\\', '/'
$msysBuild   = ConvertTo-CvcMsysPath $env:CVC_BUILD_DIR
$jobs        = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# nasm and the compiler are staged into CVC_BUILD_PREFIX; search it first.
# EXPORT the PATH (a command-prefix assignment would scope it to `mkdir` only).
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'
$depsFlag      = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }

# configure compiles its probes under $TMPDIR and hands the absolute path to the
# native gcc, which cannot resolve /tmp/...; a drive-letter path works for both.
$winTmp = ($env:CVC_BUILD_DIR -replace '\\', '/') + '/cfgtmp'
New-Item -ItemType Directory -Force -Path (Join-Path $env:CVC_BUILD_DIR 'cfgtmp') | Out-Null
$depsFlag = "export TMPDIR='$winTmp'; " + $depsFlag

# Link the MinGW runtime statically.  cvcpkg packages no libgcc_s_seh-1.dll,
# libstdc++-6.dll or libwinpthread-1.dll, and without -static both tools need
# all three (Windows exits 0xC0000135, DLL not found).  libvpx's configure keeps
# LDFLAGS from the environment.
$depsFlag = "export LDFLAGS='-static'; " + $depsFlag

$cmd = "$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && '$winSource/configure' --prefix='$msysPrefix' --target=x86_64-win64-gcc --enable-examples --disable-docs --disable-unit-tests --enable-static --disable-shared --enable-vp8 --enable-vp9 && make -j $jobs target=libs && make -j $jobs target=examples vpxenc.exe vpxdec.exe && mkdir -p '$msysPrefix/bin' && install -m 755 vpxenc.exe '$msysPrefix/bin/vpxenc.exe' && install -m 755 vpxdec.exe '$msysPrefix/bin/vpxdec.exe'"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'vpx-tools build failed' }
