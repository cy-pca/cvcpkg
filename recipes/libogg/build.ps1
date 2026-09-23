# recipes/libogg/build.ps1 — build libogg on Windows via MSYS2/MinGW64.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
# Drive-letter form with forward slashes, NOT the MSYS /c/... form. `make install`
# runs ranlib from the PINNED toolchain — a NATIVE Windows binary that cannot
# resolve /c/..., so the static half of the build dies with
#   ranlib.exe: '/c/.../install/lib/libogg.a': No such file
# This stayed hidden while PATH pointed at the ambient MSYS toolchain, whose
# ranlib understands both forms; pinning the build prefix (above) surfaces it.
# Same fix, same reason as recipes/x264.
$winPrefix  = ($env:CVC_INSTALL_DIR -replace '\\', '/')
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysDeps   = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# Host tools (the compiler) are staged into CVC_BUILD_PREFIX, deliberately NOT
# the runtime prefix. Search both, build prefix first so the pinned toolchain
# wins over any ambient C:\msys64\mingw64\bin.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

# EXPORT, not a command-prefix assignment: `PATH=x cd dir && ./configure` scopes
# the assignment to `cd` ALONE (a regular builtin), so configure runs with the
# original PATH and silently falls through to whatever toolchain the machine
# happens to have. Same defect x264 carried; see recipes/x264/build.ps1.
$depsFlag   = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--enable-shared --enable-static'
}

$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$winPrefix' --host=x86_64-w64-mingw32 $sharedFlag --disable-dependency-tracking && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libogg build failed' }

Invoke-CvcRewriteInstallPaths
