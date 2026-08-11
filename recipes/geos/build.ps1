# recipes/geos/build.ps1 — GEOS on Windows with MSVC + Ninja.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DBUILD_SHARED_LIBS=$buildSharedLibs",
    "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    # Nothing downstream consumes these, and the test suite roughly doubles the
    # build. shapely links libgeos_c only.
    '-DBUILD_TESTING=OFF',
    '-DBUILD_DOCUMENTATION=OFF',
    '-DBUILD_BENCHMARKS=OFF',
    # The C API is the ABI-stable one and the only thing shapely binds to;
    # building it is not optional here even though upstream defaults it on.
    '-DBUILD_GEOSOP=OFF'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw 'geos: cmake configure failed' }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'geos: cmake build failed' }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw 'geos: cmake install failed' }

# shapely resolves GEOS through geos-config on POSIX and through the C library
# + headers on Windows. Prove both landed rather than discovering it inside
# shapely's build, where the error is about a missing "geos_c" and says nothing
# about this package.
$hdr = Join-Path $env:CVC_INSTALL_DIR 'include\geos_c.h'
if (-not (Test-Path $hdr)) { throw "geos: geos_c.h missing from the staged prefix ($hdr)" }
$lib = @(Get-ChildItem -Path (Join-Path $env:CVC_INSTALL_DIR 'lib') -Filter 'geos_c*' -ErrorAction SilentlyContinue)
if (-not $lib) { throw 'geos: no geos_c import library staged under lib/' }
Write-Host "geos: staged $($lib[0].Name) and geos_c.h"
