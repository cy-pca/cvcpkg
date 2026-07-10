# recipes/powershell/build.ps1 — download + stage PowerShell 7 (pwsh) on Windows.
#
# Pure download/extract — no compiler needed, so this does NOT source
# env-windows.ps1 (which would require Visual Studio). pwsh.exe + its runtime
# assemblies are staged under lib\powershell\.
$ErrorActionPreference = 'Stop'

$ver = '7.4.6'
$url = "https://github.com/PowerShell/PowerShell/releases/download/v$ver/PowerShell-$ver-win-x64.zip"
$expected = 'ed49ce5adb2162cc4a835d740486be729ba904627cca71fcb6c2b95be11b993d'

$zip = Join-Path $env:CVC_BUILD_DIR 'powershell.zip'
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $zip

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

$dest = Join-Path $env:CVC_INSTALL_DIR 'lib\powershell'
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force $dest | Out-Null
Expand-Archive -Path $zip -DestinationPath $dest -Force

& (Join-Path $dest 'pwsh.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged pwsh failed to run" }
