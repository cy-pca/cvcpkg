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

# Build only gettext-runtime to avoid libtextstyle's iconv issues.
$env:MSYSTEM         = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'

$cmd  = "cd '$srcMsys/gettext-runtime' && "
$cmd += "./configure --prefix='$(ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR)' "
$cmd += "--host=x86_64-w64-mingw32 --disable-java --disable-csharp "
$cmd += "--without-emacs --without-git --without-bzip2 --without-xz "
$cmd += "--disable-nls --disable-dependency-tracking && "
$cmd += "make -j $env:CVC_JOBS && make install"

& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw 'gettext build failed' }

Invoke-CvcRewriteInstallPaths
