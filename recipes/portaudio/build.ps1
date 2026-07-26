# recipes/portaudio/build.ps1 — build PortAudio on Windows via CMake + MSVC.
#
# Windows host APIs: WMME, DirectSound, WASAPI, WDM/KS (all ON by default).
# ASIO is disabled because it needs the proprietary Steinberg ASIO SDK.
# PortAudio uses its own PA_BUILD_STATIC / PA_BUILD_SHARED switch (both ON
# by default) rather than BUILD_SHARED_LIBS, so translate CVC_LINK here.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

if ($env:CVC_LINK -eq 'static') {
    $paStatic = 'ON';  $paShared = 'OFF'
} else {
    $paStatic = 'OFF'; $paShared = 'ON'
}

Invoke-CvcCMakeBuild @(
    "-DPA_BUILD_STATIC=$paStatic",
    "-DPA_BUILD_SHARED=$paShared",
    '-DPA_LIBNAME_ADD_SUFFIX=OFF',
    '-DPA_USE_ASIO=OFF',
    '-DPA_BUILD_TESTS=OFF',
    '-DPA_BUILD_EXAMPLES=OFF'
)
