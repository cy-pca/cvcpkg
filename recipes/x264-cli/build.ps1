# recipes/x264-cli/build.ps1 — build x264 encoder CLI on Windows via MSYS2/MinGW64.
#
# The toolchain handling mirrors recipes/x264/build.ps1, which hit the same
# problems first.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysPrefix = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

# cvcpkg's mingw-w64-gcc (a host tool) is staged into CVC_BUILD_PREFIX, nasm and
# pkg-config into either prefix.  Search both, build prefix first.  Only the
# deps prefix was on PATH before, so configure found no compiler at all
# ("No working C compiler found", populate job 22615).
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysRoots = $toolRoots | ForEach-Object { ConvertTo-CvcMsysPath $_ }

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# Export, don't prefix: `VAR=... cd dir && ./configure` scopes the assignments
# to `cd` alone, so configure ran without any of them.
$exports = ''
if ($msysRoots) {
    $binPath = ($msysRoots | ForEach-Object { "$_/bin" }) -join ':'
    $pcPath  = ($msysRoots | ForEach-Object { "$_/lib/pkgconfig" }) -join ':'
    $incs    = ($msysRoots | ForEach-Object { "-I$_/include" }) -join ' '
    $libs    = ($msysRoots | ForEach-Object { "-L$_/lib" }) -join ' '
    $exports = "export PATH='${binPath}:'`$PATH PKG_CONFIG_PATH='$pcPath' CPPFLAGS='$incs' LDFLAGS='$libs'; "
}

# --cross-prefix only when the cross-named binutils exist: cvcpkg's
# mingw-w64-gcc ships x86_64-w64-mingw32-gcc but plain-named binutils, and
# configure dies looking for x86_64-w64-mingw32-strings otherwise.
$crossFlag = ''
foreach ($r in $toolRoots) {
    if (Test-Path (Join-Path $r 'bin\x86_64-w64-mingw32-strings.exe')) {
        $crossFlag = '--cross-prefix=x86_64-w64-mingw32- '
        break
    }
}

$cmd = "$exports cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 $crossFlag--disable-lavf --disable-swscale --disable-opencl && make -j $jobs x264 && mkdir -p '$msysPrefix/bin' && install -m 755 x264.exe '$msysPrefix/bin/x264.exe'"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'x264-cli build failed' }
