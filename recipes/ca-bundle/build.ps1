#!/usr/bin/env pwsh
$ErrorActionPreference = 'Stop'

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/build-vars.ps1")

$CERT_URL = "https://curl.se/ca/cacert-2026-05-14.pem"
$CERT_SHA256 = "86a1f3366afac7c6f8ae9f3c779ac221129328c43f0ab2b8817eb2f362a5025c"

$CertDir  = Join-Path $PREFIX "etc/ssl/certs"
$ShareDir = Join-Path $PREFIX "share/ca-bundle"
$CertPath = Join-Path $PREFIX "etc/ssl/cert.pem"

New-Item -ItemType Directory -Force -Path $CertDir, $ShareDir | Out-Null

# Download the CA bundle
Invoke-WebRequest -Uri $CERT_URL -OutFile $CertPath

# Verify SHA-256
$ACTUAL_SHA256 = (Get-FileHash -Path $CertPath -Algorithm SHA256).Hash.ToLower()
if ($ACTUAL_SHA256 -ne $CERT_SHA256) {
    Write-Error "SHA256 mismatch: expected $CERT_SHA256, got $ACTUAL_SHA256"
    exit 1
}

# Create individual cert symlinks for each CA entry
$n = 0
$inCert = $false
foreach ($line in (Get-Content $CertPath)) {
    if ($line -match '-----BEGIN CERTIFICATE-----') {
        $n++
        $inCert = $true
    }
    if ($inCert -and ($line -match '-----END CERTIFICATE-----')) {
        $link = Join-Path $CertDir "$n.pem"
        if (Test-Path $link) { Remove-Item $link }
        New-Item -ItemType SymbolicLink -Path $link -Target $CertPath | Out-Null
        $inCert = $false
    }
}

# Create convenience env hook
@'
# ca-bundle environment hook -- source this to point OpenSSL at the bundled certs
export SSL_CERT_FILE="{PREFIX}/etc/ssl/cert.pem"
export SSL_CERT_DIR="{PREFIX}/etc/ssl/certs"
'@ -replace '\{PREFIX\}', $PREFIX | Out-File -FilePath (Join-Path $ShareDir "env.sh") -Encoding ascii
