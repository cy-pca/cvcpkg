# recipes/curl/build.ps1 — build libcurl on Windows via CMake + MSVC.
#
# Uses OpenSSL as the TLS backend (matches Linux/macOS/BSD path) via
# CVC_DEPS_PREFIX.  Optional compression backends (zlib, zstd, brotli)
# and secondary transports (SSH2, HTTP/2, IDN2, PSL) are disabled to
# match the minimal feature set of build.sh — libcurl-with-HTTPS is
# enough to satisfy cmake's file(DOWNLOAD) and Qt6/network downstream.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$extra = @(
    '-DCURL_USE_OPENSSL=ON',
    '-DCURL_USE_LIBPSL=OFF',
    '-DCURL_USE_LIBSSH2=OFF',
    '-DCURL_BROTLI=OFF',
    '-DCURL_ZSTD=OFF',
    '-DCURL_ZLIB=OFF',
    '-DUSE_NGHTTP2=OFF',
    '-DUSE_LIBIDN2=OFF',
    '-DCURL_DISABLE_LDAP=ON',
    '-DCURL_DISABLE_LDAPS=ON',
    '-DCURL_DISABLE_DICT=ON',
    '-DCURL_DISABLE_GOPHER=ON',
    '-DCURL_DISABLE_IMAP=ON',
    '-DCURL_DISABLE_MQTT=ON',
    '-DCURL_DISABLE_POP3=ON',
    '-DCURL_DISABLE_RTSP=ON',
    '-DCURL_DISABLE_SMB=ON',
    '-DCURL_DISABLE_SMTP=ON',
    '-DCURL_DISABLE_TELNET=ON',
    '-DCURL_DISABLE_TFTP=ON',
    '-DBUILD_TESTING=OFF',
    '-DENABLE_MANUAL=OFF',
    '-DBUILD_CURL_EXE=ON'
)

# Steer curl's CMake at the OpenSSL we built as a runtime dep.
if ($env:CVC_DEPS_PREFIX -and (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include\openssl'))) {
    $extra += "-DOPENSSL_ROOT_DIR=$env:CVC_DEPS_PREFIX"
}

Invoke-CvcCMakeBuild $extra
