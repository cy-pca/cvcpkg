# recipes/scipy-cp312/build.ps1 — build SciPy FROM SOURCE (meson-python) with
# the MinGW-w64 toolchain, against cvcpkg's OpenBLAS.
#
# Why not MSVC, like numpy-cp312 next door: SciPy needs a Fortran compiler and
# MSVC has none. Per SciPy's build docs Windows accepts only MinGW-w64 gfortran
# or Intel Fortran, LLVM flang is unsupported, and SciPy's own CI builds
# gcc/g++/gfortran throughout rather than mixing MSVC for C/C++ with gfortran
# for Fortran. So this recipe drives the whole build through mingw-w64-gcc.
#
# Mixing that with cvcpkg's MSVC-built CPython is fine and is what the
# ecosystem has always done: the extension modules cross a C ABI boundary, and
# mingw-w64-gcc is the UCRT flavour, so both sides link the same C runtime.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"   # scrubs \msys64\ from PATH

$py = Get-CvcPythonExe
$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "scipy-cp312: building with $py"

# ── The Fortran toolchain ───────────────────────────────────────────────────
# mingw-w64-gcc is a host_tool, so it is staged into the build prefix. Resolve
# it explicitly instead of trusting PATH: an ambient gfortran (C:\Strawberry,
# msys64) deciding how SciPy's LAPACK bindings compile is the exact
# non-hermeticity the recipe set exists to prevent.
$gccBin = @(
    (Join-Path $bld 'bin'), (Join-Path $deps 'bin')
) | Where-Object { Test-Path (Join-Path $_ 'gfortran.exe') } | Select-Object -First 1

if (-not $gccBin) {
    throw ("scipy-cp312: no gfortran in the build prefix. SciPy cannot be built " +
           "without a Fortran compiler, and MSVC has none. Install the " +
           "mingw-w64-gcc cvcpkg package into $bld (it is declared in " +
           "depends.host_tools, so this means the host-tool staging did not run).")
}

# The GNU toolchain goes FIRST, so meson's compiler probes hit gcc rather than
# whatever cl.exe an outer vcvars64 left in the environment.
$env:PATH = "$gccBin;$env:PATH"
$env:CC  = Join-Path $gccBin 'gcc.exe'
$env:CXX = Join-Path $gccBin 'g++.exe'
$env:FC  = Join-Path $gccBin 'gfortran.exe'
$env:AR  = Join-Path $gccBin 'ar.exe'
# meson prefers MSVC on Windows whenever it can find it; naming the linker is
# how you tell it not to. Without this, a vcvars-provided cl.exe/link.exe wins
# over $env:CC and the C/C++ half of SciPy compiles MSVC while the Fortran half
# compiles GNU — which links, and then crashes at import.
#
# The value is a linker FAMILY, not an executable name: meson accepts only
# bfd/eld/gold/lld and rejects 'ld' outright ("Unsupported linker ... not ld").
# mingw-w64's default is GNU ld, whose family name is bfd.
$env:CC_LD = 'bfd'
$env:CXX_LD = 'bfd'
$env:FC_LD = 'bfd'
Write-Output "scipy-cp312: gfortran $(& $env:FC -dumpversion) from $gccBin"

# ── Build backends on the import path (no isolation, so pip cannot fetch) ───
$env:PYTHONPATH = (@(
    (Join-Path $bld 'Lib\site-packages'),
    (Join-Path $deps 'Lib\site-packages')
) + @($env:PYTHONPATH | Where-Object { $_ })) -join ';'

# Scripts\ as well as bin\: meson discovers Cython as a COMPILER by running
# `cython` off PATH, and Windows console scripts install to <prefix>\Scripts.
# Appended AFTER $gccBin so the GNU compilers still win.
$env:PATH = $env:PATH + ";" + (@(
    (Join-Path $bld 'Scripts'), (Join-Path $deps 'Scripts')
) -join ';')

# ── Hermetic pkg-config so meson finds OUR openblas and nothing else ────────
# PKG_CONFIG_LIBDIR *replaces* the default search path. No fallback to a
# pkg-config on PATH: that resolves to C:\Strawberry\perl\bin\pkg-config.bat
# here, and a tool from outside the prefix deciding how we link BLAS is
# precisely what this recipe must not allow.
$pkgconf = @(
    (Join-Path $bld 'bin\pkg-config.exe'),
    (Join-Path $bld 'bin\pkgconf.exe'),
    (Join-Path $deps 'bin\pkg-config.exe'),
    (Join-Path $deps 'bin\pkgconf.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $pkgconf) { throw "scipy-cp312: no pkg-config in the dependency prefix" }

$env:PKG_CONFIG = $pkgconf

# pybind11 ships its pkgconfig and cmake files INSIDE the python package
# (site-packages\pybind11\share\...), not in the prefix's lib\pkgconfig. Since
# PKG_CONFIG_LIBDIR replaces the search path outright, those directories have
# to be named explicitly or scipy's meson fails with
#   Dependency "pybind11" not found (tried pkg-config, config-tool and cmake)
# Resolved by asking the module where it lives, so no version is hardcoded.
$pybindShare = & $py -c "import pybind11,os;print(os.path.join(os.path.dirname(pybind11.__file__),'share'))"
if ($LASTEXITCODE -ne 0 -or -not $pybindShare) { throw "scipy-cp312: pybind11 not importable by $py" }

$pcPath = @(
    (Join-Path $deps 'lib\pkgconfig'),
    (Join-Path $bld 'lib\pkgconfig'),
    (Join-Path $pybindShare 'pkgconfig')
) -join ';'
$env:PKG_CONFIG_PATH = $pcPath
$env:PKG_CONFIG_LIBDIR = $pcPath
# cmake is meson's third probe for pybind11; give it the package's own config.
$env:CMAKE_PREFIX_PATH = "$pybindShare;$env:CMAKE_PREFIX_PATH"

& $env:PKG_CONFIG --exists openblas
if ($LASTEXITCODE -ne 0) {
    throw "scipy-cp312: openblas.pc not found by $pkgconf (PKG_CONFIG_LIBDIR=$pcPath)"
}
Write-Output "scipy-cp312: openblas $(& $env:PKG_CONFIG --modversion openblas) via $pkgconf"

# ── Build the wheel (offline, no isolation) ─────────────────────────────────
$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

# Parallelism is bounded by MEMORY here, not core count. SciPy's heavy C++
# targets (HiGHS, SuperLU, the _ufuncs translation units) run well over 1 GB
# of compiler per job, and Cython's own codegen processes pile on top. On a
# 20-core / 16 GB box, -j20 exhausted RAM and the pagefile at target 79 of
# 1162 and surfaced as
#   ImportError: DLL load failed while importing FlowControl:
#   The paging file is too small for this operation to complete.
# which looks like a broken Cython install and is nothing of the kind.
#
# ~3 GB per job matches the observed peak. An explicit CVC_JOBS still wins:
# a builder that knows it has the headroom should not be second-guessed.
$cores = [Environment]::ProcessorCount
if ($env:CVC_JOBS) {
    $jobs = [int]$env:CVC_JOBS
} else {
    $ramGB = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB
    $byRam = [Math]::Max(2, [Math]::Floor($ramGB / 3))
    $jobs = [Math]::Min($cores, $byRam)
}
Write-Output "scipy-cp312: building with -j$jobs (of $cores cores)"

& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse `
    -C setup-args=-Dblas=openblas `
    -C setup-args=-Dlapack=openblas `
    -C setup-args=-Duse-pythran=true `
    -C builddir="$(Join-Path $env:CVC_BUILD_DIR 'meson')" `
    -C compile-args="-j$jobs" `
    $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) {
    # meson's own log says WHY a probe failed; pip only relays its terse
    # "not found". Without this a BLAS/Fortran miss is undebuggable from a log.
    $log = Join-Path $env:CVC_BUILD_DIR 'meson\meson-logs\meson-log.txt'
    Write-Output "----- meson-log.txt ($log) -----"
    if (Test-Path $log) { Get-Content $log -Tail 200 | Write-Output } else { Write-Output "(no meson log)" }
    throw "scipy-cp312: pip wheel failed ($LASTEXITCODE)"
}

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'scipy-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "scipy-cp312: no wheel produced under $wheelhouse" }
Write-Output "scipy-cp312: built $($wheel.Name)"

& $py -m pip install --no-deps --no-index --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "scipy-cp312: pip install failed ($LASTEXITCODE)" }

# ── Verify: LAPACK, not just BLAS, and nothing vendored ─────────────────────
# cvcpkg's Windows OpenBLAS is built -DNOFORTRAN=1, so its LAPACK comes from
# the f2c'd netlib sources rather than the Fortran ones. numpy barely notices;
# SciPy leans on LAPACK hard. Exercise a real factorisation here so an
# inadequate LAPACK fails the BUILD rather than some user's solve() later.
$env:PYTHONPATH = "$(Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages');$env:PYTHONPATH"
& $py -c @"
import os
import numpy as np
import scipy
from scipy import linalg, ndimage

a = np.array([[3.0, 1.0], [1.0, 2.0]])
x = linalg.solve(a, np.array([9.0, 8.0]))          # LAPACK gesv
assert np.allclose(a @ x, [9.0, 8.0]), x
w = linalg.eigvalsh(a)                              # LAPACK syevd
assert np.all(w > 0), w
q, r = linalg.qr(a)                                 # LAPACK geqrf
assert np.allclose(q @ r, a)

# the function this whole exercise was for: geometry-scene-gen's clearance field
d = ndimage.distance_transform_edt(np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]]))
assert np.isclose(d[1, 1], 0.0) and np.isclose(d[0, 0], 1.4142135623730951), d

sd = os.path.dirname(scipy.__file__)
vendored = [x for x in os.listdir(sd) if x.endswith('.libs')]
assert not vendored, 'vendored libs present: %s' % vendored
print('scipy', scipy.__version__, 'from', scipy.__file__)
"@
if ($LASTEXITCODE -ne 0) { throw "scipy-cp312: verification failed ($LASTEXITCODE)" }
Write-Output "scipy-cp312 build + verification complete"
