# recipes/gettext/build.ps1 — build GNU gettext on Windows via MSYS2 + MinGW autotools.
#
# Only the gettext-runtime sub-tree (libintl.dll) is built; the full
# gettext build pulls in libunistring and libiconv which are more
# portably handled as separate recipes.  Downstream consumers on
# Windows only need libintl for i18n string lookup.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash    = Get-CvcGitBash
$srcMsys = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
# Drive-letter prefix, not /c/...: `make install` runs the NATIVE ranlib on
# lib/libintl.a, which cannot open an MSYS path (see recipes/x264).
$winPrefix = ($env:CVC_INSTALL_DIR -replace '\\', '/')

# Build only gettext-runtime to avoid libtextstyle's iconv issues.
$env:MSYSTEM         = 'MINGW64'
# Leave MSYS2's argument path conversion ON (no MSYS_NO_PATHCONV), as
# Invoke-CvcMsysAutotoolsBuild does.  libtool converts paths for the native
# toolchain with `cmd //c echo <path>`, which relies on MSYS turning `//c` into
# `/c`.  With conversion off, cmd.exe got a literal `//c`, started an
# interactive shell and waited on stdin: on the dev Windows builder every
# libtool link hung there (libmp3lame #17744, libogg #17745), holding the
# builder's only job slot for days.  Conversion also hands the native gcc
# -IC:/... for an MSYS -I/c/... argument, which it can open.
Remove-Item Env:MSYS_NO_PATHCONV -ErrorAction SilentlyContinue
$env:CHERE_INVOKING  = '1'

# env-windows.ps1 sets CC=cl / CXX=cl for the MSVC helpers; autoconf takes $CC
# at face value, so configure died with "C compiler cannot create executables".
# Clear them (as Invoke-CvcMsysAutotoolsBuild does) so it finds MinGW gcc.
'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# The toolchain (msys2, make, mingw-w64-gcc: depends.build) is staged into
# CVC_BUILD_PREFIX, link deps into CVC_DEPS_PREFIX.  Put both first on PATH,
# EXPORTED, so configure and make use them over any MinGW the builder has.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'
$pathFlag      = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }

$cmd  = "$pathFlag cd '$srcMsys/gettext-runtime' && "
$cmd += "./configure --prefix='$winPrefix' "
$cmd += "--host=x86_64-w64-mingw32 --disable-java --disable-csharp "
$cmd += "--without-emacs --without-git --without-bzip2 --without-xz "
# NLS stays on: --disable-nls drops gettext-runtime's intl/, so the Windows
# bundle had no libintl at all, the one library this build is for.
# am_cv_func_iconv=no keeps the builder's ambient MinGW libiconv (not a
# declared dependency) out of libintl; it then does no charset conversion.
$cmd += "--disable-dependency-tracking am_cv_func_iconv=no && "
$cmd += "make -j $env:CVC_JOBS && make install"

& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'gettext build failed' }

Invoke-CvcRewriteInstallPaths
