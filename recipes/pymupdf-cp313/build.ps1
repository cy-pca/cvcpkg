# recipes/pymupdf-cp313/build.ps1 — build PyMuPDF 1.28.2 FROM SOURCE on Windows
# for the cp313 column, against the MuPDF source fork PyMuPDF pins (approach (b);
# see recipe.yaml and build.sh for the full rationale and the SWIG fix).
#
# WINDOWS SPECIFICS — READ THIS:
#   PyMuPDF builds MuPDF on Windows by driving Visual Studio's `devenv` on
#   MuPDF's .sln (setup.py -> scripts/mupdfwrap.py --devenv ...); there is NO
#   msbuild code path.  devenv ships with the full Visual Studio IDE, NOT with
#   the standalone Build Tools.  This column is therefore built and verified by
#   the fleet's VS-equipped Windows builders; it cannot be built on a
#   Build-Tools-only host (where env-windows.ps1's Import-CvcMsvcEnv provides
#   cl.exe but no devenv).  Everything else mirrors build.sh: pinned MuPDF
#   source, the PyString_FromString Py2->Py3 fix, pinned pipcl/libclang, and the
#   get_pixmap rasterise proof.  No RPATH pass — Windows has none; PyMuPDF loads
#   the MuPDF DLLs it bundles into the package dir via os.add_dll_directory.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. "$PSScriptRoot\..\_common\env-windows.ps1"       # Import-CvcMsvcEnv (cl.exe), CMAKE_PREFIX_PATH
. "$PSScriptRoot\..\_common\python-wheel.ps1"      # Get-CvcPythonExe, Invoke-CvcPythonCheck

$py = Get-CvcPythonExe
Write-Output "pymupdf-cp313: building with $py"

# Bridge the build-only backends (packaging/setuptools/wheel) from the build
# prefix onto the interpreter's import path; --no-build-isolation cannot fetch.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

# ── 1. Fetch + verify the pinned MuPDF source ───────────────────────────────
$mupdfVer    = '1.28.2'
$mupdfSha256 = '44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156'
$mupdfUrl    = "https://mupdf.com/downloads/archive/mupdf-$mupdfVer-source.tar.gz"
$mupdfTgz    = Join-Path $env:CVC_BUILD_DIR "mupdf-$mupdfVer-source.tar.gz"
$mupdfSrc    = Join-Path $env:CVC_BUILD_DIR "mupdf-$mupdfVer-source"

Write-Output "pymupdf-cp313: downloading $mupdfUrl"
Invoke-WebRequest -Uri $mupdfUrl -OutFile $mupdfTgz -UseBasicParsing
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $mupdfTgz).Hash
if ($actual -ne $mupdfSha256.ToUpper()) {
    throw "pymupdf-cp313: MuPDF source sha256 mismatch`n  expected $mupdfSha256`n  actual   $actual"
}
if (Test-Path $mupdfSrc) { Remove-Item -Recurse -Force $mupdfSrc }
# tar (bsdtar) ships with Windows 10+; extracts into CVC_BUILD_DIR.
& tar -xzf $mupdfTgz -C $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $mupdfSrc)) { throw "pymupdf-cp313: MuPDF extract failed" }

# ── 2. Py2 -> Py3 fix in MuPDF's SWIG template (see build.sh) ───────────────
$swigPy = Join-Path $mupdfSrc 'scripts\wrap\swig.py'
if (-not (Test-Path $swigPy)) { throw "pymupdf-cp313: $swigPy not found — MuPDF layout changed?" }
$txt = Get-Content -Raw -LiteralPath $swigPy
if ($txt.Contains('PyString_FromString')) {
    ($txt -replace 'PyString_FromString', 'PyUnicode_FromString') |
        Set-Content -NoNewline -LiteralPath $swigPy
    Write-Output "pymupdf-cp313: patched PyString_FromString -> PyUnicode_FromString in swig.py"
}

# ── 3. Build-only backends: pipcl + libclang (version-pinned) ───────────────
& $py -c 'import pipcl, clang.cindex' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Output "pymupdf-cp313: installing pinned build backends (pipcl==13, libclang==18.1.1)"
    & $py -m pip install --disable-pip-version-check --no-warn-script-location `
        --prefix $env:CVC_BUILD_PREFIX 'pipcl==13' 'libclang==18.1.1'
    if ($LASTEXITCODE -ne 0) { throw "pymupdf-cp313: pip install of build backends failed" }
}

# ── 4. SWIG from the catalog (host_tool) ────────────────────────────────────
$swig = (Get-Command swig -ErrorAction SilentlyContinue).Source
if (-not $swig) { throw "pymupdf-cp313: swig not on PATH — is the 'swig' host_tool in the closure?" }
$env:PYMUPDF_SETUP_SWIG = $swig
# cvcpkg's swig bakes its build-time -swiglib path into the binary, so point
# SWIG_LIB at the copy staged in the prefix or swig fails with 'Unknown
# directive' (see build.sh for the full note).
if (-not $env:SWIG_LIB) {
    foreach ($root in @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX)) {
        if (-not $root) { continue }
        $cand = Get-ChildItem -Path (Join-Path $root 'share\swig') -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'swig.swg') } | Select-Object -First 1
        if ($cand) { $env:SWIG_LIB = $cand.FullName; break }
    }
}
if (-not ($env:SWIG_LIB -and (Test-Path (Join-Path $env:SWIG_LIB 'swig.swg')))) {
    throw "pymupdf-cp313: could not locate swig.swg under share\swig (SWIG_LIB=$($env:SWIG_LIB)); swig would fail with 'Unknown directive'."
}
Write-Output "pymupdf-cp313: swig [$swig], SWIG_LIB=$env:SWIG_LIB"

# ── 5. Build the wheel ──────────────────────────────────────────────────────
# PYMUPDF_SETUP_MUPDF_BUILD=<dir> -> use the local MuPDF tree, no download.
# PY_LIMITED_API=0 -> version-specific cp313 wheel (matches build.sh).
# MUPDF_VS_UPGRADE=1 lets PyMuPDF upgrade MuPDF's .sln for VS newer than 2019.
$env:PYMUPDF_SETUP_MUPDF_BUILD       = $mupdfSrc
$env:PYMUPDF_SETUP_MUPDF_BUILD_TYPE  = 'release'
$env:PYMUPDF_SETUP_PY_LIMITED_API    = '0'
$env:PYMUPDF_SETUP_MUPDF_VS_UPGRADE  = '1'

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "pymupdf-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'pymupdf-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pymupdf-cp313: no wheel produced under $wheelhouse" }
Write-Output "pymupdf-cp313: built $($wheel.Name)"

# ── 6. Install into this recipe's staging prefix ────────────────────────────
& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pymupdf-cp313: pip install failed ($LASTEXITCODE)" }

# ── 7. Verify: import + the rasterise path (get_pixmap dpi=90) ──────────────
$env:PYTHONPATH = ''
$testPdf = (Join-Path $env:CVC_SOURCE_DIR 'tests\resources\1.pdf') -replace '\\','/'
Invoke-CvcPythonCheck @"
import pymupdf, fitz
print('pymupdf', pymupdf.__version__, '(mupdf', pymupdf.mupdf_version + ')')
doc = pymupdf.open(r'$testPdf')
assert doc.page_count >= 1, doc.page_count
pix = doc[0].get_pixmap(dpi=90)
assert pix.width > 0 and pix.height > 0 and len(pix.samples) == pix.width * pix.height * pix.n
png = pix.tobytes('png')
assert png[:8] == b'\x89PNG\r\n\x1a\n', 'get_pixmap().tobytes(png) is not a PNG'
print('pymupdf-cp313: rasterised page0 ->', pix.width, 'x', pix.height, 'n=', pix.n, 'PNG', len(png), 'bytes')
"@
