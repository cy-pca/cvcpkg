# recipes/pegtl/build.ps1 — install taocpp/PEGTL (header-only) on Windows.
# Header-only CMake INTERFACE library, no platform-specific code: the same
# switches and checks as the POSIX build.sh.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"
. "$PSScriptRoot\check-install.ps1"

Invoke-CvcCMakeBuild @(
    '-DPEGTL_BUILD_TESTS=OFF',
    '-DPEGTL_BUILD_EXAMPLES=OFF',
    '-DPEGTL_INSTALL_DOC_DIR=share/licenses/pegtl'
)

Test-PegtlInstall
