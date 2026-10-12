# recipes/orjson-cp312/build.ps1 — Windows from-source build of orjson 3.11.8
# for the cp312 interpreter column (maturin + cvcpkg's Rust, MSVC from
# _common/env-windows.ps1). Same mechanic as build.sh and as
# recipes/pydantic-core-cp312/build.ps1; no rpath/DLL staging — the extension has
# no external DLL dependency.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe/link.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "orjson-cp312: building with $py"

$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import maturin; print("maturin backend", maturin.__file__)'
if ($LASTEXITCODE -ne 0) { throw "orjson-cp312: maturin backend not importable from the build prefix" }
$maturinCmd = Get-Command maturin -ErrorAction SilentlyContinue
if (-not $maturinCmd) { throw "orjson-cp312: no 'maturin' executable on PATH (maturin-cp312 supplies it in $bld\Scripts)" }
& $maturinCmd.Source --version

function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "orjson-cp312: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "orjson-cp312: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
    }
    return $path
}
$env:CARGO = Resolve-CvcPrefixTool 'cargo'
$env:RUSTC = Resolve-CvcPrefixTool 'rustc'
& $env:CARGO --version
& $env:RUSTC --version

$env:CARGO_HOME = if ($env:CVC_CARGO_HOME) { $env:CVC_CARGO_HOME } else { Join-Path $env:CVC_BUILD_DIR 'cargo-home' }
$env:RUSTUP_HOME = Join-Path $env:CVC_BUILD_DIR 'rustup-home'
Remove-Item Env:\RUSTUP_TOOLCHAIN -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $env:CARGO_HOME, $env:RUSTUP_HOME | Out-Null
$env:CARGO_TARGET_DIR = Join-Path $env:CVC_BUILD_DIR 'cargo-target'
$env:MATURIN_NO_INSTALL_RUST = '1'
$env:CARGO_NET_OFFLINE = 'true'   # the sdist vendors every crate

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "orjson-cp312: pip wheel failed ($LASTEXITCODE)" }
$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "orjson-cp312: no wheel produced under $wheelhouse" }
Write-Output "orjson-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "orjson-cp312: pip install failed ($LASTEXITCODE)" }
$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) { throw "orjson-cp312: no Lib\site-packages under $env:CVC_INSTALL_DIR" }

$env:PYTHONPATH = $sitePackages
$check = @'
import datetime, json, orjson
print("orjson", orjson.__version__, "->", orjson.__file__)
doc = {"a": [1, 2.5, None, True], "s": "ünïcode", "dt": datetime.datetime(2026, 10, 11, 12, 0, 0)}
raw = orjson.dumps(doc, option=orjson.OPT_SORT_KEYS)
assert json.loads(raw) == {"a": [1, 2.5, None, True], "dt": "2026-10-11T12:00:00", "s": "ünïcode"}, raw
assert orjson.loads(raw)["s"] == "ünïcode"
print("orjson dumps/loads OK:", raw)
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "orjson-cp312: verification failed ($LASTEXITCODE)" }
Write-Output "orjson-cp312: build + verification complete"
