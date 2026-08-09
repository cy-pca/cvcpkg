# recipes/cffi-cp313/build.ps1 — Windows from-source build of cffi 2.0.0 for
# the cp313 interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time.
#
# DELTA vs build.sh — this is the interesting one. On MSVC, cffi's setup.py does
# NOT call pkg-config at all: it compiles the libffi subset vendored in its own
# sdist (src/c/libffi_x86_x64, the copy CPython itself ships) straight into
# _cffi_backend.pyd. So there is no external libffi to find, no probe to pin, and
# no rpath to stamp — which is also why recipe.yaml restricts the libffi edge to
# the non-Windows platforms.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "cffi-cp313: building with $py"

# Bridge BUILD-only python columns (setuptools, ...) into the DEPS-prefix
# interpreter — same reason as build.sh: they live in CVC_BUILD_PREFIX and
# --no-build-isolation imports them straight off sys.path.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
if ($LASTEXITCODE -ne 0) { throw "cffi-cp313: setuptools not importable from the build prefix" }

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "cffi-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "cffi-cp313: no wheel produced under $wheelhouse" }
Write-Output "cffi-cp313: built $($wheel.Name)"

# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree, so installing --prefix into
# the initially-empty per-recipe dir is what keeps the staged tree pure.
& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "cffi-cp313: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "cffi-cp313: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "cffi-cp313: staged into $sitePackages"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import cffi, _cffi_backend
from cffi import FFI

assert _cffi_backend.__file__.endswith((".so", ".pyd", ".dylib")), _cffi_backend.__file__
print("cffi", cffi.__version__, "->", _cffi_backend.__file__)

ffi = FFI()
ffi.cdef("size_t strlen(const char *);")

# The type engine round-trip (no libffi involved).
buf = ffi.new("char[]", b"cvcpkg")
assert ffi.string(buf) == b"cvcpkg", ffi.string(buf)

# The libffi round-trip: call a real C function through a synthesised call frame.
# If we had linked a broken or mismatched libffi this is where it shows up, not at
# import. dlopen(NULL) — the process's own symbol namespace — is the POSIX way in,
# but cffi refuses it on Windows (OSError, see bpo-23606), so name the CRT there.
lib = ffi.dlopen("msvcrt.dll") if sys.platform == "win32" else ffi.dlopen(None)
assert lib.strlen(b"cvcpkg") == 6, lib.strlen(b"cvcpkg")

# pycparser is a real runtime edge (cffi.cparser imports it) — prove it resolves
# from the prefix rather than only happening to be present on the builder.
import pycparser
print("pycparser    :", pycparser.__file__)
print("cffi round-trip OK")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "cffi-cp313: verification failed ($LASTEXITCODE)" }

Write-Output "cffi-cp313: build + verification complete"
