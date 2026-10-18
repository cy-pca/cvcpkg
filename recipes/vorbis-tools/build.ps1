# recipes/vorbis-tools/build.ps1 — build Ogg Vorbis tools on Windows via MSYS2/MinGW64.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
# Drive-letter form with forward slashes for the prefix and the dependency root,
# as recipes/libvorbis: --with-ogg/--with-vorbis become -I<dir>/include
# -L<dir>/lib for the NATIVE gcc, which silently ignores a /c/... search path.
$winPrefix  = ($env:CVC_INSTALL_DIR -replace '\\', '/')
$winDeps    = if ($env:CVC_DEPS_PREFIX) { ($env:CVC_DEPS_PREFIX -replace '\\', '/') } else { '' }
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysDeps   = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

$env:MSYSTEM          = 'MINGW64'
# Leave MSYS2's argument path conversion ON (no MSYS_NO_PATHCONV), as
# Invoke-CvcMsysAutotoolsBuild does.  libtool converts paths for the native
# toolchain with `cmd //c echo <path>`, which relies on MSYS turning `//c` into
# `/c`.  With conversion off, cmd.exe got a literal `//c`, started an
# interactive shell and waited on stdin: on the dev Windows builder every
# libtool link hung there (libmp3lame #17744, libogg #17745), holding the
# builder's only job slot for days.  Conversion also hands the native gcc
# -IC:/... for an MSYS -I/c/... argument, which it can open.
Remove-Item Env:MSYS_NO_PATHCONV -ErrorAction SilentlyContinue
$env:CHERE_INVOKING   = '1'

'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# EXPORT, not a command-prefix assignment: `VAR=x cd dir && ./configure` scopes
# the assignments to `cd` ALONE (see recipes/libogg), so configure never saw the
# deps prefix and failed with "Ogg needed!" even with libogg installed there.
# The toolchain (msys2, make, mingw-w64-gcc: depends.build) is staged into
# CVC_BUILD_PREFIX, link deps into CVC_DEPS_PREFIX.  Put both first on PATH,
# EXPORTED, so configure and make use them over any MinGW the builder has.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'
$pathFlag      = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }
$depsFlag = $pathFlag + $(if ($winDeps) { "export PKG_CONFIG_PATH='$winDeps/lib/pkgconfig'; " } else { '' })
$xiphFlags = if ($winDeps) { "--with-ogg='$winDeps' --with-vorbis='$winDeps'" } else { '' }

# -std=gnu17: MinGW gcc 15+ defaults to C23, where the bundled getopt.c's
# old-style `extern char *getenv ();` declares a no-argument function
# ("too many arguments to function 'getenv'").
$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$winPrefix' --host=x86_64-w64-mingw32 --disable-dependency-tracking --disable-nls --without-ao --without-speex --without-flac --without-curl $xiphFlags CFLAGS='-O2 -std=gnu17' && make -j $jobs && make install-exec"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'vorbis-tools build failed' }
