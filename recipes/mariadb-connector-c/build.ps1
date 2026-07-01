# recipes/mariadb-connector-c/build.ps1 — build MariaDB Connector/C on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DWITH_SSL=OPENSSL',
    "-DOPENSSL_ROOT_DIR=$env:CVC_DEPS_PREFIX",
    '-DWITH_EXTERNAL_ZLIB=ON',
    '-DWITH_UNIT_TESTS=OFF',
    '-DCLIENT_PLUGIN_AUTH_GSSAPI_CLIENT=OFF',
    # Disable the MariaDB Enterprise parsec plugin: its Windows build
    # references EVP_MD_CTX_new / EVP_DigestSign / PKCS5_PBKDF2_HMAC etc.
    # without linking libcrypto, giving LNK2019 for every OpenSSL symbol.
    # It is an Enterprise-only auth plugin not used by typical clients.
    '-DCLIENT_PLUGIN_PARSEC=OFF',
    '-DINSTALL_LIBDIR=lib',
    '-DINSTALL_INCLUDEDIR=include/mariadb',
    '-DINSTALL_PLUGINDIR=lib/mariadb/plugin'
)
