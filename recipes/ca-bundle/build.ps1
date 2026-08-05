#!/usr/bin/env pwsh
# recipes/ca-bundle/build.ps1 — stage the Mozilla CA bundle into the prefix.
$ErrorActionPreference = 'Stop'

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$CERT_URL = "https://curl.se/ca/cacert-2026-05-14.pem"
$CERT_SHA256 = "86a1f3366afac7c6f8ae9f3c779ac221129328c43f0ab2b8817eb2f362a5025c"

$SslDir   = Join-Path $env:CVC_INSTALL_DIR "etc/ssl"
$CertDir  = Join-Path $SslDir "certs"
$ShareDir = Join-Path $env:CVC_INSTALL_DIR "share/ca-bundle"
$CertPath = Join-Path $SslDir "cert.pem"

New-Item -ItemType Directory -Force -Path $CertDir, $ShareDir | Out-Null

# Download the CA bundle
Invoke-WebRequest -Uri $CERT_URL -OutFile $CertPath -UseBasicParsing

# Verify SHA-256
$ACTUAL_SHA256 = (Get-FileHash -Path $CertPath -Algorithm SHA256).Hash.ToLower()
if ($ACTUAL_SHA256 -ne $CERT_SHA256) {
    Write-Error "SHA256 mismatch: expected $CERT_SHA256, got $ACTUAL_SHA256"
    exit 1
}

# One symlink per certificate in the bundle, populating etc/ssl/certs/.
# Targets are RELATIVE on purpose: cvcpkg's extraction filter rejects
# absolute link targets and aborts the pack (see recipes/bzip2/build.sh).
#
# NOTE: numbered rather than subject-hash named, so OpenSSL's SSL_CERT_DIR
# lookup does not actually consult them -- SSL_CERT_FILE is what makes
# verification work.  Behaviour preserved from the original script.
$n = 0
foreach ($line in (Get-Content $CertPath)) {
    if ($line -match '^-----BEGIN CERTIFICATE-----') {
        $n++
        $link = Join-Path $CertDir "$n.pem"
        if (Test-Path $link) { Remove-Item $link -Force }
        New-Item -ItemType SymbolicLink -Path $link -Target "../cert.pem" | Out-Null
    }
}

# Convenience env hook.  Single-quoted here-string: nothing is expanded at
# build time.  The hook derives the prefix from its OWN path
# (<prefix>/share/ca-bundle/env.sh) rather than baking in CVC_INSTALL_DIR,
# because the package is unpacked at whatever prefix the consumer chooses.
@'
# ca-bundle environment hook -- source this to point OpenSSL at the bundled certs
_ca_bundle_self="${BASH_SOURCE[0]:-$0}"
_ca_bundle_prefix="$(cd "$(dirname "${_ca_bundle_self}")/../.." && pwd)"
export SSL_CERT_FILE="${_ca_bundle_prefix}/etc/ssl/cert.pem"
export SSL_CERT_DIR="${_ca_bundle_prefix}/etc/ssl/certs"
unset _ca_bundle_self _ca_bundle_prefix
'@ | Out-File -FilePath (Join-Path $ShareDir "env.sh") -Encoding ascii

Write-Host "ca-bundle: staged $n certificates to $SslDir"
