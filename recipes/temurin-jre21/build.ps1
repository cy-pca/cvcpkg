# recipes/temurin-jre21/build.ps1 — download + stage the Eclipse Temurin 21 JRE on Windows.
#
# Same layout as build.sh: the runtime image goes to lib\jvm\temurin-jre21
# (its JAVA_HOME). Windows has no dependable symlinks for unprivileged users,
# so bin\java.cmd and bin\keytool.cmd are shims into it. Pure download, no
# compiler -- like the other prebuilt recipes it does not source
# env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep in step with build.sh's JRE_TAG/sha_for() and recipe.yaml's
# source.artifacts -- three places, one release.
$tag = 'jdk-21.0.12.1+1'
$file = 'OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip'
$expected = 'd35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636'
$base = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1'

$zip = Join-Path $env:CVC_BUILD_DIR $file
Write-Host "Downloading $base/$file ..."
Invoke-WebRequest -Uri "$base/$file" -OutFile $zip -UseBasicParsing

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

$extract = Join-Path $env:CVC_BUILD_DIR 'extract'
Expand-Archive -Path $zip -DestinationPath $extract -Force
$src = Join-Path $extract "$tag-jre"

$jh = Join-Path $env:CVC_INSTALL_DIR 'lib\jvm\temurin-jre21'
New-Item -ItemType Directory -Force -Path $jh | Out-Null
Copy-Item -Recurse -Force (Join-Path $src '*') $jh

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
foreach ($tool in 'java', 'keytool') {
    $shim = "@`"%~dp0..\lib\jvm\temurin-jre21\bin\$tool.exe`" %*`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bin "$tool.cmd"), $shim)
}

& (Join-Path $jh 'bin\java.exe') -version
if ($LASTEXITCODE -ne 0) { throw "staged java.exe failed to run" }
