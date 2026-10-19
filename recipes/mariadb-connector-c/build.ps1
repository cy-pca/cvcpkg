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
    # remote_io needs libcurl, which is not a dependency of this recipe: it was
    # built only when cmake's curl happened to be in the deps prefix, and MSVC
    # then failed it on C4267 under /WX (windows populate 2026-10-19).
    '-DCLIENT_PLUGIN_REMOTE_IO=OFF',
    '-DINSTALL_LIBDIR=lib',
    '-DINSTALL_INCLUDEDIR=include/mariadb',
    '-DINSTALL_PLUGINDIR=lib/mariadb/plugin'
)
