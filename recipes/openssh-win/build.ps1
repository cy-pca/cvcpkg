# recipes/openssh-win/build.ps1 — stage Microsoft's prebuilt Win32-OpenSSH.
# The recipe `source` is the official OpenSSH-Win64.zip (already downloaded,
# sha256-verified and stripped by cvcpkg), so this just copies the binaries
# and sample config into the prefix.  No compilation — the portable OpenSSH
# source does not build with ./configure on native Windows.
$ErrorActionPreference = 'Stop'

$src = $env:CVC_SOURCE_DIR
$dst = $env:CVC_INSTALL_DIR

New-Item -ItemType Directory -Force -Path "$dst\bin"     | Out-Null
New-Item -ItemType Directory -Force -Path "$dst\etc\ssh" | Out-Null

# Executables + the bundled OpenSSL runtime (libcrypto.dll).
Copy-Item "$src\*.exe"        "$dst\bin\" -Force
Copy-Item "$src\libcrypto.dll" "$dst\bin\" -Force

# Sample sshd config + moduli (host keys are generated per-machine at deploy).
if (Test-Path "$src\sshd_config_default") {
    Copy-Item "$src\sshd_config_default" "$dst\etc\ssh\sshd_config.sample" -Force
}
if (Test-Path "$src\moduli") {
    Copy-Item "$src\moduli" "$dst\etc\ssh\moduli" -Force
}

Write-Host "Win32-OpenSSH staged to $dst"
