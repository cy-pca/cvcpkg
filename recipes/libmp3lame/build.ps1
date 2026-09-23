# recipes/libmp3lame/build.ps1 — build LAME MP3 library on Windows via MSYS2/MinGW64.
#
# Only the library is built (--disable-frontend).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
# Drive-letter form with forward slashes, NOT the MSYS /c/... form. `make install`
# runs ranlib from the PINNED toolchain — a NATIVE Windows binary that cannot
# resolve /c/..., so the static half of the build dies with
#   ranlib.exe: '/c/.../install/lib/libmp3lame.a': No such file
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
# Shared means shared ONLY here — do not also build the static archive.
#
# LAME links a convenience archive (libmp3lame/vector/liblamevectorroutines.a).
# The SHARED link handles that with -Wl,--whole-archive and succeeds. The STATIC
# one cannot, so libtool falls back to unpacking the convenience archive through
# a scratch ".lax" directory:
#   (cd .libs/libmp3lame.lax/liblamevectorroutines.a && ar x "/c/.../vector/.libs/liblamevectorroutines.a")
# and that inner ar gets an absolute MSYS path which the pinned native ar cannot
# open. Only this step is affected: every other ar/ranlib call in the build uses
# a relative path and works. The sibling autotools recipes (opus, ogg, vorbis)
# build both flavours happily because none of them has a convenience archive.
#
# Asking for both flavours under CVC_LINK=shared was never the intent anyway —
# ffmpeg resolves libmp3lame through pkg-config and takes the DLL.
$sharedFlag = if ($env:CVC_LINK -eq 'static') {
    '--enable-static --disable-shared'
} else {
    '--enable-shared --disable-static'
}

# LAME 3.100 lists lame_init_old in its export file but never defines it — the
# declaration in include/lame.h is guarded and the definition was dropped
# upstream. GNU ld treats an undefined name in --export-symbols as fatal:
#   ld.exe: cannot export lame_init_old: symbol not defined
# A laxer ambient toolchain let this pass, so it only appears once the pinned
# mingw-w64-gcc is actually the one doing the link. Dropping the line is what
# every downstream packager does (MSYS2, vcpkg, Debian). Idempotent, so
# re-running an incremental build is safe.
$sedSym = "sed -i '/lame_init_old/d' include/libmp3lame.sym"

$cmd = "$depsFlag cd '$msysSource' && $sedSym && ./configure --prefix='$winPrefix' --host=x86_64-w64-mingw32 $sharedFlag --disable-dependency-tracking --disable-frontend --disable-doc && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libmp3lame build failed' }

Invoke-CvcRewriteInstallPaths
