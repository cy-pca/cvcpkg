# recipes/h5py-cp311/build.ps1 — build h5py 3.16.0 FROM SOURCE on Windows against
# cvcpkg's OWN HDF5 (recipes/hdf5: hdf5.dll + hdf5_hl.dll), for the cp311 column.
#
# Windows counterpart of build.sh; same contract (see that file for the why). The
# deltas:
#   * setuptools compiles the Cython extensions with MSVC (python-wheel.ps1 trims
#     PATH and drops MinGW for it).
#   * HDF5_DIR=<prefix>: h5py takes headers from include\ and the import libraries
#     hdf5.lib / hdf5_hl.lib from lib\, and on Windows adds bin\ itself; its own
#     Windows defines (_HDF5USEDLL_, H5_BUILT_AS_DYNAMIC_LIB) select the DLL ABI.
#   * No rpath pass: Windows has none. The extensions link hdf5.dll by name and
#     resolve it at import out of <prefix>\bin, which the python311 recipe's
#     cvcpkg-dll-directories.pth registers (as numpy does for openblas.dll). The
#     verify below registers bin\ itself, as build.sh's sets LD_LIBRARY_PATH.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"   # Get-CvcPythonExe; prefixes' bin\ on PATH

$py = Get-CvcPythonExe
$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "h5py-cp311: building with $py"

# Bridge the build-only backends (cython, pkgconfig, packaging, setuptools, wheel)
# from the build prefix onto the interpreter's import path; --no-build-isolation
# cannot fetch them.
$env:PYTHONPATH = (@(
    (Join-Path $bld 'Lib\site-packages'),
    (Join-Path $deps 'Lib\site-packages')
) + @($env:PYTHONPATH | Where-Object { $_ })) -join ';'

# ── Point h5py's build at OUR HDF5 ──────────────────────────────────────────
# hdf5 is both a build and a runtime dep; take whichever prefix holds it.
$hdf5 = @($deps, $bld) | Where-Object { Test-Path (Join-Path $_ 'include\hdf5.h') } |
    Select-Object -First 1
if (-not $hdf5) { throw "h5py-cp311: include\hdf5.h in neither $deps nor $bld; is the hdf5 dep in the closure?" }
$env:HDF5_DIR = $hdf5
# MUST track recipes/hdf5 upstream_version (build.sh pins the same).
$env:HDF5_VERSION = '1.14.4'
$env:HDF5_MPI = 'OFF'
Write-Output "h5py-cp311: HDF5_DIR=$env:HDF5_DIR  HDF5_VERSION=$env:HDF5_VERSION"

# ── Build the wheel (offline, no isolation) and install it ──────────────────
$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "h5py-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'h5py-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "h5py-cp311: no wheel produced under $wheelhouse" }
Write-Output "h5py-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "h5py-cp311: pip install failed ($LASTEXITCODE)" }

# ── Verify: a real HDF5 round-trip through the cvcpkg hdf5.dll ──────────────
# Register the prefixes' bin\ (hdf5.dll, hdf5_hl.dll, zlib1.dll) first: since
# Python 3.8 the loader ignores PATH for an extension module's dependent DLLs.
$env:PYTHONPATH = "$(Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages');$env:PYTHONPATH"
$binLits = ((@((Join-Path $deps 'bin'), (Join-Path $bld 'bin')) |
    Where-Object { Test-Path $_ } | Select-Object -Unique) |
    ForEach-Object { "r'" + $_ + "'" }) -join ', '
& $py -c @"
import os
_dll_cookies = [os.add_dll_directory(d) for d in [$binLits]]
import tempfile, numpy as np, h5py

exp = np.arange(24, dtype='f8').reshape(4, 6)
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'roundtrip.h5')
    with h5py.File(path, 'w') as f:
        f.create_dataset('cube', data=exp)
        f.attrs['stamp'] = 'cvcpkg'
    with h5py.File(path, 'r') as f:
        got = f['cube'][()]
        assert f.attrs['stamp'] == 'cvcpkg'

assert got.shape == exp.shape, got.shape
assert np.array_equal(got, exp), 'h5py round-trip mismatch'
linked = h5py.version.hdf5_version
print('h5py', h5py.__version__, '-> HDF5', linked, '(numpy', np.__version__ + ')')
print('h5py.__file__:', h5py.__file__)
assert linked.startswith('1.14.'), 'linked HDF5 %s is not the cvcpkg hdf5 build' % linked
assert not [n for n in os.listdir(os.path.dirname(h5py.__file__)) if n.lower().endswith('.dll')], \
    'h5py/ carries its own DLLs; it must use the prefix hdf5'
print('h5py-cp311 round-trip OK')
"@
if ($LASTEXITCODE -ne 0) { throw "h5py-cp311: verification failed ($LASTEXITCODE)" }
Write-Output "h5py-cp311: build + verification complete"
