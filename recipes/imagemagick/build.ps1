# recipes/imagemagick/build.ps1 — ImageMagick on Windows without vcpkg.
#
# ImageMagick's Windows source build is intricate (its own VisualMagick
# configure wizard, ~20 optional delegate libraries, Q-depth and HDRI
# matrices).  For CI usage we repackage the official upstream Inno Setup
# installer directly (no vcpkg) using a pinned innoextract.exe.
#
# Any *libraries* ImageMagick would depend on are declared as cvcpkg
# recipes in recipe.yaml (zlib, tiff); this script does not pull anything
# from vcpkg.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$IM_VERSION            = '7.1.2-22'
$IM_INSTALLER_URL      = "https://github.com/ImageMagick/ImageMagick/releases/download/$IM_VERSION/ImageMagick-$IM_VERSION-Q16-HDRI-x64-dll.exe"
$IM_INSTALLER_SHA256   = 'd55b0868a8fc1ce3e7c9d1ff755f910bb01fc9306bdf856c252c2b77559dd660'
$INNOEXTRACT_URL       = 'https://github.com/dscharrer/innoextract/releases/download/1.9/innoextract-1.9-windows.zip'
$INNOEXTRACT_SHA256    = '6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065'

function Get-CvcRemoteFile {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$OutFile,
        [string]$Sha256 = ''
    )
    if (Test-Path $OutFile) {
        if ($Sha256) {
            $actual = (Get-FileHash -Algorithm SHA256 $OutFile).Hash.ToLower()
            if ($actual -eq $Sha256.ToLower()) {
                Write-Host "cvcpkg: cached $OutFile (sha256 ok)"
                return
            }
            Write-Host "cvcpkg: cached $OutFile sha256 mismatch, redownloading"
            Remove-Item -Force $OutFile
        } else {
            Write-Host "cvcpkg: cached $OutFile"
            return
        }
    }
    $dir = Split-Path -Parent $OutFile
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Write-Host "cvcpkg: downloading $Url"
    # Invoke-WebRequest is slow with its progress bar; use .NET WebClient.
    $wc = New-Object System.Net.WebClient
    try {
        $wc.DownloadFile($Url, $OutFile)
    } finally {
        $wc.Dispose()
    }
    if ($Sha256) {
        $actual = (Get-FileHash -Algorithm SHA256 $OutFile).Hash.ToLower()
        if ($actual -ne $Sha256.ToLower()) {
            throw "sha256 mismatch for $OutFile: expected $Sha256, got $actual"
        }
    }
}

$downloadDir = Join-Path $env:CVC_BUILD_DIR 'downloads'
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

$installer     = Join-Path $downloadDir "ImageMagick-$IM_VERSION-Q16-HDRI-x64-dll.exe"
$innoextractZip = Join-Path $downloadDir 'innoextract-1.9-windows.zip'
Get-CvcRemoteFile -Url $IM_INSTALLER_URL -OutFile $installer      -Sha256 $IM_INSTALLER_SHA256
Get-CvcRemoteFile -Url $INNOEXTRACT_URL  -OutFile $innoextractZip -Sha256 $INNOEXTRACT_SHA256

$stageDir = Join-Path $env:CVC_BUILD_DIR 'stage'
if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

# Extract innoextract.
Write-Host "cvcpkg: unzipping innoextract"
Expand-Archive -Path $innoextractZip -DestinationPath $stageDir -Force
$innoextractExe = Join-Path $stageDir 'innoextract.exe'
if (-not (Test-Path $innoextractExe)) {
    throw "innoextract.exe missing after unzip: $innoextractExe"
}

# Run innoextract on the IM installer. --silent extracts the {app}
# subtree into $stageDir/app/.
Write-Host "cvcpkg: running innoextract on $installer"
& $innoextractExe --silent --extract --output-dir $stageDir $installer
if ($LASTEXITCODE -ne 0) { throw "innoextract failed with code $LASTEXITCODE" }

$appDir = Join-Path $stageDir 'app'
if (-not (Test-Path (Join-Path $appDir 'include'))) {
    throw "innoextract output missing headers: $appDir\include"
}

# Stage into $CVC_INSTALL_DIR using the same layout as the previous
# vcpkg overlay port (include/, lib/, bin/), so consumers using the
# stock FindImageMagick.cmake module keep working unchanged.
$installInclude = Join-Path $env:CVC_INSTALL_DIR 'include'
$installLib     = Join-Path $env:CVC_INSTALL_DIR 'lib'
$installBin     = Join-Path $env:CVC_INSTALL_DIR 'bin'
$installShare   = Join-Path $env:CVC_INSTALL_DIR 'share\imagemagick'
foreach ($d in @($installInclude, $installLib, $installBin, $installShare)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# Headers: everything under app/include/ (Magick++/, MagickCore/, MagickWand/).
Copy-Item -Recurse -Force (Join-Path $appDir 'include\*') $installInclude

# Import libraries.
$imLibs = Get-ChildItem -Path (Join-Path $appDir 'lib') -Filter 'CORE_RL_*.lib' -ErrorAction SilentlyContinue
if (-not $imLibs -or $imLibs.Count -eq 0) {
    throw "No CORE_RL_*.lib files found under $appDir\lib"
}
foreach ($lib in $imLibs) {
    Copy-Item -Force $lib.FullName $installLib
}

# Canonical-named aliases so older FindImageMagick.cmake variants
# (which look for Magick++.lib / MagickCore.lib / MagickWand.lib
# without the CORE_RL_ prefix) also find them.
foreach ($component in @('Magick++','MagickCore','MagickWand')) {
    $src = Join-Path $installLib "CORE_RL_${component}_.lib"
    $dst = Join-Path $installLib "${component}.lib"
    if ((Test-Path $src) -and (-not (Test-Path $dst))) {
        Copy-Item -Force $src $dst
    }
}

# Runtime DLLs (CORE_RL_*, IM_MOD_RL_*, FILTER_*) shipped alongside the
# installer's magick.exe.  Ship them all so apps can resolve symbols.
foreach ($pat in @('CORE_RL_*.dll','IM_MOD_RL_*.dll','FILTER_*.dll')) {
    $matches = Get-ChildItem -Path $appDir -Filter $pat -ErrorAction SilentlyContinue
    foreach ($f in $matches) {
        Copy-Item -Force $f.FullName $installBin
    }
}

# Executables (magick.exe, convert.exe, etc.).
foreach ($exe in Get-ChildItem -Path $appDir -Filter '*.exe' -ErrorAction SilentlyContinue) {
    Copy-Item -Force $exe.FullName $installBin
}

# Configuration files (colors.xml, delegates.xml, etc.) — needed at
# runtime for many operations.
foreach ($xml in Get-ChildItem -Path $appDir -Filter '*.xml' -ErrorAction SilentlyContinue) {
    Copy-Item -Force $xml.FullName $installShare
}

# License.
foreach ($licName in @('License.txt','LICENSE.txt','NOTICE.txt')) {
    $licSrc = Join-Path $appDir $licName
    if (Test-Path $licSrc) {
        Copy-Item -Force $licSrc (Join-Path $installShare $licName)
    }
}

Write-Host "cvcpkg: imagemagick staged to $env:CVC_INSTALL_DIR"
