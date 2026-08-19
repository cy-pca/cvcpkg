# recipes/numpy-cp311/build.ps1 — build NumPy FROM SOURCE (meson-python) against
# cvcpkg's OpenBLAS on Windows/MSVC, then install the wheel into the python311
# interpreter's site-packages.
#
# Windows counterpart of build.sh; see that file for WHY from source (the PyPI
# wheel bundles its own auditwheel-vendored OpenBLAS, which is exactly the
# non-hermeticity this recipe exists to avoid).
#
# The one structural difference from the POSIX script: there is no RUNPATH on
# Windows, so there is no patchelf pass. numpy's extensions link openblas.dll by
# name and resolve it at import out of <prefix>\bin. Downstream that directory is
# registered by the python311 recipe's cvcpkg-dll-directories.pth (the Windows
# counterpart of $ORIGIN, run via os.add_dll_directory at interpreter startup).
# The in-build verify below does NOT assume that .pth is present — it registers
# <prefix>\bin itself, exactly as the POSIX verify sets LD_LIBRARY_PATH — so the
# build proves the gemm path regardless of the interpreter's startup hooks.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"   # also scrubs MinGW from PATH

# numpy ships its OWN vendored meson (vendored-meson/meson/meson.py) and pins it
# in pyproject.toml ([tool.meson-python] meson = 'vendored-meson/meson/meson.py')
# because meson_cpu's SIMD dispatch uses numpy's custom `features` meson module
# that stock meson lacks — otherwise: meson_cpu/x86/meson.build ERROR: Module
# "features" does not exist. python-wheel.ps1 sets $env:MESON (so meson-backed
# wheels can find cvcpkg's meson), but that env var OVERRIDES numpy's pyproject
# pin. Clear it here so meson-python honors numpy's vendored meson.
Remove-Item Env:\MESON -ErrorAction SilentlyContinue

$py = Get-CvcPythonExe
$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "numpy-cp311: building with $py"

# ── Build backends + native CLIs ────────────────────────────────────────────
# meson-python / Cython / packaging / pyproject-metadata are build-only deps and
# land in CVC_BUILD_PREFIX; --no-build-isolation cannot fetch them, so bridge
# them onto the build interpreter's import path.
$env:PYTHONPATH = (@(
    (Join-Path $bld 'Lib\site-packages'),
    (Join-Path $deps 'Lib\site-packages')
) + @($env:PYTHONPATH | Where-Object { $_ })) -join ';'

# Scripts\ as well as bin\: Windows console scripts (cython.exe, meson.exe)
# install to <prefix>\Scripts, and meson discovers Cython as a COMPILER by
# running `cython` off PATH. With only bin\ here, meson fails with
#   ERROR: Unknown compiler(s): [['cython'], ['cython3']]
# even though Cython is installed and importable.
$env:PATH = (@(
    (Join-Path $bld 'Scripts'), (Join-Path $bld 'bin'),
    (Join-Path $deps 'Scripts'), (Join-Path $deps 'bin')
) -join ';') + ";$env:PATH"

# ── Hermetic pkg-config so meson finds OUR openblas and nothing else ────────
# PKG_CONFIG_LIBDIR *replaces* the default search path, so no stray system .pc
# can leak in. openblas's module name is `openblas` (LP64, no symbol suffix).
#
# Deliberately NO fallback to whatever pkg-config is on PATH: that selected
# C:\Strawberry\perl\bin\pkg-config.bat here, and a tool from outside the
# dependency prefix deciding how we link BLAS is precisely the non-hermeticity
# this recipe exists to prevent.
#
# NOTE: this REQUIRES a pkg-config in the prefix. numpy's meson probes BLAS with
# pkgconfig and `system` only — never cmake — so shipping lib/cmake/OpenBLAS is
# not enough ("Run-time dependency openblas found: NO (tried pkgconfig,
# pkgconfig, pkgconfig and system)"). The `pkg-config` recipe cannot supply that
# on Windows: it is autotools and its configure dies "C compiler cannot create
# executables" under MSVC once MinGW is off PATH. A `pkgconf` recipe (meson,
# MSVC-clean) is the missing piece.
$pkgconf = @(
    (Join-Path $bld 'bin\pkg-config.exe'),
    (Join-Path $bld 'bin\pkgconf.exe'),
    (Join-Path $deps 'bin\pkg-config.exe'),
    (Join-Path $deps 'bin\pkgconf.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $pkgconf) {
    throw ("numpy-cp311: no pkg-config in the dependency prefix. numpy's meson " +
           "resolves BLAS via pkgconfig/system only, so openblas cannot be found " +
           "without it and the build fails with 'No BLAS library detected!'. " +
           "Install a pkgconf/pkg-config cvcpkg package into $deps. Note the " +
           "existing pkg-config recipe does NOT build on Windows (autotools " +
           "configure under MSVC); a pkgconf recipe is required.")
}

$env:PKG_CONFIG = $pkgconf
$pcPath = "$(Join-Path $deps 'lib\pkgconfig');$(Join-Path $bld 'lib\pkgconfig')"
$env:PKG_CONFIG_PATH = $pcPath
$env:PKG_CONFIG_LIBDIR = $pcPath

# Fail HERE, with the search path in hand, rather than 200 lines into meson's
# generic no-BLAS message.
& $env:PKG_CONFIG --exists openblas
if ($LASTEXITCODE -ne 0) {
    Get-ChildItem (Join-Path $deps 'lib\pkgconfig') -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name | Write-Output
    throw "numpy-cp311: openblas.pc not found by $pkgconf (PKG_CONFIG_LIBDIR=$pcPath)"
}
Write-Output "numpy-cp311: openblas $(& $env:PKG_CONFIG --modversion openblas) via $pkgconf"

# ── Build the wheel (offline, no isolation) ─────────────────────────────────
$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
$jobs = if ($env:CVC_JOBS) { $env:CVC_JOBS } else { [Environment]::ProcessorCount }

& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse `
    -C setup-args=-Dblas=openblas `
    -C setup-args=-Dlapack=openblas `
    -C setup-args=-Dallow-noblas=false `
    -C setup-args=-Duse-ilp64=false `
    -C builddir="$(Join-Path $env:CVC_BUILD_DIR 'meson')" `
    -C compile-args="-j$jobs" `
    $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) {
    # meson's own log says WHY a dependency probe failed; pip only relays its
    # terse "not found". Without this a BLAS miss is undebuggable from a job log.
    $log = Join-Path $env:CVC_BUILD_DIR 'meson\meson-logs\meson-log.txt'
    Write-Output "----- meson-log.txt ($log) -----"
    if (Test-Path $log) { Get-Content $log -Tail 200 | Write-Output } else { Write-Output "(no meson log)" }
    throw "numpy-cp311: pip wheel failed ($LASTEXITCODE)"
}

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'numpy-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "numpy-cp311: no wheel produced under $wheelhouse" }
Write-Output "numpy-cp311: built $($wheel.Name)"

& $py -m pip install --no-deps --no-index --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "numpy-cp311: pip install failed ($LASTEXITCODE)" }

# ── Verify: gemm actually runs, and NOTHING was vendored ────────────────────
# Register <prefix>\bin (where openblas.dll lives) on the DLL search path before
# importing numpy — the Windows analog of the POSIX verify's LD_LIBRARY_PATH.
# PATH is NOT consulted: since Python 3.8 the loader ignores it for an extension
# module's dependent DLLs (see python312/build.ps1). Doing it here means the
# build's own proof does not depend on the interpreter's startup .pth.
$env:PYTHONPATH = "$(Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages');$env:PYTHONPATH"
$binLits = ((@((Join-Path $deps 'bin'), (Join-Path $bld 'bin')) |
    Where-Object { Test-Path $_ } | Select-Object -Unique) |
    ForEach-Object { "r'" + $_ + "'" }) -join ', '
& $py -c @"
import os
_dll_cookies = []
for _d in [$binLits]:
    if os.path.isdir(_d) and hasattr(os, 'add_dll_directory'):
        _dll_cookies.append(os.add_dll_directory(_d))
import numpy as np
a = np.arange(12, dtype=np.float64).reshape(3, 4)
assert a.sum() == 66.0, a.sum()
assert (a @ a.T).shape == (3, 3)          # exercises the BLAS gemm path
nd = os.path.dirname(np.__file__)
vendored = [d for d in os.listdir(nd) if d.endswith('.libs')]
assert not vendored, 'vendored libs present: %s' % vendored
print('numpy', np.__version__, 'from', np.__file__)
"@
if ($LASTEXITCODE -ne 0) { throw "numpy-cp311: verification failed ($LASTEXITCODE)" }
Write-Output "numpy-cp311 build + verification complete"