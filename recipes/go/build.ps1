# recipes/go/build.ps1 — download + stage the official Go distribution on Windows.
#
# Same layout as build.sh: GOROOT is lib\go, go.env pins GOTOOLCHAIN=local,
# and bin\go.cmd / bin\gofmt.cmd are shims into lib\go\bin (Windows has no
# dependable symlinks for unprivileged users). Pure download, no compiler --
# like the other prebuilt recipes it does not source env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep in step with build.sh's GO_VER/sha_for() and recipe.yaml's
# source.artifacts -- three places, one release.
$ver = '1.27.2'
$file = "go$ver.windows-amd64.zip"
$expected = '1314008898bd40df77af4b014f777f08873dbdfbcd3d92308728ee03304fe04f'

$zip = Join-Path $env:CVC_BUILD_DIR $file
Write-Host "Downloading https://dl.google.com/go/$file ..."
Invoke-WebRequest -Uri "https://dl.google.com/go/$file" -OutFile $zip -UseBasicParsing

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

# The zip's single top-level directory is go\.
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
New-Item -ItemType Directory -Force -Path $lib | Out-Null
Expand-Archive -Path $zip -DestinationPath $lib -Force
Remove-Item -Force $zip
$goroot = Join-Path $lib 'go'

$envFile = Join-Path $goroot 'go.env'
$lines = Get-Content $envFile
if (-not ($lines -contains 'GOTOOLCHAIN=auto')) { throw "unexpected go.env (no GOTOOLCHAIN=auto line)" }
$header = @(
    '# cvcpkg: GOTOOLCHAIN is pinned to `local` (upstream: auto), so a go.mod asking',
    '# for a newer Go fails instead of downloading one. Export GOTOOLCHAIN=auto to',
    '# restore upstream behaviour.'
)
$body = $lines | ForEach-Object { if ($_ -eq 'GOTOOLCHAIN=auto') { 'GOTOOLCHAIN=local' } else { $_ } }
[System.IO.File]::WriteAllLines($envFile, [string[]]($header + $body))

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
foreach ($tool in 'go', 'gofmt') {
    $shim = "@`"%~dp0..\lib\go\bin\$tool.exe`" %*`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bin "$tool.cmd"), $shim)
}

& (Join-Path $goroot 'bin\go.exe') version
if ($LASTEXITCODE -ne 0) { throw "staged go.exe failed to run" }
