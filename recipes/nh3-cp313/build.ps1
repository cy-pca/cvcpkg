# recipes/nh3-cp313/build.ps1 — Windows from-source build of nh3 0.3.6
# for the cp313 interpreter column (maturin + cvcpkg's Rust, MSVC from
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
Write-Output "nh3-cp313: building with $py"

$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import maturin; print("maturin backend", maturin.__file__)'
if ($LASTEXITCODE -ne 0) { throw "nh3-cp313: maturin backend not importable from the build prefix" }
$maturinCmd = Get-Command maturin -ErrorAction SilentlyContinue
if (-not $maturinCmd) { throw "nh3-cp313: no 'maturin' executable on PATH (maturin-cp313 supplies it in $bld\Scripts)" }
& $maturinCmd.Source --version

function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "nh3-cp313: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "nh3-cp313: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
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
$env:CARGO_NET_OFFLINE = if ($env:CVC_CARGO_OFFLINE) { 'true' } else { 'false' }

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "nh3-cp313: pip wheel failed ($LASTEXITCODE)" }
$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "nh3-cp313: no wheel produced under $wheelhouse" }
Write-Output "nh3-cp313: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "nh3-cp313: pip install failed ($LASTEXITCODE)" }
$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) { throw "nh3-cp313: no Lib\site-packages under $env:CVC_INSTALL_DIR" }

$env:PYTHONPATH = $sitePackages
$check = @'
import nh3
from nh3 import nh3 as _ext
print("nh3", nh3.__file__, "->", _ext.__file__)
out = nh3.clean("<b>ok</b><script>alert(1)</script><a href='javascript:x'>l</a>")
assert out == "<b>ok</b><a rel=\"noopener noreferrer\">l</a>", out
assert nh3.is_html("<p>x</p>") and not nh3.is_html("plain")
print("nh3.clean OK:", out)
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "nh3-cp313: verification failed ($LASTEXITCODE)" }
Write-Output "nh3-cp313: build + verification complete"
