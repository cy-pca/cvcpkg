# recipes/dav1d-tools/build.ps1 — build dav1d decoder tool on Windows via Meson + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild -MesonArgs @(
    '-Denable_tests=false',
    '-Denable_tools=true',
    '-Denable_examples=false',
    '-Denable_docs=false'
    # No -Ddefault_library: Invoke-CvcMesonBuild passes --default-library from
    # CVC_LINK, and meson refuses both ("Got argument default_library as both
    # -Ddefault_library= and --default-library"; windows populate 2026-10-19).
)

# Only keep the tool binary; the library comes from the dav1d recipe.
$bin = "$env:CVC_INSTALL_DIR\bin"
Get-ChildItem $env:CVC_INSTALL_DIR -Recurse -Filter '*.dll' |
    Where-Object { $_.Name -like 'dav1d*' } |
    ForEach-Object { Remove-Item $_.FullName -Force }
Get-ChildItem $env:CVC_INSTALL_DIR -Recurse -Filter '*.lib' |
    ForEach-Object { Remove-Item $_.FullName -Force }
if (Test-Path "$env:CVC_INSTALL_DIR\include") {
    Remove-Item "$env:CVC_INSTALL_DIR\include" -Recurse -Force
}
if (Test-Path "$env:CVC_INSTALL_DIR\lib") {
    Remove-Item "$env:CVC_INSTALL_DIR\lib" -Recurse -Force
}
