# recipes/cvcpkg/build.ps1 — freeze the cvcpkg CLI into one self-contained bin\cvcpkg.exe.
#
# The Windows twin of build.sh: the same PyInstaller build over
# packaging/cvcpkg.spec as .github/workflows/cvcpkg-standalone.yml, but from
# cvcpkg's own recipe graph instead of pip and PyPI, with pip kept off the
# network (--no-index).  The result is the package /install.ps1 installs.
#
# Windows differences from build.sh:
#   - the python312 bundle puts python.exe at the prefix ROOT (no bin\, no
#     version suffix), with packages under Lib\site-packages;
#   - PyInstaller's Windows bootloaders ship prebuilt in its sdist, so nothing
#     here needs a C compiler;
#   - the verify step runs the binary with PATH cut down to System32, because
#     Windows also resolves DLLs from PATH: a binary missing a DLL would
#     otherwise pass here by finding the prefix's copy.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$pyVer = '3.12'
$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }

# Every dependency is a build edge, so it is staged into the build prefix; a
# remote builder installs the whole closure into one shared prefix instead.
# Look in both, and confirm the version rather than trusting a bare python.exe.
$py = $null
foreach ($p in @($bld, $deps)) {
    $cand = Join-Path $p 'python.exe'
    if (Test-Path $cand) {
        $got = (& $cand -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null)
        if ($LASTEXITCODE -eq 0 -and "$got".Trim() -eq $pyVer) { $py = $cand; break }
    }
}
if (-not $py) { throw "cvcpkg: python $pyVer not found under $bld or $deps" }
$site = @($bld, $deps) | Select-Object -Unique | ForEach-Object { Join-Path $_ 'Lib\site-packages' }

# PyInstaller freezes whatever the interpreter can import, and a user site
# (%APPDATA%\Python\Python312\site-packages) would leak the builder account's
# own packages into the binary.  Freeze from the prefix only.
$env:PYTHONNOUSERSITE = '1'
Write-Host "cvcpkg: freezing cvcpkg $($env:CVC_VERSION) with $py"

# ── 1. Install cvcpkg into a scratch prefix ─────────────────────────────────
# The spec copies cvcpkg's .dist-info into the binary (copy_metadata), and that
# is where `cvcpkg --version` reads its version, so cvcpkg has to be installed,
# not just importable from src\.  The in-tree backend (pep517-backend\, over
# poetry-core-cp312) builds it.
$stage = Join-Path $env:CVC_BUILD_DIR 'stage'
$env:PYTHONPATH = ($site -join ';')
& $py -m pip install --no-index --no-deps --no-build-isolation --no-compile `
    --prefix $stage $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "cvcpkg: pip install of the source failed ($LASTEXITCODE)" }
$env:PYTHONPATH = ((@(Join-Path $stage 'Lib\site-packages') + $site) -join ';')

# ── 2. Freeze ────────────────────────────────────────────────────────────────
$dist = Join-Path $env:CVC_BUILD_DIR 'dist'
& $py -m PyInstaller --clean --noconfirm `
    --distpath $dist `
    --workpath (Join-Path $env:CVC_BUILD_DIR 'pyinstaller') `
    (Join-Path $env:CVC_SOURCE_DIR 'packaging\cvcpkg.spec')
if ($LASTEXITCODE -ne 0) { throw "cvcpkg: PyInstaller failed ($LASTEXITCODE)" }
$bin = Join-Path $dist 'cvcpkg.exe'
if (-not (Test-Path $bin)) { throw "cvcpkg: PyInstaller produced no $bin" }

# ── 3. Verify what will actually ship ───────────────────────────────────────
# Run the binary with the build environment stripped: PYTHONPATH, and the
# prefix directories on PATH (Windows' DLL search order includes PATH), would
# let a binary that is missing a module or a DLL pass here and fail on a user's
# machine.
function Invoke-Bare {
    param([string]$WorkDir, [string[]]$BinArgs)
    $saved = @{ PATH = $env:PATH; PYTHONPATH = $env:PYTHONPATH; PYTHONHOME = $env:PYTHONHOME }
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Push-Location $WorkDir
    try {
        $out = & $bin @BinArgs
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
        foreach ($k in $saved.Keys) {
            if ($null -ne $saved[$k]) { Set-Item "Env:$k" $saved[$k] }
        }
    }
    if ($rc -ne 0) { throw "cvcpkg: '$bin $($BinArgs -join ' ')' failed ($rc): $out" }
    return $out
}

# The version comes from the bundled .dist-info, so this also proves the
# metadata made it in, and that the tarball is the release the recipe claims.
$versionOut = ("$(Invoke-Bare $env:CVC_BUILD_DIR @('--version'))").Trim()
Write-Host "cvcpkg: $versionOut"
if (-not $versionOut.EndsWith(" $($env:CVC_VERSION)")) {
    throw "cvcpkg: expected version $($env:CVC_VERSION), the binary says '$versionOut'"
}

# Validate a bundled recipe from a directory with no .\recipes, so the recipes,
# the schemas (and jsonschema) all have to come out of the executable itself.
$empty = Join-Path $env:CVC_BUILD_DIR 'empty'
New-Item -ItemType Directory -Force -Path $empty | Out-Null
Invoke-Bare $empty @('validate', 'recipes/zlib') | Write-Host

# The extras are imported lazily (cvcpkg.optional) and degrade quietly when
# missing, so --version and validate would pass without them.  Check the
# archive itself.
$listing = Join-Path $env:CVC_BUILD_DIR 'archive.txt'
& $py -m PyInstaller.utils.cliutils.archive_viewer -r -b $bin | Set-Content -Encoding utf8 $listing
if ($LASTEXITCODE -ne 0) { throw "cvcpkg: archive_viewer failed ($LASTEXITCODE)" }
$names = Get-Content $listing | ForEach-Object { $_.Trim() }
foreach ($mod in 'websockets.sync.client', 'httpx', 'cryptography.hazmat.primitives.asymmetric.ed25519',
    'jsonschema', 'yaml', 'click', 'colorama') {
    if ($names -notcontains $mod) { throw "cvcpkg: $mod is not bundled in $bin" }
}

# ── 4. Stage ─────────────────────────────────────────────────────────────────
$outBin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $outBin | Out-Null
Copy-Item -Force $bin (Join-Path $outBin 'cvcpkg.exe')
Write-Host "cvcpkg: staged $(Join-Path $outBin 'cvcpkg.exe')"
