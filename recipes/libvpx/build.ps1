# recipes/libvpx/build.ps1 — build VP8/VP9 codec library on Windows via MSYS2/MinGW64.
#
# libvpx uses a custom configure that must be run from a separate build dir.
# We use --target=x86_64-win64-gcc so libvpx selects the MinGW-w64 toolchain
# and produces a native Windows DLL.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash        = Get-CvcGitBash
# Drive-letter form with forward slashes, NOT the MSYS /c/... form. `make install`
# runs ranlib from the PINNED toolchain — a NATIVE Windows binary that cannot
# resolve /c/..., so the static half of the build dies with
#   ranlib.exe: '/c/.../install/lib/libvpx.a': No such file
# Hidden while PATH pointed at the ambient MSYS toolchain, whose ranlib takes
# both forms; pinning the build prefix (below) surfaces it. Same as x264.
$winPrefix   = ($env:CVC_INSTALL_DIR -replace '\\', '/')
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

# Host tools (nasm, the compiler) are staged into CVC_BUILD_PREFIX, deliberately
# NOT the runtime prefix — a toolchain in the runtime prefix shadows MSVC for
# every later CMake build against it. Search both, build prefix first so the
# pinned toolchain wins over any ambient C:\msys64\mingw64\bin. Looking only at
# CVC_DEPS_PREFIX pointed PATH at the runtime prefix, where nasm never lives.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

# EXPORT, not a command-prefix assignment: `PATH=x mkdir -p d && cd d && ./configure`
# scopes the assignment to `mkdir` ALONE (a regular builtin), so configure ran
# with the original PATH, never saw the staged nasm, and died with
#   Neither yasm nor nasm have been found.
# which reads as a missing dependency rather than a quoting bug. Same defect
# x264 carried; see recipes/x264/build.ps1 for the original diagnosis.
$depsFlag   = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }

# libvpx's configure compiles its probe files under $TMPDIR (default /tmp) and
# passes the ABSOLUTE path straight to the compiler. gcc here is a NATIVE
# Windows binary and cannot resolve /tmp/..., so every probe dies with
#   cc1.exe: fatal error: /tmp/vpx-conf-NNNN.c: No such file or directory
# and configure reports only the summary line
#   Unable to invoke compiler: gcc  -fno-common -m64
# which reads as a broken or missing toolchain rather than a path problem. The
# real message is in the build dir's config.log.
#
# MSYS2 would normally rewrite such arguments on the way to a native process,
# but MSYS_NO_PATHCONV=1 (set above, and needed so Windows-style arguments are
# not mangled) turns that off — so the conversion cannot be relied on here.
# A drive-letter path with forward slashes is understood by BOTH bash and the
# native compiler. Same fix as recipes/ffmpeg.
$winTmp = ($env:CVC_BUILD_DIR -replace '\\', '/') + '/cfgtmp'
New-Item -ItemType Directory -Force -Path (Join-Path $env:CVC_BUILD_DIR 'cfgtmp') | Out-Null
$depsFlag = "export TMPDIR='$winTmp'; " + $depsFlag
# ALWAYS static on Windows — CVC_LINK cannot be honoured here. libvpx's own
# configure refuses a shared build for PE targets:
#   --enable-shared only supported on ELF, OS/2, and Darwin for now
# It is an upstream limitation, not a toolchain problem, and it stayed hidden
# because the nasm/PATH bug above killed configure before it got this far — so
# this recipe had never actually produced a Windows build.
#
# Static is also what we want: a shared libvpx would drag libgcc into
# everything linking it (and cvcpkg's mingw-w64-runtime package exists for
# consumers that import the runtime DLLs, which the ffmpeg stack deliberately
# does not — it links the runtime statically). ffmpeg absorbs the archive and
# stays self-contained, exactly as it does with x264.
$sharedFlag = '--disable-shared --enable-static'

$cmd = "$depsFlag mkdir -p '$msysBuild' && cd '$msysBuild' && '$msysSource/configure' --prefix='$winPrefix' --target=x86_64-win64-gcc $sharedFlag --disable-examples --disable-tools --disable-docs --disable-unit-tests --enable-vp8 --enable-vp9 --enable-vp9-highbitdepth && make -j $jobs && make install"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'libvpx build failed' }

Invoke-CvcRewriteInstallPaths
