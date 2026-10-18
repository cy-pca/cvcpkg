# recipes/lame/build.ps1 — build the lame MP3 encoder CLI on Windows via MSYS2/MinGW64.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysPrefix = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
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

# The toolchain (msys2, make, mingw-w64-gcc: depends.build) is staged into
# CVC_BUILD_PREFIX, link deps into CVC_DEPS_PREFIX.  Put both first on PATH,
# EXPORTED, so configure and make use them over any MinGW the builder has.
$toolRoots     = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
$msysToolPaths = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'
$depsFlag       = if ($msysToolPaths) { "export PATH='${msysToolPaths}:'`$PATH; " } else { '' }

# am_cv_func_iconv=no: with iconv found (the builder's ambient MinGW has a
# libiconv), frontend/parse.c includes <langinfo.h> under HAVE_ICONV, and
# MinGW has no langinfo.h ("fatal error: langinfo.h: No such file or
# directory").  iconv only converts ID3 tag text in the frontend; it is not
# a declared dependency here, so the build should not pick it up anyway.
# No termcap/curses either: configure found the ambient MinGW ncurses headers,
# but console.o then failed to link (undefined __imp_tgetent/tgetstr/tgetnum).
# Without HAVE_TERMCAP the frontend uses its plain Windows console path.
$noTermcap = 'ac_cv_header_termcap_h=no ac_cv_header_ncurses_termcap_h=no ' +
             'ac_cv_lib_termcap_initscr=no ac_cv_lib_curses_initscr=no ac_cv_lib_ncurses_initscr=no'
$cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' --host=x86_64-w64-mingw32 --enable-frontend --enable-static --disable-shared --disable-nls --disable-dependency-tracking am_cv_func_iconv=no $noTermcap && make -j $jobs && mkdir -p '$msysPrefix/bin' && install -m 755 frontend/lame.exe '$msysPrefix/bin/lame.exe'"
Write-Host "cvcpkg: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'lame build failed' }
