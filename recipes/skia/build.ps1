# recipes/skia/build.ps1 — build Skia on Windows with GN + ninja.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$skiaTag = 'chrome/m137'
$src = Join-Path $env:CVC_SOURCE_DIR 'skia'
if (-not (Test-Path (Join-Path $src '.git'))) {
    git clone --depth=1 --branch $skiaTag https://skia.googlesource.com/skia.git $src
}
Push-Location $src
try {
    python tools\git-sync-deps

    $isComponent = if ($env:CVC_LINK -eq 'static') { 'false' } else { 'true' }
    $gnArgs = @(
        'is_official_build=true',
        "is_component_build=$isComponent",
        'skia_use_system_expat=false',
        'skia_use_system_freetype2=false',
        'skia_use_system_harfbuzz=false',
        'skia_use_system_icu=false',
        'skia_use_system_libjpeg_turbo=false',
        'skia_use_system_libpng=false',
        'skia_use_system_libwebp=false',
        'skia_use_system_zlib=false',
        'skia_enable_tools=false',
        'skia_enable_gpu=true',
        'target_os="win"'
    ) -join ' '

    $out = Join-Path $env:CVC_BUILD_DIR 'out'
    & (Join-Path $src 'bin\gn.exe') gen $out --args="$gnArgs"
    if ($LASTEXITCODE -ne 0) { throw "gn gen failed" }

    ninja -C $out -j $env:CVC_JOBS skia skshaper skparagraph skunicode
    if ($LASTEXITCODE -ne 0) { throw "ninja failed" }

    # Stage.
    $libDst = Join-Path $env:CVC_INSTALL_DIR 'lib'
    $incDst = Join-Path $env:CVC_INSTALL_DIR 'include\skia'
    New-Item -ItemType Directory -Force -Path $libDst,$incDst | Out-Null

    Get-ChildItem -Path $out -Filter 'sk*.lib' | Copy-Item -Destination $libDst
    Get-ChildItem -Path $out -Filter 'sk*.dll' | Copy-Item -Destination $libDst -ErrorAction SilentlyContinue
    Copy-Item -Recurse -Path (Join-Path $src 'include\*') -Destination $incDst -Force
    foreach ($m in 'skshaper','skparagraph','skunicode') {
        $mi = Join-Path $src ("modules\$m\include")
        if (Test-Path $mi) { Copy-Item -Recurse -Path (Join-Path $mi '*') -Destination $incDst -Force }
    }

    # CMake package config.
    $cmake = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake\Skia'
    New-Item -ItemType Directory -Force -Path $cmake | Out-Null
    @'
get_filename_component(_skia_root "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
foreach(_lib skia skshaper skparagraph skunicode)
    if(NOT TARGET Skia::${_lib})
        add_library(Skia::${_lib} UNKNOWN IMPORTED)
        find_library(_skia_${_lib}_path NAMES ${_lib} HINTS "${_skia_root}/lib" NO_DEFAULT_PATH)
        set_target_properties(Skia::${_lib} PROPERTIES
            IMPORTED_LOCATION             "${_skia_${_lib}_path}"
            INTERFACE_INCLUDE_DIRECTORIES "${_skia_root}/include/skia"
            INTERFACE_COMPILE_FEATURES    "cxx_std_17")
    endif()
endforeach()
'@ | Set-Content -Path (Join-Path $cmake 'SkiaConfig.cmake') -Encoding ASCII

    Write-Host "skia $skiaTag installed to $env:CVC_INSTALL_DIR"
} finally {
    Pop-Location
}
