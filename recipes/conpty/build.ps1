# recipes/conpty/build.ps1 — stage Microsoft's ConPTY (conpty.dll + OpenConsole.exe)
# from the Microsoft.Windows.Console.ConPTY package Microsoft attaches to its
# Windows Terminal release on GitHub. Pure download, no compiler -- like the
# other prebuilt recipes it does not source env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep $ver/$terminal/$expected in step with recipe.yaml's source.artifacts.
$ver = '1.25.260930003'
$terminal = 'v1.25.2733.0'
$expected = '6d6f8b008c655d814c498d8205d4012dcd4d0b6dfc1096461d0a185d1e3caf03'
$url = "https://github.com/microsoft/terminal/releases/download/$terminal/Microsoft.Windows.Console.ConPTY.$ver.nupkg"

# A .nupkg is a zip; Expand-Archive only takes the .zip extension.
$nupkg = Join-Path $env:CVC_BUILD_DIR "conpty.$ver.zip"
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $nupkg -UseBasicParsing
$actual = (Get-FileHash $nupkg -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }
$pkg = Join-Path $env:CVC_BUILD_DIR 'conpty'
Expand-Archive -Path $nupkg -DestinationPath $pkg -Force

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -Force (Join-Path $pkg 'runtimes\win-x64\native\conpty.dll') $bin
Copy-Item -Force (Join-Path $pkg 'build\native\runtimes\x64\OpenConsole.exe') $bin

# The package carries only an SPDX id; LICENSE.txt beside this script is
# microsoft/terminal's LICENSE at v1.25.2733.0.
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\conpty'
New-Item -ItemType Directory -Force -Path $doc | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot 'LICENSE.txt') $doc
