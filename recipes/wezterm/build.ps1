# recipes/wezterm/build.ps1 — download + stage the official WezTerm binaries on Windows.
#
# Upstream's portable WezTerm-windows-<tag>.zip (not the setup.exe) is one flat
# directory, and it is staged into bin\ as it is: wezterm.exe starts
# wezterm-gui.exe from its own directory, and wezterm-gui.exe loads conpty.dll +
# OpenConsole.exe, ANGLE's libEGL.dll / libGLESv2.dll and mesa\opengl32.dll from
# there too. Everything links the CRT statically, so the tree needs nothing
# beyond Windows itself.
#
# One change: conpty.dll + OpenConsole.exe are replaced by Microsoft's ConPTY
# 1.25 (the Microsoft.Windows.Console.ConPTY package on nuget.org). The ConPTY
# the 20240203 zip bundles (February 2024) drops APC and DCS sequences that a
# program writes, so neither kitty graphics nor Sixel from a Windows program
# ever reached WezTerm; WezTerm's own nightlies bundle a 2025 ConPTY for the
# same reason. See the recipe notes.
#
# Pure download, no compiler -- like the other prebuilt recipes it does not
# source env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep $tag/$expected and $conptyVer/$conptySha in step with build.sh and with
# recipe.yaml's source.artifacts.
$tag = '20240203-110809-5046fc22'
$stem = "WezTerm-windows-$tag"
$expected = '57e5d03b585303d81e8b8e96d1230362852eb39aca92b3b29c7a42cfb82f9ac4'
$conptyVer = '1.25.260930003'
$conptySha = '02b07b349af66d801159bdf9e440d4a1ce78bb951f37fc8609731665afdae7ee'

function Get-Pinned([string]$url, [string]$out, [string]$sha) {
    Write-Host "Downloading $url ..."
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
    $actual = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha) { throw "sha256 mismatch for ${url}: got $actual expected $sha" }
}

$zip = Join-Path $env:CVC_BUILD_DIR "$stem.zip"
Get-Pinned "https://github.com/wez/wezterm/releases/download/$tag/$stem.zip" $zip $expected
Expand-Archive -Path $zip -DestinationPath $env:CVC_BUILD_DIR -Force
$src = Join-Path $env:CVC_BUILD_DIR $stem

# A .nupkg is a zip; Expand-Archive only takes the .zip extension.
$nupkg = Join-Path $env:CVC_BUILD_DIR "conpty.$conptyVer.zip"
Get-Pinned "https://api.nuget.org/v3-flatcontainer/microsoft.windows.console.conpty/$conptyVer/microsoft.windows.console.conpty.$conptyVer.nupkg" $nupkg $conptySha
$conpty = Join-Path $env:CVC_BUILD_DIR 'conpty'
Expand-Archive -Path $nupkg -DestinationPath $conpty -Force

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -Recurse -Force (Join-Path $src '*') $bin
# Debug symbols for wezterm.exe only (18 MB); nothing loads them.
Remove-Item -Force (Join-Path $bin 'wezterm.pdb') -ErrorAction SilentlyContinue
# The x64 pair goes flat beside wezterm-gui.exe, where the zip had its own;
# this conpty.dll starts the OpenConsole.exe next to it (checked: the pane's
# OpenConsole.exe process runs from bin\).
Copy-Item -Force (Join-Path $conpty 'runtimes\win-x64\native\conpty.dll') $bin
Copy-Item -Force (Join-Path $conpty 'build\native\runtimes\x64\OpenConsole.exe') $bin

# No release archive carries a licence; licenses\ beside this script holds
# WezTerm's own and those of what the Windows build bundles (ANGLE, the
# fonts, ConPTY), copied from the upstream repositories at the pinned tags.
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\wezterm'
New-Item -ItemType Directory -Force -Path $doc | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot 'licenses\*') $doc

& (Join-Path $bin 'wezterm.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged wezterm.exe failed to run" }
