# recipes/pillow-cp311/build.ps1 — build Pillow 11.1.0 FROM SOURCE against the
# prefix's zlib / libjpeg-turbo / libtiff (hand-converted; see build.sh for the
# full why).  Verified end-to-end on Windows by the from-source-python-stack
# fix: pillow links zlib1/jpeg62/tiff out of <prefix>\bin, and the interpreter
# resolves them via cvcpkg-dll-directories.pth (no RPATH on Windows).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "pillow-cp311: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

# Per-feature roots -> the runtime prefix (setup.py derives <root>\lib +
# <root>\include from each).  Deterministic discovery of OUR libraries first;
# platform guessing stays ON here — the MSVC INCLUDE/LIB plumbing the verified
# Windows build used flows through it.
if ($env:CVC_DEPS_PREFIX) {
    $env:ZLIB_ROOT = $env:CVC_DEPS_PREFIX
    $env:JPEG_ROOT = $env:CVC_DEPS_PREFIX
    $env:TIFF_ROOT = $env:CVC_DEPS_PREFIX
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
# zlib/jpeg/tiff are `enable` (= hard-require: those three are what the
# Windows build verified).  freetype/webp stay auto until a Windows build
# proves them; everything without a cvcpkg recipe is disabled so nothing
# leaks in from the builder.
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    -C zlib=enable -C jpeg=enable -C tiff=enable `
    -C raqm=disable -C lcms=disable -C jpeg2000=disable `
    -C imagequant=disable -C xcb=disable `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "pillow-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pillow-cp311: no wheel produced under $wheelhouse" }
Write-Output "pillow-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pillow-cp311: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend —
# and it must prove the CODECS, not just the import: the old Image.new()
# check passes with zero codecs compiled in.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import io
from PIL import Image, features
missing = [f for f in ("zlib", "jpg", "libtiff") if not features.check(f)]
assert not missing, f"features missing from the build: {missing}"
buf = io.BytesIO()
Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
px = Image.open(io.BytesIO(buf.getvalue())).convert("RGB").getpixel((0, 0))
assert px == (10, 20, 30), f"PNG round-trip corrupted: {px}"
s = io.BytesIO()
Image.new("RGB", (8, 8)).save(s, "JPEG")
Image.open(io.BytesIO(s.getvalue())).load()
'@
