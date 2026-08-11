# recipes/shapely-cp312/build.ps1 — build Shapely FROM SOURCE against cvcpkg's
# GEOS, then install the wheel into the python312 column.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "shapely-cp312: building with $py"

# Bridge the build-only backends (Cython, numpy headers, setuptools) onto the
# import path; --no-build-isolation cannot fetch them.
$env:PYTHONPATH = (@(
    (Join-Path $bld 'Lib\site-packages'),
    (Join-Path $deps 'Lib\site-packages')
) + @($env:PYTHONPATH | Where-Object { $_ })) -join ';'

# ── Point shapely at OUR GEOS, explicitly ──────────────────────────────────
# On POSIX shapely's setup.py shells out to `geos-config`. On Windows there is
# no geos-config, and it falls back to GEOS_INCLUDE_PATH / GEOS_LIBRARY_PATH.
# Setting them is not optional: without them the build either fails with a
# missing geos_c.h or, worse, finds some other libgeos on the machine and links
# a GEOS cvcpkg did not build — which is the whole reason this is a
# hand-written recipe rather than a generated one.
$geosInc = Join-Path $deps 'include'
$geosLib = Join-Path $deps 'lib'
if (-not (Test-Path (Join-Path $geosInc 'geos_c.h'))) {
    throw ("shapely-cp312: geos_c.h not found under $geosInc. The `geos` package " +
           "must be installed into the dependency prefix first (it is declared " +
           "in depends.build).")
}
$env:GEOS_INCLUDE_PATH = $geosInc
$env:GEOS_LIBRARY_PATH = $geosLib
Write-Output "shapely-cp312: GEOS headers $geosInc, libs $geosLib"

# geos_c.dll has to be importable at RUNTIME too. The python31X recipe's
# cvcpkg-dll-directories.pth adds <prefix>\bin via os.add_dll_directory at
# startup, which is what makes this work without touching PATH — the same
# mechanism numpy relies on for openblas.dll.
$env:PATH = (Join-Path $deps 'bin') + ";$env:PATH"

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "shapely-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'shapely-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "shapely-cp312: no wheel produced under $wheelhouse" }
Write-Output "shapely-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "shapely-cp312: pip install failed ($LASTEXITCODE)" }

# Verify against the RUNTIME closure, and exercise GEOS rather than just
# importing: a shapely that imports but cannot run a predicate has not been
# shown to have found its engine.
$env:PYTHONPATH = "$(Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages');$(Join-Path $deps 'Lib\site-packages')"
& $py -c @"
import shapely
from shapely.geometry import Polygon, Point
print('shapely', shapely.__version__, 'GEOS', shapely.geos_version_string)
a = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
b = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
assert a.intersection(b).area == 1.0, a.intersection(b).area
assert a.contains(Point(1, 1))
assert round(Point(0, 0).buffer(1.0).area, 2) == 3.14
print('shapely predicates + buffer OK')
"@
if ($LASTEXITCODE -ne 0) { throw "shapely-cp312: verification failed ($LASTEXITCODE)" }
Write-Output "shapely-cp312 build + verification complete"
