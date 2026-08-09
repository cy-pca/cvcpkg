# recipes/greenlet-cp313/build.ps1 — Windows from-source build of greenlet 3.5.3 for
# the cp313 interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time.
#
# DELTA vs build.sh: none of substance. greenlet's MSVC path uses the
# switch_x64_masm.obj / switch_arm64_masm.obj prebuilt in the sdist and links only
# the CRT, so there is no rpath or DLL-search problem to solve.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "greenlet-cp313: building with $py"

# Bridge BUILD-only python columns (setuptools, ...) into the DEPS-prefix
# interpreter — same reason as build.sh: they live in CVC_BUILD_PREFIX and
# --no-build-isolation imports them straight off sys.path.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
if ($LASTEXITCODE -ne 0) { throw "greenlet-cp313: setuptools not importable from the build prefix" }

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "greenlet-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "greenlet-cp313: no wheel produced under $wheelhouse" }
Write-Output "greenlet-cp313: built $($wheel.Name)"

# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree, so installing --prefix into
# the initially-empty per-recipe dir is what keeps the staged tree pure.
& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "greenlet-cp313: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "greenlet-cp313: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "greenlet-cp313: staged into $sitePackages"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import greenlet
from greenlet import _greenlet

assert _greenlet.__file__.endswith((".so", ".pyd", ".dylib")), _greenlet.__file__
print("greenlet", greenlet.__version__, "->", _greenlet.__file__)

# Drive a real stack switch in both directions. This is the whole package: if the
# per-architecture assembly in switch_*.h were wrong for this platform it would
# crash or corrupt here, not at import.
main = greenlet.getcurrent()
log = []

def child():
    log.append("child-start")
    main.switch()
    log.append("child-resume")
    return "child-done"

g = greenlet.greenlet(child)
g.switch()
log.append("main")
result = g.switch()
assert log == ["child-start", "main", "child-resume"], log
assert result == "child-done", result
assert g.dead, "greenlet did not finish"

# And that an exception propagates back across the switch boundary.
def boom():
    raise ValueError("expected")

b = greenlet.greenlet(boom)
try:
    b.switch()
except ValueError as exc:
    assert str(exc) == "expected", exc
else:
    raise AssertionError("exception did not cross the greenlet boundary")

print("greenlet round-trip OK")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "greenlet-cp313: verification failed ($LASTEXITCODE)" }

Write-Output "greenlet-cp313: build + verification complete"
