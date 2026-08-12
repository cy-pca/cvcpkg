# recipes/qhull/build.ps1 — build Qhull 8.0.2 (CMake) and ship a qhull_r.pc
# (see build.sh for the why; matplotlib's -Dsystem-qhull needs the .pc).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @()

$pcdir = Join-Path $env:CVC_INSTALL_DIR 'lib\pkgconfig'
New-Item -ItemType Directory -Force -Path $pcdir | Out-Null
@'
prefix=${pcfiledir}/../..
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: qhull_r
Description: Qhull reentrant library — convex hulls, Delaunay, Voronoi
URL: http://www.qhull.org/
Version: 8.0.2
Libs: -L${libdir} -lqhull_r
Cflags: -I${includedir}
'@ | Set-Content -NoNewline -Encoding ascii (Join-Path $pcdir 'qhull_r.pc')

if (-not (Test-Path (Join-Path $pcdir 'qhull_r.pc'))) { throw 'qhull: qhull_r.pc missing' }
