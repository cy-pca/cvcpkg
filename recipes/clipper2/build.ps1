# recipes/clipper2/build.ps1 — Clipper2 2.0.1 (C++), static, MSVC.
# See build.sh: the CMake project is in CPP/, and the library is static because
# its C++ API has no DLL export annotations.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$licenseFile = Join-Path $env:CVC_SOURCE_DIR 'LICENSE'
$env:CVC_SOURCE_DIR = Join-Path $env:CVC_SOURCE_DIR 'CPP'

Invoke-CvcCMakeBuild @(
    '-DBUILD_SHARED_LIBS=OFF',
    '-DCMAKE_INSTALL_LIBDIR=lib',
    '-DCLIPPER2_UTILS=OFF',
    '-DCLIPPER2_EXAMPLES=OFF',
    '-DCLIPPER2_TESTS=OFF',
    '-DCLIPPER2_USINGZ=ON'
)

$share = Join-Path $env:CVC_INSTALL_DIR 'share\clipper2'
New-Item -ItemType Directory -Force -Path $share | Out-Null
Copy-Item -Force $licenseFile (Join-Path $share 'LICENSE')

foreach ($f in @('lib\Clipper2.lib', 'lib\Clipper2Z.lib', 'include\clipper2\clipper.h',
                 'lib\cmake\clipper2\Clipper2Config.cmake')) {
    if (-not (Test-Path (Join-Path $env:CVC_INSTALL_DIR $f))) { throw "clipper2: $f not installed" }
}
Write-Host "clipper2: installed static Clipper2 and Clipper2Z"
