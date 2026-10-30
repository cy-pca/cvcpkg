# recipes/zstandard-cp312/build.ps1 — build zstandard 0.25.0 FROM SOURCE (generated).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "zstandard-cp312: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "zstandard-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "zstandard-cp312: no wheel produced under $wheelhouse" }
Write-Output "zstandard-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "zstandard-cp312: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck 'import io, zstandard as zstd; assert zstd.backend == ''cext'', ''C backend not used: '' + zstd.backend; assert zstd.ZSTD_VERSION == (1, 5, 7), zstd.ZSTD_VERSION; assert {''multi_compress_to_buffer'', ''multi_decompress_to_buffer''} <= zstd.backend_features, zstd.backend_features; data = b''cvcpkg zstandard '' * 4096; blob = zstd.ZstdCompressor(level=19).compress(data); assert len(blob) < len(data) // 50, len(blob); assert zstd.ZstdDecompressor().decompress(blob) == data; assert zstd.ZstdDecompressor().stream_reader(io.BytesIO(blob)).read() == data; mt = zstd.ZstdCompressor(level=3, threads=2).compress(data); assert zstd.ZstdDecompressor().decompress(mt) == data'
