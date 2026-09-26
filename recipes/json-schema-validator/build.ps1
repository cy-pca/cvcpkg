# recipes/json-schema-validator/build.ps1 — build json-schema-validator on Windows.
# Plain CMake C++ library over nlohmann/json; the same switches as build.sh.
# nlohmann-json is a build dep so find_package(nlohmann_json) resolves in-prefix
# (no FetchContent). Shared to match the other shared windows bundles.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DJSON_VALIDATOR_BUILD_TESTS=OFF',
    '-DJSON_VALIDATOR_BUILD_EXAMPLES=OFF',
    '-DJSON_VALIDATOR_INSTALL=ON',
    '-DJSON_VALIDATOR_SHARED_LIBS=ON'
)
