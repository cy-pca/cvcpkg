# recipes/yaml-cpp/build.ps1 — build yaml-cpp on Windows via CMake + MSVC.
# Plain CMake C++ library, no platform-specific code; the same switches the
# POSIX build.sh uses. YAML_BUILD_SHARED_LIBS=ON matches build.sh so the
# windows bundle ships yaml-cpp.dll + the import lib, like the other shared
# windows bundles.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DYAML_CPP_BUILD_TESTS=OFF',
    '-DYAML_CPP_BUILD_TOOLS=OFF',
    '-DYAML_CPP_BUILD_CONTRIB=OFF',
    '-DYAML_BUILD_SHARED_LIBS=ON'
)
