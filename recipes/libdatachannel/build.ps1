# recipes/libdatachannel/build.ps1 -- build libdatachannel on Windows via CMake + MSVC.
#
# The same four submodules as build.sh, pinned by the same commits and sha256s
# (keep the two tables in step), fetched into the tag archive's empty deps/
# directories.  DTLS uses the OpenSSL in CVC_DEPS_PREFIX.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

function Get-CvcSubmodule {
    param([string]$Path, [string]$Repo, [string]$Commit, [string]$Sha256)
    New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null
    $archive = Join-Path $env:CVC_BUILD_DIR ("{0}-{1}.tar.gz" -f ($Repo -replace '/', '_'), $Commit)
    $url = "https://github.com/$Repo/archive/$Commit.tar.gz"
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing -MaximumRetryCount 3
    $got = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash.ToLower()
    if ($got -ne $Sha256) { throw "libdatachannel: sha256 mismatch for $Repo@${Commit}: got $got, want $Sha256" }
    $dest = Join-Path $env:CVC_SOURCE_DIR $Path
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # Windows' own bsdtar (System32), not a Git-Bash GNU tar that reads "C:" as a host name.
    & "$env:SystemRoot\System32\tar.exe" -xzf $archive -C $dest --strip-components=1
    if ($LASTEXITCODE -ne 0) { throw "libdatachannel: cannot unpack $Repo@$Commit" }
    if (-not (Test-Path (Join-Path $dest 'CMakeLists.txt'))) {
        throw "libdatachannel: $Repo@$Commit has no CMakeLists.txt in $Path"
    }
    Write-Host "libdatachannel: $Path <- $Repo@$Commit"
}

Get-CvcSubmodule 'deps/plog' 'SergiusTheBest/plog' '94899e0b926ac1b0f4750bfbd495167b4a6ae9ef' `
    '92a08bce559b5f28aa88d3fd9071567414b9f43a83fe2a05a6dd14f1da536072'
Get-CvcSubmodule 'deps/usrsctp' 'paullouisageneau/usrsctp' 'fec583d54493f879d2ae44a743423bf8a04371ab' `
    'e5c114afe73c9a0ec419fab5f5b3f63f3ce57b09b90d06ea85e63dca8aed3e7c'
Get-CvcSubmodule 'deps/libjuice' 'paullouisageneau/libjuice' 'b89c792e3612faf2f12cf35bcc56857313a06be3' `
    '713b22174e8cb2e06538fa9bac2650bda21b83c86f5128813ab4dcf8f5bea9ed'
Get-CvcSubmodule 'deps/libsrtp' 'cisco/libsrtp' '24b3bf8f19b6f5ab4cd2bcceb4f4064efca86fd5' `
    '063478e368d7cd13d04a908d152a46f90bfa728c4b324be6d413b0b92728207c'

$extra = @(
    '-DNO_EXAMPLES=ON',
    '-DNO_TESTS=ON',
    '-DUSE_GNUTLS=OFF',
    '-DUSE_MBEDTLS=OFF',
    '-DUSE_NICE=OFF'
)
# Only cvcpkg's OpenSSL: without it in the deps prefix, FindOpenSSL settles for any copy on PATH --
# Strawberry Perl's MinGW libcrypto.a links into an MSVC datachannel.dll without a word.
if (-not ($env:CVC_DEPS_PREFIX -and (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include\openssl')))) {
    throw "libdatachannel: no OpenSSL in CVC_DEPS_PREFIX ($env:CVC_DEPS_PREFIX); install the openssl package first"
}
$extra += "-DOPENSSL_ROOT_DIR=$env:CVC_DEPS_PREFIX"
$extra += '-DOPENSSL_USE_STATIC_LIBS=OFF'

Invoke-CvcCMakeBuild $extra
