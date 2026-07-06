# recipes/aspell/build.ps1 — build GNU Aspell on Windows via MSYS2 MinGW-w64.
#
# Aspell is a C++ autotools project; MSVC has no official support.
# MinGW-w64's g++ builds the tree without patches after we suppress
# `-Werror=implicit-function-declaration` (aspell 0.60.8 tickles it
# on modern gcc).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$env:CFLAGS   = "-O2 -Wno-error=implicit-function-declaration"
$env:CXXFLAGS = "-O2 -Wno-error=implicit-function-declaration"

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @(
    '--disable-dependency-tracking',
    '--disable-nls'
)

# Emit a pkg-config file — Aspell doesn't ship one.  Use MSYS-style
# ${pcfiledir}/../.. so consumers can activate the prefix under either
# MinGW or an MSYS shell.
$pc = @'
prefix=${pcfiledir}/../..
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: aspell
Description: GNU spell-checker library
URL: http://aspell.net/
Version: 0.60.8
Libs: -L${libdir} -laspell
Cflags: -I${includedir}
'@
$pcDir = Join-Path $env:CVC_INSTALL_DIR 'lib\pkgconfig'
if (-not (Test-Path $pcDir)) { New-Item -ItemType Directory -Force -Path $pcDir | Out-Null }
Set-Content -NoNewline -Path (Join-Path $pcDir 'aspell.pc') -Value $pc

Invoke-CvcRewriteInstallPaths
