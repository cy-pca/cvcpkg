# recipes/lupa-cp312/build.ps1 — Windows from-source build of lupa 2.8 for the
# cp312 interpreter column, against cvcpkg's lua (lua54.dll) and luajit
# (lua51.dll). Same mechanic as build.sh (`pip wheel --no-build-isolation
# --no-deps --no-index`, then pip-install into the staging prefix); MSVC comes
# from _common/env-windows.ps1.
#
# DELTA vs build.sh: Python 3.8+ removed PATH from the DLL search path used for
# extension modules, so lua54.dll / lua51.dll sitting in <prefix>\bin are NOT
# found by lupa\lua54*.pyd / luajit21*.pyd. The directory containing the .pyd
# IS searched, so both DLLs are staged next to them -- copies of our own
# libraries from the same prefix (the pyyaml-cp312 precedent), which is also
# what lupa's own Windows wheels do with their bundled DLLs.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
$tag = 'lupa-cp312'
Write-Output "${tag}: building with $py"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
if ($LASTEXITCODE -ne 0) { throw "${tag}: setuptools not importable from the build prefix" }
& $py -c 'import Cython; print("Cython", Cython.__version__)'
if ($LASTEXITCODE -ne 0) { throw "${tag}: Cython not importable from the build prefix" }

function Find-Dep([string]$rel) {
    foreach ($root in @($deps, $bld)) {
        if ($root -and (Test-Path -LiteralPath (Join-Path $root $rel))) { return $root }
    }
    throw "${tag}: $rel not found under $deps or $bld -- is that recipe in the closure?"
}
$luaRoot = Find-Dep 'include\lua.h'
$luajitRoot = Find-Dep 'include\luajit-2.1\luajit.h'
$luaDll = Join-Path (Find-Dep 'bin\lua54.dll') 'bin\lua54.dll'
$luajitDll = Join-Path (Find-Dep 'bin\lua51.dll') 'bin\lua51.dll'
Find-Dep 'lib\lua54.lib' | Out-Null
Find-Dep 'lib\lua51.lib' | Out-Null

# One extension per entry (lupa-external-lua.patch). ConvertTo-Json escapes
# the backslashes; -Depth keeps the nested arrays.
$env:LUPA_EXTERNAL_LUA = @{
    lua54    = @{ include_dirs = @("$luaRoot\include");
                  library_dirs = @("$luaRoot\lib"); libraries = @('lua54') }
    luajit21 = @{ include_dirs = @("$luajitRoot\include\luajit-2.1");
                  library_dirs = @("$luajitRoot\lib"); libraries = @('lua51') }
} | ConvertTo-Json -Compress -Depth 5
$env:LUPA_WITH_CYTHON = 'true'
Write-Output "${tag}: LUPA_EXTERNAL_LUA=$env:LUPA_EXTERNAL_LUA"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "${tag}: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "${tag}: no wheel produced under $wheelhouse" }
Write-Output "${tag}: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "${tag}: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
$lupaDir = Join-Path $sitePackages 'lupa'
if (-not (Test-Path -LiteralPath $lupaDir)) { throw "${tag}: no Lib\site-packages\lupa after pip install" }
foreach ($m in @('lua54', 'luajit21')) {
    if (-not (Get-ChildItem -Path $lupaDir -Filter "$m*.pyd")) { throw "${tag}: lupa\$m*.pyd was not built" }
}

# Stage the two DLLs beside the .pyd files -- see the header.
Copy-Item -LiteralPath $luaDll -Destination $lupaDir -Force
Copy-Item -LiteralPath $luajitDll -Destination $lupaDir -Force
Write-Output "${tag}: staged lua54.dll + lua51.dll -> $lupaDir"

$env:PYTHONPATH = "$sitePackages;$env:PYTHONPATH"
$check = @'
import lupa
import lupa.lua54 as L54
import lupa.luajit21 as LJ
rt = L54.LuaRuntime()
assert rt.eval("1+1") == 2
assert rt.eval("_VERSION") == "Lua 5.4", rt.eval("_VERSION")
jt = LJ.LuaRuntime()
assert jt.eval("1+1") == 2
assert jt.eval("_VERSION") == "Lua 5.1", jt.eval("_VERSION")
assert jt.eval("jit.version").startswith("LuaJIT 2.1"), jt.eval("jit.version")
assert rt.eval("function(f) return f(21) end")(lambda x: x * 2) == 42
assert lupa.LuaRuntime().eval("_VERSION") == "Lua 5.4"
print("lupa", lupa.__version__, "OK:", rt.eval("_VERSION"), "+", jt.eval("jit.version"))
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "${tag}: verification failed ($LASTEXITCODE)" }

Write-Output "${tag}: build + verification complete"
