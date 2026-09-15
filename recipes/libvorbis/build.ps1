# recipes/libvorbis/build.ps1 — build libvorbis on Windows via MSYS2/MinGW64.
#
# libogg must be in CVC_DEPS_PREFIX (declared as depends.build in recipe.yaml).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR

# Drive-letter form with forward slashes, NOT the MSYS /c/... form, for both the
# install prefix and the dependency root. Two separate reasons:
#   * `make install` runs ranlib from the PINNED toolchain — a NATIVE Windows
#     binary that cannot resolve /c/... — so the static half dies with
#       ranlib.exe: '/c/.../install/lib/libvorbis.a': No such file
#   * --with-ogg becomes -I<dir>/include -L<dir>/lib for that same native gcc,
#     which silently ignores search paths that do not exist rather than erroring.
# Both stayed hidden while PATH leaked to the ambient MSYS toolchain. See
# recipes/x264 for the original diagnosis.
$winPrefix  = ($env:CVC_INSTALL_DIR -replace '\\', '/')
$winDeps    = if ($env:CVC_DEPS_PREFIX) { ($env:CVC_DEPS_PREFIX -replace '\\', '/') } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# Host tools (the compiler, pkg-config) are staged into CVC_BUILD_PREFIX,
# deliberately NOT the runtime prefix. Search both, build prefix first so the
# pinned toolchain wins over any ambient C:\msys64\mingw64\bin. Looking only at
# CVC_DEPS_PREFIX meant pkg-config itself was never on PATH.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }

# PATH is consumed by the SHELL, so it wants MSYS form and ':' separators.
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

# PKG_CONFIG_PATH is consumed by pkg-config.exe, a NATIVE Windows binary, so it
# wants drive-letter paths and ';' separators. Handed the MSYS form it silently
# finds nothing, and configure then reports the dependency as simply absent —
# indistinguishable from it never having been built:
#   configure: error: must have Ogg installed!
# with ogg.pc sitting in the prefix the whole time.
$winPcPaths    = ($toolRoots | ForEach-Object { ($_ -replace '\\', '/') + '/lib/pkgconfig' }) -join ';'

# EXPORT, not a command-prefix assignment: `VAR=x cd dir && ./configure` scopes
# the assignment to `cd` ALONE (a regular builtin), so configure ran with the
# original environment and saw neither PKG_CONFIG_PATH nor PATH. Same defect
# x264 carried; see recipes/x264/build.ps1.
$depsFlag   = if ($msysToolPaths) {
    "export PKG_CONFIG_PATH='$winPcPaths'; export PATH='${msysToolPaths}:'`$PATH; "
} else { '' }
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--enable-shared --enable-static'
}
$oggFlag    = if ($winDeps) { "--with-ogg='$winDeps'" } else { '' }

$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$winPrefix' --host=x86_64-w64-mingw32 $sharedFlag --disable-dependency-tracking $oggFlag && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libvorbis build failed' }

Invoke-CvcRewriteInstallPaths
