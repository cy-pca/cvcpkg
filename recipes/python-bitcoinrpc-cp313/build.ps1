# recipes/python-bitcoinrpc-cp313/build.ps1 — build python-bitcoinrpc 1.0 FROM SOURCE (hand-written).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "python-bitcoinrpc-cp313: building with $py"

if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "python-bitcoinrpc-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "python-bitcoinrpc-cp313: no wheel produced under $wheelhouse" }
Write-Output "python-bitcoinrpc-cp313: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "python-bitcoinrpc-cp313: pip install failed ($LASTEXITCODE)" }

$env:PYTHONPATH = ''
Invoke-CvcPythonCheck 'from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
proxy = AuthServiceProxy("http://user:pass@127.0.0.1:1", timeout=2)
try:
    proxy.getblockcount()
except (OSError, ConnectionError) as e:
    print("bitcoinrpc: no node on 127.0.0.1:1, as expected:", type(e).__name__)
else:
    raise AssertionError("getblockcount() against a closed port returned?")
assert issubclass(JSONRPCException, Exception)'
