# recipes/gtk4/build.ps1 — build GTK 4 on Windows via gvsbuild.
#
# gvsbuild (https://github.com/wingtk/gvsbuild) is a Python-based
# meta-builder that fetches and builds the entire GTK 4 stack under
# MSVC.  We install it with pipx and let it stage into the cvcpkg
# install prefix.
$ErrorActionPreference = 'Stop'

# Ensure pipx is available.
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if (-not $pipx) {
    Write-Host "Installing pipx..."
    python -m pip install --user pipx
    python -m pipx ensurepath
}

# Install (or update) gvsbuild.
Write-Host "Installing gvsbuild..."
pipx install --force gvsbuild

# Build GTK4 and its dependency stack.  gvsbuild manages sources and
# their build outputs under its own tree and copies release artifacts
# to a build directory we control.
$build = Join-Path $env:CVC_BUILD_DIR 'gvsbuild'
if (-not (Test-Path $build)) { New-Item -ItemType Directory -Path $build | Out-Null }

Write-Host "Building GTK 4 stack (this can take 1-2 hours) ..."
gvsbuild build gtk4 `
    --build-dir $build `
    --configuration release `
    --platform x64

# gvsbuild places the resulting GTK 4 install tree under
# <build>/gtk/x64/release.
$gvs = Join-Path $build 'gtk\x64\release'
if (-not (Test-Path $gvs)) {
    throw "gvsbuild did not produce expected output at $gvs"
}

Write-Host "Staging into $env:CVC_INSTALL_DIR ..."
Copy-Item -Path (Join-Path $gvs '*') -Destination $env:CVC_INSTALL_DIR -Recurse -Force

Write-Host "gtk4 (via gvsbuild) installed to $env:CVC_INSTALL_DIR"
