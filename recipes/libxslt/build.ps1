# recipes/libxslt/build.ps1 — build libxslt (+libexslt) on Windows with CMake,
# linking cvcpkg's own libxml2 (see build.sh for the full why).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# Minimal feature set (see build.sh): shared libs; no xsltproc, tests or Python
# bindings; crypto and the runtime module loader off. libxml2 is found through
# its installed CMake config package (find_package(LibXml2 CONFIG REQUIRED));
# Invoke-CvcCMakeBuild puts the deps prefix on CMAKE_PREFIX_PATH.
Invoke-CvcCMakeBuild @(
    '-DBUILD_SHARED_LIBS=ON',
    '-DLIBXSLT_WITH_PYTHON=OFF',
    '-DLIBXSLT_WITH_PROGRAMS=OFF',
    '-DLIBXSLT_WITH_TESTS=OFF',
    '-DLIBXSLT_WITH_CRYPTO=OFF',
    '-DLIBXSLT_WITH_MODULES=OFF'
)

# cvcpkg ships the WHOLE install tree (package.files only DECLARES the payload),
# so trim what this recipe does not ship: libxslt's CMake install always writes
# the HTML docs (share\doc), man pages (share\man), the xslt-config helper and
# the legacy xsltConf.sh shim.  Consumers use pkg-config / the CMake config
# package, never these.  bin\ is KEPT on Windows — the shared libxslt/libexslt
# DLLs live there.
Remove-Item -Recurse -Force (Join-Path $env:CVC_INSTALL_DIR 'share') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $env:CVC_INSTALL_DIR 'bin\xslt-config') -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $env:CVC_INSTALL_DIR 'lib\xsltConf.sh') -ErrorAction SilentlyContinue
