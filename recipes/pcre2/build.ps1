# recipes/pcre2/build.ps1 — build PCRE2 from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# pcre2grep's .gz/.bz2 support stays off, as in build.sh: find_package() would
# otherwise link whatever zlib/bzip2 the deps prefix happens to hold, and pcre2
# declares neither.
Invoke-CvcCMakeBuild @(
    '-DPCRE2_BUILD_PCRE2_8=ON',
    '-DPCRE2_BUILD_PCRE2_16=ON',
    '-DPCRE2_BUILD_PCRE2_32=OFF',
    '-DPCRE2_SUPPORT_UNICODE=ON',
    '-DPCRE2_BUILD_PCRE2GREP=ON',
    '-DPCRE2_SUPPORT_LIBZ=OFF',
    '-DPCRE2_SUPPORT_LIBBZ2=OFF',
    '-DPCRE2_BUILD_TESTS=OFF',
    "-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig"
)
